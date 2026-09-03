"""Canonical spatial/temporal/provenance model.

Every value that enters ORCA's reasoning layer is represented here, regardless
of which provider it came from. See 05_CANONICAL_DATA_SCHEMA.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    Confidence, Freshness, QualityFlag, Representation, Representativeness, ValueKind,
)

WGS84 = "EPSG:4326"
_EARTH_R_KM = 6371.0088


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BBox(BaseModel):
    model_config = ConfigDict(frozen=True)
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @model_validator(mode="after")
    def _check(self) -> "BBox":
        if not (-90 <= self.min_lat < self.max_lat <= 90):
            raise ValueError("INVALID_BBOX: latitude bounds")
        if not (-180 <= self.min_lon < self.max_lon <= 180):
            raise ValueError("INVALID_BBOX: longitude bounds")
        return self

    def area_km2(self) -> float:
        """Spherical area -- never degree arithmetic (see 11 section 4)."""
        lat1, lat2 = math.radians(self.min_lat), math.radians(self.max_lat)
        dlon = math.radians(self.max_lon - self.min_lon)
        return abs(_EARTH_R_KM**2 * dlon * (math.sin(lat2) - math.sin(lat1)))


class SpatialRef(BaseModel):
    """Where a value applies. CRS is always explicit; never assumed."""
    kind: Literal["point", "bbox", "grid", "geometry", "linestring"]
    crs: str = WGS84
    lat: float | None = None
    lon: float | None = None
    coordinates: list[list[float]] | None = None  # For linestrings: [[lon, lat], [lon, lat], ...]
    bbox: BBox | None = None
    depth_m: float | None = None                 # positive downward
    nearest_node_distance_km: float | None = None
    area_description: str | None = None
    representation: Representation | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "SpatialRef":
        if self.kind == "point":
            if self.lat is None or self.lon is None:
                raise ValueError("INVALID_LOCATION: point requires lat and lon")
            if not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):
                raise ValueError("INVALID_LOCATION: out of range")
        if self.kind == "linestring":
            if not self.coordinates or len(self.coordinates) < 2:
                raise ValueError("INVALID_LOCATION: linestring requires at least 2 coordinates")
        if self.kind in ("bbox", "grid") and self.bbox is None:
            raise ValueError("INVALID_BBOX: bbox required")
        return self

    @classmethod
    def point(cls, lat: float, lon: float, **kw: Any) -> "SpatialRef":
        return cls(kind="point", lat=lat, lon=lon, **kw)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Degrees are never used as a distance unit."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(a))


class TemporalRef(BaseModel):
    """When a value applies, and what it represents."""
    valid_time: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    reference_time: datetime | None = None
    lead_time_h: float | None = None
    temporal_resolution: str | None = None       # ISO-8601 duration as published
    representativeness: Representativeness = Representativeness.INSTANTANEOUS
    retrieved_at: datetime = Field(default_factory=utcnow)

    @field_validator("valid_time", "valid_from", "valid_to", "reference_time",
                     "retrieved_at", mode="after")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("all times must be timezone-aware UTC")
        return v

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or utcnow()) - self.valid_time


class QualityMetadata(BaseModel):
    flag: QualityFlag = QualityFlag.UNKNOWN
    basis: Literal["source-provided", "orca-computed", "unknown"] = "unknown"
    freshness: Freshness | None = None
    staleness_s: float | None = None
    coverage_fraction: float | None = None
    masked_reason: str | None = None
    nearest_node_distance_km: float | None = None
    lead_time_h: float | None = None
    representativeness_match: bool | None = None
    validation_checks: list[dict[str, Any]] = Field(default_factory=list)

    def add_check(self, check: str, result: str, detail: str = "") -> None:
        self.validation_checks.append({"check": check, "result": result, "detail": detail})


class Derivation(BaseModel):
    """Makes a derived value recomputable."""
    method: str
    method_version: str
    inputs: list[str] = Field(default_factory=list)   # provenance ids
    params: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=utcnow)
    code_reference: str | None = None


class Provenance(BaseModel):
    """Where a value came from. No value enters reasoning without one."""
    provenance_id: str
    parameter: str
    value_kind: ValueKind
    unit: str | None = None
    spatial: SpatialRef | None = None
    temporal: TemporalRef | None = None

    source: str
    source_id: str
    organisation: str | None = None
    dataset: str | None = None
    dataset_version: str | None = None
    product_reference: str | None = None
    access_method: str | None = None
    external_source: bool = False

    retrieved_at: datetime = Field(default_factory=utcnow)
    request_fingerprint: str | None = None
    response_bytes: int | None = None
    cache_hit: bool = False

    spatial_resolution: str | None = None
    temporal_resolution: str | None = None
    quality: QualityMetadata = Field(default_factory=QualityMetadata)
    uncertainty: "Uncertainty | None" = None

    fallback_used: bool = False
    fallback_reason: str | None = None
    derivation: Derivation | None = None
    notes: str | None = None
    licence_reference: str | None = None

    @model_validator(mode="after")
    def _derived_needs_derivation(self) -> "Provenance":
        if self.value_kind == ValueKind.DERIVED and self.derivation is None:
            raise ValueError(
                "SCHEMA_VALIDATION_FAILED: derived value requires a derivation record"
            )
        return self


class Uncertainty(BaseModel):
    value_uncertainty: dict[str, Any] | None = None
    spatial_uncertainty: dict[str, Any] | None = None
    temporal_uncertainty: dict[str, Any] | None = None
    evidence_sufficiency: dict[str, Any] | None = None
