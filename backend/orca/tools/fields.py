"""Gridded fields for map rendering.

A capability tool like any other: agents and the API ask for a field by
canonical name and never learn which provider serves it.

The one rule that matters here is that **a masked cell reaches the client as
`null`**, never as zero. A land-masked wave cell or a cloud-masked chlorophyll
cell drawn as 0.0 would paint a calm, empty sea over missing data -- F-10 and
D-3 restated in pixels. The response reports how many cells are valid so a
renderer can say "partial coverage" rather than implying a complete picture.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np

#: Canonical field name -> what it is and where it comes from.
FIELDS: dict[str, dict[str, Any]] = {
    "wind": {"kind": "vector", "source": "gfs", "unit": "m s-1",
             "components": ("eastward_wind", "northward_wind"),
             "label": "Wind at 10 m"},
    "current": {"kind": "vector", "source": "cmems", "unit": "m s-1",
                "components": ("current_u", "current_v"),
                "label": "Surface current"},
    "chlorophyll": {"kind": "scalar", "source": "cmems", "unit": "mg m-3",
                    "parameter": "chlorophyll_a", "label": "Chlorophyll-a"},
    "sst": {"kind": "scalar", "source": "cmems", "unit": "degC",
            "parameter": "sst", "label": "Sea surface temperature"},
    "waves": {"kind": "scalar", "source": "cmems", "unit": "m",
              "parameter": "significant_wave_height",
              "label": "Significant wave height"},
}


class FieldError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _clean(a: np.ndarray) -> list:
    """NaN -> None. A hole must stay a hole all the way to the canvas."""
    return [[None if (v is None or (isinstance(v, float) and math.isnan(v)))
             else round(float(v), 4) for v in row] for row in np.atleast_2d(a)]


def get_field(name: str, lat: float, lon: float, valid_time: datetime, *,
              radius_km: float = 300.0, cmems=None, gfs=None) -> dict:
    spec = FIELDS.get(name)
    if spec is None:
        raise FieldError("DATASET_UNAVAILABLE",
                         f"no field named {name!r}; known: {sorted(FIELDS)}")

    if spec["source"] == "gfs":
        if gfs is None:
            raise FieldError("DATASET_UNAVAILABLE", "no GFS adapter supplied")
        lats, lons, blocks, actual = gfs.fetch_grid(
            list(spec["components"]), lat, lon, valid_time, radius_km=radius_km)
        u = np.asarray(blocks[spec["components"][0]], dtype="f8")
        v = np.asarray(blocks[spec["components"][1]], dtype="f8")
        source, source_id = "NOAA NCEP GFS", "S-11"
        dataset = "ncep_global"
    else:
        if cmems is None:
            raise FieldError("DATASET_UNAVAILABLE", "no CMEMS adapter supplied")
        if spec["kind"] == "vector":
            la, lo, ub, binding, actual = cmems.fetch_grid(
                spec["components"][0], lat, lon, valid_time, radius_km=radius_km)
            _, _, vb, _, _ = cmems.fetch_grid(
                spec["components"][1], lat, lon, valid_time, radius_km=radius_km)
            lats, lons, u, v = list(la), list(lo), np.asarray(ub), np.asarray(vb)
            dataset = binding.dataset_id
        else:
            la, lo, block, binding, actual = cmems.fetch_grid(
                spec["parameter"], lat, lon, valid_time, radius_km=radius_km)
            lats, lons, u, v = list(la), list(lo), np.asarray(block), None
            dataset = binding.dataset_id
        source, source_id = "CMEMS", "S-07"

    primary = u if v is None else np.sqrt(u ** 2 + v ** 2)
    valid = int(np.sum(~np.isnan(primary)))
    if valid == 0:
        raise FieldError("NO_DATA", f"{name}: no valid cells in this area")

    out: dict[str, Any] = {
        "field": name,
        "label": spec["label"],
        "kind": spec["kind"],
        "unit": spec["unit"],
        "lats": [round(float(x), 4) for x in lats],
        "lons": [round(float(x), 4) for x in lons],
        "valid_time": actual.isoformat(),
        "source": source,
        "source_id": source_id,
        "dataset": dataset,
        # A renderer must be able to say "partial coverage" rather than imply a
        # complete picture; masked cells are null, never zero.
        "cells": {"total": int(primary.size), "valid": valid,
                  "coverage": round(valid / primary.size, 3)},
        "range": {"min": round(float(np.nanmin(primary)), 4),
                  "max": round(float(np.nanmax(primary)), 4)},
        "advisory_only": True,
    }
    if spec["kind"] == "vector":
        out["u"] = _clean(u)
        out["v"] = _clean(v)
        out["speed"] = _clean(primary)
    else:
        out["values"] = _clean(u)
    return out

# ---------------------------------------------------------------- routing ---
#
# The router consumes gridded fields; every other consumer of `get_field` wants
# JSON for a canvas. These two helpers are the bridge, and they live here rather
# than in the graph so that the row-order normalisation below is testable
# without standing up a run.

#: Fields that steer a route, and the parameter name `routing.cost_function`
#: looks for. Wind is a vector product, so its SPEED is what matters here.
ROUTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("waves", "significant_wave_height"),
    ("wind", "wind_speed"),
)


def as_ocean_field(payload: dict, parameter: str, *, provenance_id: str):
    """A `get_field` payload as the gridded field the router consumes.

    Rows are normalised to ASCENDING latitude. `routing.extract_field_values`
    indexes with ``int((lat - bbox.min_lat) / dlat)``, so row 0 must be the
    southernmost latitude -- but sources publish either order. A flipped grid
    would apply each penalty to the MIRROR IMAGE of the sea it was measured in,
    which is worse than applying no penalty at all: the route would look
    weather-aware while steering by an inverted picture.
    """
    from ..schemas.core import BBox, Provenance, SpatialRef, TemporalRef
    from ..schemas.data import OceanField
    from ..schemas.enums import ValueKind

    lats = payload["lats"]
    rows = payload.get("values")
    if rows is None:                      # vector field: steer on the magnitude
        rows = payload.get("speed")
    if not rows or not lats:
        return None, None

    if lats[0] > lats[-1]:                # published north-to-south
        rows = list(reversed(rows))
        lats = list(reversed(lats))

    lons = payload["lons"]
    bbox = BBox(min_lat=min(lats), min_lon=min(lons),
                max_lat=max(lats), max_lon=max(lons))
    valid_time = datetime.fromisoformat(payload["valid_time"])
    temporal = TemporalRef(valid_time=valid_time)
    field = OceanField(
        parameter=parameter, unit=payload.get("unit"),
        value_kind=ValueKind.FORECAST,
        spatial=SpatialRef(kind="bbox", bbox=bbox),
        temporal=temporal,
        values_inline=rows,
        summary={"cells": payload.get("cells"), "range": payload.get("range"),
                 "field": payload.get("field")},
        provenance_id=provenance_id,
    )
    prov = Provenance(
        provenance_id=provenance_id, parameter=parameter,
        value_kind=ValueKind.FORECAST, unit=payload.get("unit"),
        spatial=field.spatial, temporal=temporal,
        source=payload.get("source"), source_id=payload.get("source_id"),
        dataset=payload.get("dataset"),
        access_method="gridded field for route steering",
    )
    return field, prov


def route_fields(lat: float, lon: float, valid_time: datetime, *,
                 radius_km: float, cmems=None, gfs=None) -> tuple[list, list, list]:
    """Gridded fields for route steering: (fields, provenance, unavailable).

    Every failure is REPORTED, never swallowed. A route steered by nothing is a
    shortest path, and the answer has to be able to say so -- the whole risk
    here is a distance-only line that looks weather-aware.
    """
    fields, provenance, unavailable = [], [], []
    for i, (name, parameter) in enumerate(ROUTE_FIELDS):
        try:
            payload = get_field(name, lat, lon, valid_time,
                                radius_km=radius_km, cmems=cmems, gfs=gfs)
        except FieldError as exc:
            unavailable.append({"parameter": parameter, "reason": exc.code,
                                "detail": exc.detail})
            continue
        except Exception as exc:                      # adapter, network, decode
            unavailable.append({"parameter": parameter, "reason": "ADAPTER_ERROR",
                                "detail": f"{type(exc).__name__}: {exc}"})
            continue
        field, prov = as_ocean_field(
            payload, parameter, provenance_id=f"pv-route-{parameter[:12]}-{i}")
        if field is None:
            unavailable.append({"parameter": parameter, "reason": "NO_DATA",
                                "detail": f"{name}: no grid returned"})
            continue
        fields.append(field)
        provenance.append(prov)
    return fields, provenance, unavailable
