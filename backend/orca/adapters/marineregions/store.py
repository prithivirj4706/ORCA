"""Versioned boundary snapshot: writer (capture time) and reader (query time).

A boundary answer must be reproducible. A run that said "inside the Indian EEZ"
in March must still be checkable in September against the geometry it actually
used, so geometry is pinned to a snapshot rather than fetched per query
(11_GEOSPATIAL_REASONING_SPEC.md section 13, "version binding").

Layout
    data/boundaries/<snapshot_version>/
        manifest.json        provenance, layer versions, per-feature attributes
        <layer>.npz          flattened full-precision geometry

The geometry is stored flat -- one coordinate array per layer plus ring, polygon
and feature offsets -- rather than as GeoJSON. Re-parsing 6 MB of JSON on every
query would dominate the cost of the query itself, and a numpy load is memory
mapped.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ...geospatial.topology import FeatureIndex, build_index

MANIFEST_NAME = "manifest.json"
SNAPSHOT_FORMAT = "orca-boundary-snapshot/1"


class SnapshotError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class BoundaryFeature:
    """One boundary polygon's attributes, as published by the source."""
    feature_id: str
    index: int
    mrgid: int | None
    name: str | None
    territory: str | None
    sovereign: str | None
    iso_ter: str | None
    iso_sov: str | None
    pol_type: str | None
    area_km2: float | None
    bbox: tuple[float, float, float, float]      # min_lon, min_lat, max_lon, max_lat

    @property
    def is_disputed(self) -> bool:
        """Joint regimes and overlapping claims are labelled by the source."""
        p = (self.pol_type or "").lower()
        return "joint" in p or "disput" in p or "overlap" in p


@dataclass(frozen=True, slots=True)
class LayerSnapshot:
    layer: str
    boundary_type: str
    title: str
    dataset_version: str
    effective_year: str
    effective_date: datetime
    abstract: str
    features: list[BoundaryFeature]
    index: FeatureIndex
    vertex_count: int
    request_url: str
    request_fingerprint: str
    geometry_sha256: str


# -- attribute extraction ------------------------------------------------------

def _first(props: dict[str, Any], *names: str) -> Any:
    for n in names:
        v = props.get(n)
        if v not in (None, ""):
            return v
    return None


def _feature_attributes(idx: int, feature: dict, index: FeatureIndex,
                        layer: str) -> BoundaryFeature:
    p = feature.get("properties") or {}
    f0, f1 = int(index.feature_offsets[idx]), int(index.feature_offsets[idx + 1])
    r0 = int(index.polygon_offsets[f0])
    r1 = int(index.polygon_offsets[f1])
    if r1 > r0:
        boxes = index.ring_bbox[r0:r1]
        bbox = (float(boxes[:, 0].min()), float(boxes[:, 1].min()),
                float(boxes[:, 2].max()), float(boxes[:, 3].max()))
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
    return BoundaryFeature(
        feature_id=str(feature.get("id") or f"{layer}.{idx}"),
        index=idx,
        mrgid=_first(p, "mrgid"),
        name=_first(p, "geoname", "name"),
        territory=_first(p, "territory1", "territory"),
        sovereign=_first(p, "sovereign1", "sovereign"),
        iso_ter=_first(p, "iso_ter1"),
        iso_sov=_first(p, "iso_sov1"),
        pol_type=_first(p, "pol_type"),
        area_km2=_first(p, "area_km2"),
        bbox=bbox,
    )


# -- writing -------------------------------------------------------------------

def write_layer(directory: pathlib.Path, layer: str, boundary_type: str,
                collection: dict, *, title: str, dataset_version: str,
                effective_year: str, abstract: str, request_url: str) -> dict:
    """Flatten a WFS FeatureCollection into the snapshot. Returns its manifest."""
    features = collection.get("features") or []
    index, vertices = build_index([f.get("geometry") for f in features])
    attrs = [_feature_attributes(i, f, index, layer) for i, f in enumerate(features)]

    slug = layer.split(":")[-1]
    path = directory / f"{slug}.npz"
    directory.mkdir(parents=True, exist_ok=True)
    np.savez(path, coords=index.coords, ring_offsets=index.ring_offsets,
             ring_bbox=index.ring_bbox, polygon_offsets=index.polygon_offsets,
             feature_offsets=index.feature_offsets)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "layer": layer,
        "boundary_type": boundary_type,
        "title": title,
        "dataset_version": dataset_version,
        "effective_year": effective_year,
        "abstract": abstract,
        "geometry_file": path.name,
        "geometry_sha256": f"sha256:{digest}",
        "feature_count": len(features),
        "vertex_count": vertices,
        "request_url": request_url,
        "request_fingerprint": "sha256:" + hashlib.sha256(
            request_url.encode()).hexdigest()[:16],
        "features": [
            {"feature_id": a.feature_id, "index": a.index, "mrgid": a.mrgid,
             "name": a.name, "territory": a.territory, "sovereign": a.sovereign,
             "iso_ter": a.iso_ter, "iso_sov": a.iso_sov, "pol_type": a.pol_type,
             "area_km2": a.area_km2, "bbox": list(a.bbox)}
            for a in attrs
        ],
    }


def write_manifest(directory: pathlib.Path, manifest: dict) -> pathlib.Path:
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    return path


# -- reading -------------------------------------------------------------------

@dataclass(slots=True)
class BoundarySnapshot:
    """A loaded, versioned boundary snapshot. Layers load lazily."""
    root: pathlib.Path
    snapshot_version: str
    captured_at: datetime
    region: dict[str, float]
    source: dict[str, Any]
    _layer_meta: dict[str, dict]
    _loaded: dict[str, LayerSnapshot]

    @property
    def layers(self) -> list[str]:
        return sorted(self._layer_meta)

    def covers(self, lat: float, lon: float) -> bool:
        r = self.region
        return (r["min_lat"] <= lat <= r["max_lat"]
                and r["min_lon"] <= lon <= r["max_lon"])

    def layer(self, name: str) -> LayerSnapshot:
        if name in self._loaded:
            return self._loaded[name]
        meta = self._layer_meta.get(name)
        if meta is None:
            raise SnapshotError(f"layer {name!r} is not in snapshot "
                                f"{self.snapshot_version}")
        path = self.root / meta["geometry_file"]
        if not path.is_file():
            raise SnapshotError(f"snapshot {self.snapshot_version} declares "
                                f"{meta['geometry_file']} but the file is missing")
        with np.load(path) as z:
            index = FeatureIndex(
                coords=z["coords"], ring_offsets=z["ring_offsets"],
                ring_bbox=z["ring_bbox"], polygon_offsets=z["polygon_offsets"],
                feature_offsets=z["feature_offsets"])
        features = [
            BoundaryFeature(
                feature_id=f["feature_id"], index=f["index"], mrgid=f.get("mrgid"),
                name=f.get("name"), territory=f.get("territory"),
                sovereign=f.get("sovereign"), iso_ter=f.get("iso_ter"),
                iso_sov=f.get("iso_sov"), pol_type=f.get("pol_type"),
                area_km2=f.get("area_km2"), bbox=tuple(f["bbox"]))
            for f in meta["features"]
        ]
        if index.feature_count != len(features):
            raise SnapshotError(
                f"layer {name!r}: manifest lists {len(features)} features but the "
                f"geometry holds {index.feature_count}")
        year = str(meta["effective_year"])
        snap = LayerSnapshot(
            layer=name, boundary_type=meta["boundary_type"], title=meta["title"],
            dataset_version=meta["dataset_version"], effective_year=year,
            effective_date=datetime(int(year), 1, 1, tzinfo=timezone.utc),
            abstract=meta.get("abstract", ""), features=features, index=index,
            vertex_count=int(meta["vertex_count"]),
            request_url=meta.get("request_url", ""),
            request_fingerprint=meta.get("request_fingerprint", ""),
            geometry_sha256=meta.get("geometry_sha256", ""))
        self._loaded[name] = snap
        return snap


def find_snapshot(directory: str | pathlib.Path,
                  version: str | None = None) -> pathlib.Path | None:
    """Locate a snapshot directory: the pinned version, else the newest."""
    root = pathlib.Path(directory)
    if not root.is_dir():
        return None
    if version:
        cand = root / version
        return cand if (cand / MANIFEST_NAME).is_file() else None
    dirs = sorted((d for d in root.iterdir()
                   if d.is_dir() and (d / MANIFEST_NAME).is_file()),
                  key=lambda d: d.name)
    return dirs[-1] if dirs else None


def load_snapshot(path: str | pathlib.Path) -> BoundarySnapshot:
    root = pathlib.Path(path)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SnapshotError(f"no {MANIFEST_NAME} in {root}")
    m = json.loads(manifest_path.read_text())
    if m.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError(f"{manifest_path}: unsupported snapshot format "
                            f"{m.get('format')!r}")
    return BoundarySnapshot(
        root=root, snapshot_version=m["snapshot_version"],
        captured_at=datetime.fromisoformat(m["captured_at"]),
        region={k: float(v) for k, v in m["region"].items()},
        source=m.get("source", {}),
        _layer_meta={lm["layer"]: lm for lm in m.get("layers", [])},
        _loaded={})
