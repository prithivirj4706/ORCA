"""Provider-agnostic LLM interface.

19_ENVIRONMENT_AND_CONFIGURATION_SPEC.md section 3.3: the provider and model
identifiers are CONFIGURATION. No provider name is hard-coded in the reasoning
layer, so providers are resolved through a registry keyed by ORCA_LLM_PROVIDER
and imported lazily -- an uninstalled SDK is not an import error.

The important property for ORCA is that `available` is False when nothing is
configured. Every agent has a deterministic path that the specification already
requires as its fallback (06 sections 3.8, 6.7, 7.8), so an unconfigured
deployment produces a complete, grounded, less fluent answer rather than no
answer. LLM availability changes fluency, never a number and never a verdict.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


class LLMUnavailable(Exception):
    """No model is configured, or the configured one could not be reached.

    Agents catch this and take their deterministic path; it is never allowed to
    cross a graph node boundary (06 section 2).
    """


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.tokens_in + other.tokens_in,
                     self.tokens_out + other.tokens_out)

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class LLMRequest:
    """A single constrained model call.

    `template_id` and `template_version` are recorded in the run trace so a run
    can be reproduced (06 section 3.9). `schema` constrains the output; when it
    is set the caller expects `LLMResponse.parsed` to validate against it.
    """
    template_id: str
    template_version: str
    system: str
    user: str
    schema: dict[str, Any] | None = None
    max_tokens: int = 1024
    temperature: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    template_id: str
    template_version: str
    usage: Usage = field(default_factory=Usage)
    parsed: dict[str, Any] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    @property
    def available(self) -> bool: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class UnavailableProvider:
    """The provider used when none is configured.

    This is a first-class supported mode, not a stub: ORCA runs end to end
    without a model, using deterministic planning and template reporting.
    """

    name = "unavailable"
    model = "none"

    def __init__(self, reason: str = "ORCA_LLM_PROVIDER is not configured"):
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailable(self.reason)


#: provider key -> loader. Loaders import their SDK lazily so that a provider
#: whose package is absent costs nothing until it is actually selected.
_REGISTRY: dict[str, Callable[[dict[str, str]], Any]] = {}


def register_provider(key: str, loader: Callable[[dict[str, str]], Any]) -> None:
    _REGISTRY[key.lower()] = loader


def _load_builtin_providers() -> None:
    """Register built-in keys. The SDK import happens inside each loader, so a
    provider whose package is absent is only a problem if it is selected."""

    def _anthropic(env: dict[str, str]):
        from .providers import anthropic_provider
        return anthropic_provider.build(env)

    register_provider("anthropic", _anthropic)


def resolve_provider(env: dict[str, str] | None = None) -> LLMProvider:
    """Resolve the configured provider, or an UnavailableProvider.

    Never raises: a misconfiguration degrades to the deterministic path with the
    reason recorded, because a missing model must not take the system down.
    """
    env = dict(os.environ if env is None else env)
    key = (env.get("ORCA_LLM_PROVIDER") or "").strip()
    if not key or key == "CHANGE_ME":
        return UnavailableProvider()
    if not _REGISTRY:
        _load_builtin_providers()
    loader = _REGISTRY.get(key.lower())
    if loader is None:
        return UnavailableProvider(f"unknown ORCA_LLM_PROVIDER {key!r}; "
                                   f"known: {sorted(_REGISTRY) or ['(none)']}")
    try:
        return loader(env)
    except Exception as exc:                      # SDK missing, bad key, ...
        return UnavailableProvider(f"provider {key!r} could not be initialised: {exc}")
