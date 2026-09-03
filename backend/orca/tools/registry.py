"""Capability-tool registry: per-environment enablement and agent allow-lists.

18_REPOSITORY_STRUCTURE.md section 1 -- `agents/` never imports `adapters/`.
The registry is the seam that makes that possible. It carries:

  * a CATALOGUE of pure metadata (name, args schema, evidence yielded), which is
    all the Planner is permitted to see (06 section 3.4);
  * optional bound callables, supplied by the composition root, which the Data
    Discovery Agent invokes without knowing what is behind them.

A tool absent from the registry is never planned. A tool present but
unavailable is planned as an explicit gap, so the answer can state what it could
not check rather than silently omitting it (06 section 3.8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..schemas.enums import Domain

#: JSON Schema shared by the point-in-time capability tools.
_POINT_ARGS = {
    "type": "object",
    "properties": {
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
        "valid_time": {"type": "string", "format": "date-time"},
    },
    "required": ["lat", "lon", "valid_time"],
    "additionalProperties": False,
}

_POINT_ARGS_NO_TIME = {
    "type": "object",
    "properties": {
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
    },
    "required": ["lat", "lon"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ToolSpec:
    """Everything an agent may know about a capability tool."""

    name: str
    description: str
    args_schema: dict[str, Any]
    #: Assessment factors this tool can contribute. The Planner maps required
    #: evidence to tools through this, deterministically.
    yields: tuple[str, ...]
    domains: tuple[Domain, ...]
    timeout_s: float = 20.0
    #: Widening this tool's request is defensible for these argument keys.
    #: An empty tuple means the request may never be widened -- a warning for a
    #: different area is a different warning (06 section 4.7).
    widenable: tuple[str, ...] = ()


#: The P0 capability set (04_ORCA_TOOL_CONTRACTS.md).
CATALOGUE: tuple[ToolSpec, ...] = (
    ToolSpec("get_marine_warnings",
             "Official marine warnings in force for an area and time.",
             _POINT_ARGS, ("official_warning_status",), (Domain.SAFETY,), 15.0, ()),
    ToolSpec("get_wave_conditions",
             "Significant wave height, swell and period.",
             _POINT_ARGS, ("significant_wave_height", "swell_height", "peak_period"),
             (Domain.SAFETY,), 25.0, ("radius_km",)),
    ToolSpec("get_weather",
             "Wind components, from which speed and direction are derived.",
             _POINT_ARGS, ("wind_speed", "wind_gust"), (Domain.SAFETY,), 25.0,
             ("radius_km",)),
    ToolSpec("get_currents",
             "Surface current components.",
             _POINT_ARGS, ("current_speed",), (Domain.SAFETY,), 25.0, ("radius_km",)),
    ToolSpec("get_lightning",
             "Recent lightning activity.",
             _POINT_ARGS, ("lightning",), (Domain.SAFETY,), 15.0, ("radius_km",)),
    ToolSpec("get_cyclone_track",
             "Active cyclone tracks and distance to the position.",
             _POINT_ARGS, ("cyclone_distance_km",), (Domain.SAFETY,), 15.0, ()),
    ToolSpec("get_sst",
             "Sea surface temperature and anomaly.",
             _POINT_ARGS, ("sst", "sst_anomaly_abs"),
             (Domain.FISHING_SUITABILITY,), 25.0, ("radius_km",)),
    ToolSpec("get_chlorophyll",
             "Ocean colour; yields a ratio to the local median, never an "
             "absolute judgement.",
             _POINT_ARGS,
             ("chlorophyll_a", "chlorophyll_ratio_to_local_median"),
             (Domain.FISHING_SUITABILITY,), 30.0, ("radius_km",)),
    ToolSpec("get_pfz",
             "INCOIS Potential Fishing Zone advisory.",
             _POINT_ARGS, ("pfz_advisory", "pfz_distance_km"),
             (Domain.FISHING_SUITABILITY,), 25.0, ("radius_km",)),
    ToolSpec("get_ocean_observations",
             "Subsurface temperature and salinity.",
             _POINT_ARGS, ("temperature", "salinity"),
             (Domain.FISHING_SUITABILITY,), 25.0, ("radius_km",)),
    #: NOT in 04_ORCA_TOOL_CONTRACTS.md's list of eleven. The problem statement
    #: names tide explicitly ("What are the tide, weather, and sea conditions
    #: near my fishing location?"), so the capability is DECLARED even though no
    #: source currently serves it -- an answer that silently omitted tide would
    #: read as though tide had been considered (D-15).
    ToolSpec("get_tides",
             "Tidal height and phase at a coastal position.",
             _POINT_ARGS, ("tide_height", "tide_phase"),
             (Domain.SAFETY,), 15.0, ()),
    ToolSpec("get_maritime_boundaries",
             "Point-in-polygon against versioned maritime boundary geometry.",
             _POINT_ARGS_NO_TIME, ("maritime_boundaries",),
             (Domain.REGULATORY,), 15.0, ()),
)

_BY_NAME = {t.name: t for t in CATALOGUE}


@dataclass
class ToolRegistry:
    """Per-environment enablement plus the bound implementations.

    `catalogue()` is the projection agents are allowed to see: names, schemas
    and declared evidence -- no callables, no sources, no URLs.
    """

    _bound: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _unavailable: dict[str, str] = field(default_factory=dict)

    def bind(self, name: str, fn: Callable[..., Any]) -> "ToolRegistry":
        if name not in _BY_NAME:
            raise KeyError(f"{name} is not a known capability tool")
        self._bound[name] = fn
        self._unavailable.pop(name, None)
        return self

    def mark_unavailable(self, name: str, reason: str) -> "ToolRegistry":
        """Declare a capability that exists in the catalogue but has no source
        in this environment. It is planned, then reported as a gap."""
        if name not in _BY_NAME:
            raise KeyError(f"{name} is not a known capability tool")
        self._unavailable[name] = reason
        return self

    def spec(self, name: str) -> ToolSpec:
        return _BY_NAME[name]

    def catalogue(self) -> tuple[ToolSpec, ...]:
        """Every tool the Planner may reference, available or not."""
        return CATALOGUE

    def is_available(self, name: str) -> bool:
        return name in self._bound

    def unavailable_reason(self, name: str) -> str | None:
        return self._unavailable.get(name)

    def available_names(self) -> list[str]:
        return sorted(self._bound)

    def call(self, name: str, **kwargs: Any) -> Any:
        fn = self._bound.get(name)
        if fn is None:
            raise KeyError(f"{name} is not bound in this environment")
        return fn(**kwargs)

    def tools_yielding(self, evidence: str) -> list[str]:
        return [t.name for t in CATALOGUE if evidence in t.yields]
