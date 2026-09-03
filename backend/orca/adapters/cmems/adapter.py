"""CMEMS source adapter (S-07).

Serves wave and surface-current forecasts from the Copernicus Marine ARCO
store. Nothing above this layer knows that Zarr or S3 exist.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ...schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef, Uncertainty,
    haversine_km, utcnow,
)
from ...schemas import units as U
from ...schemas.data import Forecast
from ...schemas.enums import Freshness, QualityFlag, ValueKind
from ...schemas.errors import ErrorCode
from .bindings import BINDINGS, CmemsBinding
from .client import (
    ATTRIBUTION, CmemsHttp, ORGANISATION, SOURCE_ID, SOURCE_NAME, canonical_code,
)
from .store import ZarrError, ZarrStore, decode_time, nearest_index

log = logging.getLogger("orca.adapters.cmems")

ACCESS_METHOD = "CMEMS ARCO (Zarr over HTTPS)"

#: Physical plausibility ranges. A value outside these is flagged suspect and
#: excluded from assessment rather than silently used.
PLAUSIBLE = {
    "significant_wave_height": (0.0, 25.0),
    "max_wave_height": (0.0, 40.0),
    "swell_height": (0.0, 25.0),
    "peak_period": (0.5, 30.0),
    "swell_period": (0.5, 30.0),
    "mean_wave_direction": (0.0, 360.0),
    "eastward_wind": (-80.0, 80.0),
    "northward_wind": (-80.0, 80.0),
    "current_u": (-5.0, 5.0),
    "current_v": (-5.0, 5.0),
}


class CmemsError(Exception):
    def __init__(self, code: ErrorCode, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class CmemsPointResult:
    observations: list[Forecast] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    codes: list[ErrorCode] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dataset_id: str | None = None


class CmemsAdapter:
    #: How far to look for a valid ocean cell when the requested cell is masked.
    max_search_km: float = 60.0

    def __init__(self, http: CmemsHttp | None = None, max_search_km: float = 60.0):
        self.max_search_km = max_search_km
        self._http = http or CmemsHttp()
        self._owns = http is None
        self._stores: dict[str, ZarrStore] = {}
        self._times: dict[str, list[datetime]] = {}

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def __enter__(self) -> "CmemsAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- store access ----------------------------------------------------------

    def _store(self, binding: CmemsBinding) -> ZarrStore:
        url = binding.store_url
        if url not in self._stores:
            self._stores[url] = ZarrStore(url, self._http)
        return self._stores[url]

    def _time_axis(self, store: ZarrStore, binding: CmemsBinding) -> list[datetime]:
        key = binding.store_url
        if key not in self._times:
            meta = store.array("time")
            raw = store.read_coord("time")
            units = meta.units
            if not units:
                raise CmemsError(ErrorCode.ADAPTER_ERROR, "time axis publishes no units")
            self._times[key] = decode_time(raw, units)
        return self._times[key]

    def _nearest_valid(self, store, binding, meta, index, lats, lons,
                       lat: float, lon: float):
        """Nearest non-masked cell within max_search_km. Returns (value, iy, ix, km)."""
        step_km = 0.0833 * 111.32
        half = max(1, int(self.max_search_km / step_km) + 1)
        win = {"latitude": half, "longitude": half}
        block, slices = store.read_window(binding.variable, index, win)
        axis = {d: i for i, d in enumerate(meta.dims)}
        block = np.squeeze(block, axis=tuple(
            axis[d] for d in meta.dims if d not in ("latitude", "longitude")))
        if axis["latitude"] > axis["longitude"]:
            block = block.T

        best = None
        y0, x0 = slices["latitude"].start, slices["longitude"].start
        ys, xs = np.where(~np.isnan(block))
        for dy, dx in zip(ys, xs):
            iy2, ix2 = y0 + int(dy), x0 + int(dx)
            km = haversine_km(lat, lon, float(lats[iy2]), float(lons[ix2]))
            if km > self.max_search_km:
                continue
            if best is None or km < best[3]:
                best = (float(block[dy, dx]), iy2, ix2, km)
        return best

    def fetch_local_field(self, parameter: str, lat: float, lon: float,
                          valid_time: datetime, radius_km: float = 100.0):
        """Read a neighbourhood of valid values around a point.

        Returns (values, binding, actual_time, cell_count). Used to express a
        value COMPARATIVELY -- "above the local median for this field" -- rather
        than against an absolute standard ORCA has not validated
        (12_RISK_AND_RECOMMENDATION_SPEC.md section 5.3).
        """
        bindings = BINDINGS.get(parameter)
        if not bindings:
            raise CmemsError(ErrorCode.DATASET_UNAVAILABLE,
                             f"no CMEMS binding for parameter {parameter!r}")
        binding = bindings[0]
        store = self._store(binding)
        lats = store.read_coord("latitude")
        lons = store.read_coord("longitude")
        times = self._time_axis(store, binding)

        q_lon = lon if lon <= 180.0 else lon - 360.0
        iy, _ = nearest_index(lats, lat)
        ix, _ = nearest_index(lons, q_lon)
        t_arr = np.array([t.timestamp() for t in times])
        it, _ = nearest_index(t_arr, valid_time.timestamp())

        lat_step_km = abs(float(lats[1] - lats[0])) * 111.32
        half = max(1, int(radius_km / max(lat_step_km, 1e-6)))
        meta = store.array(binding.variable)
        index = {"time": it, "latitude": iy, "longitude": ix}
        if "elevation" in meta.dims:
            index["elevation"] = 0
        block, _ = store.read_window(binding.variable, index,
                                     {"latitude": half, "longitude": half})
        vals = block[~np.isnan(block)]
        if vals.size == 0:
            raise CmemsError(ErrorCode.NO_DATA,
                             f"{parameter}: no valid cells within {radius_km:g} km")
        published_unit = meta.units or binding.canonical_unit
        vals = np.array([U.convert(float(v), published_unit, binding.canonical_unit)
                         for v in vals])
        return vals, binding, times[it], int(vals.size)

    def fetch_grid(self, parameter: str, lat: float, lon: float,
                   valid_time: datetime, radius_km: float = 200.0):
        """A rectangular field around a point, WITH its axes and its holes.

        `fetch_local_field` flattens and drops NaNs, which is right for a median
        and wrong for a map: rendering needs the grid shape, the coordinates,
        and the masked cells left as gaps. A land-masked or cloud-masked cell
        must reach the client as null and be drawn as absent -- rendering it as
        zero would paint a calm sea over missing data (F-10, in pixels).

        Returns (lats, lons, block, binding, actual_time), block[y][x] with NaN
        where there is no value.
        """
        bindings = BINDINGS.get(parameter)
        if not bindings:
            raise CmemsError(ErrorCode.DATASET_UNAVAILABLE,
                             f"no CMEMS binding for parameter {parameter!r}")
        binding = bindings[0]
        store = self._store(binding)
        lats = store.read_coord("latitude")
        lons = store.read_coord("longitude")
        times = self._time_axis(store, binding)

        q_lon = lon if lon <= 180.0 else lon - 360.0
        iy, _ = nearest_index(lats, lat)
        ix, _ = nearest_index(lons, q_lon)
        t_arr = np.array([t.timestamp() for t in times])
        it, _ = nearest_index(t_arr, valid_time.timestamp())

        lat_step_km = abs(float(lats[1] - lats[0])) * 111.32
        half = max(1, int(radius_km / max(lat_step_km, 1e-6)))
        # A field big enough to look like a map is a lot of cells; cap it so a
        # careless radius cannot pull tens of megabytes through the chunk reader.
        half = min(half, 120)

        meta = store.array(binding.variable)
        index = {"time": it, "latitude": iy, "longitude": ix}
        if "elevation" in meta.dims:
            index["elevation"] = 0
        block, slices = store.read_window(binding.variable, index,
                                          {"latitude": half, "longitude": half})
        block = np.squeeze(block)
        published_unit = meta.units or binding.canonical_unit
        if published_unit != binding.canonical_unit:
            with np.errstate(invalid="ignore"):
                block = np.vectorize(
                    lambda v: (np.nan if np.isnan(v)
                               else U.convert(float(v), published_unit,
                                              binding.canonical_unit)))(block)
        out_lats = lats[slices["latitude"]]
        out_lons = lons[slices["longitude"]]
        return out_lats, out_lons, block, binding, times[it]

    # -- public API ------------------------------------------------------------

    def fetch_point(self, parameter: str, lat: float, lon: float,
                    valid_time: datetime) -> CmemsPointResult:
        bindings = BINDINGS.get(parameter)
        if not bindings:
            raise CmemsError(ErrorCode.DATASET_UNAVAILABLE,
                             f"no CMEMS binding for parameter {parameter!r}")
        last: CmemsError | None = None
        for binding in bindings:
            try:
                return self._fetch_one(binding, lat, lon, valid_time)
            except ZarrError as exc:
                last = CmemsError(canonical_code(exc), exc.detail)
            except CmemsError as exc:
                last = exc
        raise last or CmemsError(ErrorCode.NO_DATA, "no binding produced data")

    def _fetch_one(self, binding: CmemsBinding, lat: float, lon: float,
                   requested_time: datetime) -> CmemsPointResult:
        store = self._store(binding)
        codes: list[ErrorCode] = []
        notes: list[str] = []

        lats = store.read_coord("latitude")
        lons = store.read_coord("longitude")
        times = self._time_axis(store, binding)

        # CMEMS global grids run -180..180; normalise a 0..360 request.
        q_lon = lon if lon <= 180.0 else lon - 360.0
        iy, dlat = nearest_index(lats, lat)
        ix, dlon = nearest_index(lons, q_lon)
        node_km = haversine_km(lat, q_lon, float(lats[iy]), float(lons[ix]))

        t_arr = np.array([t.timestamp() for t in times])
        it, _ = nearest_index(t_arr, requested_time.timestamp())
        actual_time = times[it]
        offset_h = (actual_time - requested_time).total_seconds() / 3600.0

        step_h = ((times[1] - times[0]).total_seconds() / 3600.0
                  if len(times) > 1 else 1.0)
        if abs(offset_h) > step_h:
            # The request falls outside the published time axis; do not extrapolate.
            codes.append(ErrorCode.STALE_DATA if actual_time < requested_time
                         else ErrorCode.INSUFFICIENT_COVERAGE)
            notes.append(
                f"nearest available step is {actual_time:%Y-%m-%d %H:%MZ}, "
                f"{abs(offset_h):.1f} h from the requested time "
                f"(axis covers {times[0]:%Y-%m-%d}..{times[-1]:%Y-%m-%d})")

        meta = store.array(binding.variable)
        index = {"time": it, "latitude": iy, "longitude": ix}
        if "elevation" in meta.dims:
            index["elevation"] = 0
        if "depth" in meta.dims:
            index["depth"] = 0

        value = store.read_point(binding.variable, index)
        if value is None:
            # Coastal cells are routinely land-masked in wave models. Search
            # outward for the nearest valid ocean cell rather than reporting no
            # data, and carry the distance so the caller can judge relevance.
            found = self._nearest_valid(store, binding, meta, index, lats, lons,
                                        lat, q_lon)
            if found is None:
                raise CmemsError(
                    ErrorCode.NO_DATA,
                    f"{binding.dataset_id}.{binding.variable}: no valid cell within "
                    f"{self.max_search_km:g} km of {lat:.3f}N {q_lon:.3f}E "
                    f"(land-masked)")
            value, iy, ix, node_km = found
            codes.append(ErrorCode.INSUFFICIENT_COVERAGE)
            notes.append(
                f"the requested position is land-masked in this product; nearest "
                f"valid ocean cell is {node_km:.1f} km away at "
                f"{lats[iy]:.3f}N {lons[ix]:.3f}E")
            index = {**index, "latitude": iy, "longitude": ix}

        # Read the published unit and convert explicitly. Never assume.
        published_unit = meta.units or binding.canonical_unit
        try:
            value = U.convert(value, published_unit, binding.canonical_unit)
        except U.UnitError as exc:
            raise CmemsError(ErrorCode.ADAPTER_ERROR,
                             f"{binding.variable}: {exc}") from exc

        # Source-published uncertainty, where the product provides it.
        uncertainty = None
        if binding.uncertainty_variable:
            try:
                unc = store.read_point(binding.uncertainty_variable, index)
            except ZarrError:
                unc = None
            if unc is not None:
                u_meta = store.array(binding.uncertainty_variable)
                u_unit = u_meta.units
                if binding.uncertainty_kind == "std_dev" and U.convertible(
                        u_unit, binding.canonical_unit):
                    # An error in kelvin is a magnitude, not a temperature:
                    # convert the scale, not the offset.
                    if U.canonical(u_unit) == "K" and binding.canonical_unit == "degC":
                        u_unit = "degC"
                uncertainty = Uncertainty(value_uncertainty={
                    "type": binding.uncertainty_kind, "value": round(unc, 4),
                    "unit": u_unit, "basis": "source-provided",
                    "variable": binding.uncertainty_variable})

        flag = QualityFlag.NOMINAL
        lo, hi = PLAUSIBLE.get(binding.parameter, (-np.inf, np.inf))
        if not (lo <= value <= hi):
            flag = QualityFlag.SUSPECT
            notes.append(f"value {value:g} outside the plausible range {lo}..{hi}")

        now = utcnow()
        lead_h = (actual_time - now).total_seconds() / 3600.0
        is_forecast = actual_time > now

        quality = QualityMetadata(
            flag=flag, basis="source-provided",
            nearest_node_distance_km=round(node_km, 2),
            lead_time_h=round(lead_h, 2),
            freshness=Freshness.FRESH if abs(lead_h) < 72 else Freshness.AGING,
            staleness_s=max(0.0, (now - actual_time).total_seconds()),
        )
        quality.add_check("unit_read_from_store", "pass",
                          f"published={published_unit!r} -> {binding.canonical_unit!r}")
        quality.add_check("plausibility_range", "pass" if flag is QualityFlag.NOMINAL
                          else "fail", f"{lo}..{hi}")
        quality.add_check("nearest_node", "pass", f"{node_km:.2f} km from request")

        spatial = SpatialRef.point(lat=float(lats[iy]), lon=float(lons[ix]),
                                   nearest_node_distance_km=round(node_km, 2))
        temporal = TemporalRef(
            valid_time=actual_time,
            representativeness=binding.representativeness,
            temporal_resolution=f"PT{int(step_h)}H",
            lead_time_h=round(lead_h, 2) if is_forecast else None,
            retrieved_at=now,
        )
        pid = "pv-" + hashlib.sha256(
            f"{binding.dataset_id}|{binding.variable}|{it}|{iy}|{ix}".encode()
        ).hexdigest()[:10]

        prov = Provenance(
            provenance_id=pid, parameter=binding.parameter,
            value_kind=ValueKind.FORECAST if is_forecast else ValueKind.OBSERVED,
            unit=binding.canonical_unit, spatial=spatial, temporal=temporal,
            source=SOURCE_NAME, source_id=SOURCE_ID, organisation=ORGANISATION,
            dataset=binding.dataset_id, product_reference=binding.product_id,
            access_method=ACCESS_METHOD, external_source=True,
            retrieved_at=now,
            request_fingerprint="sha256:" + hashlib.sha256(
                binding.store_url.encode()).hexdigest()[:16],
            spatial_resolution="0.083 deg (1/12 deg)",
            temporal_resolution=f"PT{int(step_h)}H",
            quality=quality, notes=binding.note,
            uncertainty=uncertainty,
            licence_reference=ATTRIBUTION,
        )
        obs = Forecast(
            parameter=binding.parameter, value=round(value, 4),
            unit=binding.canonical_unit,
            value_kind=ValueKind.FORECAST if is_forecast else ValueKind.OBSERVED,
            spatial=spatial, temporal=temporal, quality=quality,
            uncertainty=uncertainty, provenance_id=pid)
        return CmemsPointResult([obs], [prov], codes, notes, binding.dataset_id)
