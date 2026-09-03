"""Anthropic provider.

The SDK is imported inside `build` so that ORCA runs, and its tests pass, with
no LLM package installed at all. Selecting a provider whose SDK is absent
degrades to the deterministic path with the reason recorded.
"""
from __future__ import annotations

import json
from typing import Any

from ..provider import LLMRequest, LLMResponse, LLMUnavailable, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any, model: str, timeout_s: float = 30.0):
        self._client = client
        self.model = model
        self._timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        # A schema-constrained call is expressed as a single forced tool, which
        # is the provider's mechanism for guaranteeing parseable structure.
        if request.schema is not None:
            kwargs["tools"] = [{"name": "emit", "description": "Return the result.",
                                "input_schema": request.schema}]
            kwargs["tool_choice"] = {"type": "tool", "name": "emit"}
        try:
            msg = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMUnavailable(f"anthropic call failed: {exc}") from exc

        text, parsed = "", None
        for block in getattr(msg, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text += getattr(block, "text", "")
            elif btype == "tool_use":
                parsed = getattr(block, "input", None)
        if request.schema is not None and parsed is None and text:
            try:                                  # defensive: model answered in prose
                parsed = json.loads(text)
            except ValueError:
                parsed = None

        u = getattr(msg, "usage", None)
        usage = Usage(getattr(u, "input_tokens", 0) or 0,
                      getattr(u, "output_tokens", 0) or 0)
        return LLMResponse(text=text, model=self.model, provider=self.name,
                           template_id=request.template_id,
                           template_version=request.template_version,
                           usage=usage, parsed=parsed)


def build(env: dict[str, str]) -> AnthropicProvider:
    import anthropic                              # noqa: PLC0415 -- deliberate

    key = env.get("ORCA_LLM_API_KEY") or ""
    if not key or key == "CHANGE_ME":
        raise LLMUnavailable("ORCA_LLM_API_KEY is not set")
    model = env.get("ORCA_LLM_MODEL_PLANNER") or "claude-sonnet-5"
    timeout_s = float(env.get("ORCA_LLM_TIMEOUT_S") or 30)
    base_url = env.get("ORCA_LLM_BASE_URL") or None
    client = anthropic.Anthropic(
        api_key=key, timeout=timeout_s,
        max_retries=int(env.get("ORCA_LLM_MAX_RETRIES") or 2),
        **({"base_url": base_url} if base_url else {}))
    return AnthropicProvider(client, model, timeout_s)
