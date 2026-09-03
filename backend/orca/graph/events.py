"""Node event emission (07_LANGGRAPH_WORKFLOW_SPEC.md section 12).

NEVER logged: raw model chain-of-thought, credentials, or prompt text. Agents
emit a short reasoning_summary instead; that is what appears here.
"""
from __future__ import annotations

import time
from typing import Any

from ..schemas.core import utcnow


def node_event(node: str, status: str, *, started: float | None = None,
               summary: str = "", **extra: Any) -> dict:
    event = {
        "node": node,
        "status": status,
        "at": utcnow().isoformat(),
        "duration_ms": (int((time.perf_counter() - started) * 1000)
                        if started is not None else 0),
    }
    if summary:
        event["summary"] = summary
    event.update(extra)
    return event
