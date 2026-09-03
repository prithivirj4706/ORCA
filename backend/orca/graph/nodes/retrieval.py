"""tool_exec -- the fan-out worker (07 section 6).

A worker ALWAYS returns. It never raises across the node boundary, so one dead
source cannot kill the run and the fan-in always occurs.
"""
from __future__ import annotations

import time

from ...agents.discovery import DiscoveryAgent
from ...geospatial.derive import derive_from_envelope
from ..events import node_event
from ..runtime import runtime_from


def tool_exec(payload: dict, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    agent = DiscoveryAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)
    step = payload["step"]

    env, result, modification = agent.execute_step(step, rt.registry)

    # Kernel derivations (vector pairs -> scalar speed/direction) happen HERE,
    # not in geo_reason, because the validate gate runs in between and has to
    # judge evidence coverage against what the run actually holds. Deriving
    # later made `wind_speed` read as a required gap in the ValidationReport
    # while the assessment went on to use it -- an audit artifact that
    # contradicted the answer.
    if env is not None:
        try:
            d_data, d_prov = derive_from_envelope(env)
        except Exception:
            d_data, d_prov = [], []
        if d_data:
            env.data.extend(d_data)
            env.provenance.extend(d_prov)

    out: dict = {
        "step_results": [result],
        "tool_results": [env] if env is not None else [],
        "provenance": list(env.provenance) if env is not None else [],
        "modifications": [modification] if modification is not None else [],
        "fallbacks_used": ([{"step_id": result.step_id, "tool": result.tool,
                             "actual": result.source}] if result.fallback_used else []),
        "budget": {"tool_calls": 1},
        "node_events": [node_event(
            "tool_exec", result.outcome, started=started, step_id=result.step_id,
            tool=result.tool, source=result.source,
            fallback_used=result.fallback_used, codes=result.codes,
            summary=f"{result.tool} -> {result.outcome}")],
    }
    return out
