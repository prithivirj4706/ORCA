"""Boundary-type policy, loaded from config/boundaries.yaml.

Which WFS layer serves which boundary type is CONFIGURATION, not code: the
geometry source is versioned upstream and a boundary type without a source must
be able to say so (18_REPOSITORY_STRUCTURE.md section 5).

What a containment result MEANS is deliberately NOT here. Reading "inside
another state's EEZ" as a restriction is a legal judgement, and this layer only
reports what the source publishes -- the sovereign, the territory, the ISO code.
The judgement lives in `assessment/jurisdiction.py`, above the adapter layer.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from functools import lru_cache

import yaml

CONFIG = pathlib.Path(__file__).resolve().parents[4] / "config" / "boundaries.yaml"


@dataclass(frozen=True, slots=True)
class BoundaryType:
    name: str
    configured: bool
    layer: str | None = None
    description: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BoundaryPolicy:
    source_id: str
    source_name: str
    organisation: str
    endpoint: str
    access_method: str
    licence_reference: str
    snapshot_dir: str
    region: dict[str, float]
    region_note: str
    near_boundary_km: float
    types: dict[str, BoundaryType]

    @property
    def configured_types(self) -> list[str]:
        return [k for k, v in self.types.items() if v.configured]

    @property
    def unconfigured_types(self) -> list[str]:
        return [k for k, v in self.types.items() if not v.configured]

    def layer_for(self, boundary_type: str) -> str | None:
        t = self.types.get(boundary_type)
        return t.layer if t and t.configured else None


@lru_cache(maxsize=2)
def load_policy(path: str | None = None) -> BoundaryPolicy:
    p = pathlib.Path(path or CONFIG)
    if not p.is_file():
        raise FileNotFoundError(f"boundary policy not found at {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    for key in ("source", "snapshot", "boundary_types"):
        if key not in raw:
            raise ValueError(f"{p.name}: missing required key {key!r}")

    src, snap = raw["source"], raw["snapshot"]

    types = {}
    for name, spec in raw["boundary_types"].items():
        configured = bool(spec.get("configured"))
        if configured and not spec.get("layer"):
            raise ValueError(f"{p.name}: boundary type {name!r} is configured "
                             f"but names no layer")
        if not configured and not spec.get("reason"):
            raise ValueError(f"{p.name}: boundary type {name!r} is unconfigured "
                             f"and must state a reason")
        types[name] = BoundaryType(
            name=name, configured=configured, layer=spec.get("layer"),
            description=(spec.get("description") or "").strip() or None,
            reason=(spec.get("reason") or "").strip() or None)

    region = snap.get("region") or {}
    for key in ("min_lat", "min_lon", "max_lat", "max_lon"):
        if key not in region:
            raise ValueError(f"{p.name}: snapshot.region is missing {key!r}")

    return BoundaryPolicy(
        source_id=src["source_id"], source_name=src["name"],
        organisation=src["organisation"], endpoint=src["endpoint"],
        access_method=src["access_method"],
        licence_reference=(src.get("licence_reference") or "").strip(),
        snapshot_dir=snap.get("directory", "data/boundaries"),
        region={k: float(v) for k, v in region.items()},
        region_note=(snap.get("region_note") or "").strip(),
        near_boundary_km=float(raw.get("near_boundary_km", 5.0)),
        types=types)
