"""Runtime context passed through LangGraph config, not through state.

The tool registry, the LLM provider and the run budget are live objects: they
are not serialisable into a checkpoint and must not be. They travel in
`config["configurable"]["orca"]`, which keeps graph state to plain data that can
be checkpointed and replayed for audit (07 section 10).

This is also what preserves the layering rule: the registry arrives already
bound by the composition root, so nothing in `graph/` or `agents/` imports
`adapters/`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..agents.base import Budget
from ..llm.provider import LLMProvider, UnavailableProvider
from ..llm.usage import UsageLedger
from ..tools.registry import ToolRegistry

MAX_REPLANS = 2
MAX_CONCURRENT_TOOLS = 6


@dataclass
class OrcaRuntime:
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    llm: LLMProvider = field(default_factory=UnavailableProvider)
    ledger: UsageLedger = field(default_factory=UsageLedger)
    budget: Budget = field(default_factory=Budget)
    #: Navigability test for route planning, supplied by the composition
    #: root. `None` means routing is unavailable and says so.
    navigable: Any = None
    #: Gridded wave and wind for route STEERING, supplied by the composition
    #: root as `fn(lat, lon, valid_time, radius_km) -> (fields, provenance,
    #: unavailable)`. `None` means the route is planned on distance and
    #: navigability alone -- which is a legitimate degradation, but one the
    #: answer must state rather than present as an optimised route.
    route_fields: Any = None
    max_replans: int = MAX_REPLANS
    #: Analysis window length when the query implies a period rather than an instant.
    window_hours: int = 4

    def configurable(self) -> dict[str, Any]:
        return {"orca": self}


def runtime_from(config: dict | None) -> OrcaRuntime:
    """Read the runtime out of a LangGraph config, with a safe default."""
    if not config:
        return OrcaRuntime()
    configurable = config.get("configurable") or {}
    rt = configurable.get("orca")
    return rt if isinstance(rt, OrcaRuntime) else OrcaRuntime()
