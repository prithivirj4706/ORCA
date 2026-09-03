"""Shared agent contract (06_AGENT_SPEC.md section 2).

Every agent: takes a typed slice of state, returns a validated typed object,
records a short reasoning_summary (never chain-of-thought), and returns a
STRUCTURED FAILURE rather than raising across the node boundary. A dead agent
must degrade the answer, not kill the run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from ..llm.provider import LLMProvider, LLMRequest, LLMResponse, LLMUnavailable
from ..llm.usage import BudgetExceeded, UsageLedger

T = TypeVar("T")


@dataclass
class Budget:
    """Token, wall-clock and tool-call ceilings (07 section 14).

    Exceeding a budget is a structured failure, not a hang.
    """

    wall_clock_ms: int = 30_000
    tool_calls: int = 32
    tokens: int | None = None
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _calls: int = field(default=0, repr=False)

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    def remaining_ms(self) -> int:
        return max(0, self.wall_clock_ms - self.elapsed_ms())

    def spend_call(self) -> None:
        self._calls += 1
        if self._calls > self.tool_calls:
            raise BudgetExceeded(f"tool-call budget {self.tool_calls} exhausted")

    def exhausted(self) -> bool:
        return self.remaining_ms() <= 0 or self._calls >= self.tool_calls


@dataclass(frozen=True)
class AgentFailure:
    code: str
    detail: str
    agent: str = ""


@dataclass
class AgentResult(Generic[T]):
    """Either a validated value or a structured failure -- never an exception."""

    agent: str
    value: T | None = None
    failure: AgentFailure | None = None
    reasoning_summary: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.failure is None and self.value is not None


class Agent:
    """Base class carrying the pieces every agent needs.

    `llm` is always present but may be unavailable; agents must consult
    `use_llm()` and fall back to their deterministic path rather than assuming a
    model exists.
    """

    name = "agent"

    def __init__(self, llm: LLMProvider | None = None,
                 ledger: UsageLedger | None = None,
                 budget: Budget | None = None):
        from ..llm.provider import UnavailableProvider
        self.llm = llm if llm is not None else UnavailableProvider()
        self.ledger = ledger if ledger is not None else UsageLedger()
        self.budget = budget if budget is not None else Budget()

    def use_llm(self) -> bool:
        return bool(getattr(self.llm, "available", False))

    def ask(self, request: LLMRequest) -> LLMResponse | None:
        """One bounded model call. Returns None when the model cannot be used,
        which is the signal for the caller to take its deterministic path."""
        if not self.use_llm():
            return None
        try:
            self.ledger.check(self.name)
            response = self.llm.complete(request)
        except (LLMUnavailable, BudgetExceeded):
            return None
        except Exception:
            # A provider bug must not take the run down; fluency degrades only.
            return None
        self.ledger.record(self.name, response.model, response.usage)
        return response

    def failed(self, code: str, detail: str, summary: str = "") -> AgentResult[Any]:
        return AgentResult(agent=self.name, reasoning_summary=summary,
                           failure=AgentFailure(code=code, detail=detail,
                                                agent=self.name))
