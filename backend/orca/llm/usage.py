"""Token accounting and budget enforcement (06 section 2, 07 section 14).

A budget overrun is a structured failure, not a hang and not a silent
truncation. The ledger is per-run and is reported in the run trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .provider import Usage


class BudgetExceeded(Exception):
    """Raised inside an agent, converted to a structured failure at its edge."""


@dataclass
class UsageLedger:
    """Accumulates model usage across every LLM node in one run."""

    token_budget: int | None = None
    calls: list[dict] = field(default_factory=list)
    total: Usage = field(default_factory=Usage)

    def record(self, node: str, model: str, usage: Usage) -> None:
        self.total = self.total + usage
        self.calls.append({"node": node, "model": model,
                           "tokens_in": usage.tokens_in,
                           "tokens_out": usage.tokens_out})

    def check(self, node: str) -> None:
        if self.token_budget is not None and self.total.total > self.token_budget:
            raise BudgetExceeded(
                f"run token budget {self.token_budget} exceeded at {node} "
                f"({self.total.total} used)")

    def summary(self) -> dict:
        return {"tokens_in": self.total.tokens_in, "tokens_out": self.total.tokens_out,
                "llm_calls": len(self.calls)}
