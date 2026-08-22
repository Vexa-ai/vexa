"""openai_compat.py — the DEFAULT CompletionPort adapter: any OpenAI-compatible endpoint.

One dialect covers nearly every provider — OpenRouter, Ollama, vLLM, LM Studio, OpenAI itself, and
most gateways all speak ``POST {base}/chat/completions``. Raw httpx, no vendor SDK: the request is
~10 lines and a pinned SDK would be a heavier supply-chain surface than the protocol itself.

Config (constructor args win over env): ``VEXA_LLM_BASE_URL`` (required — e.g.
``https://openrouter.ai/api/v1``, ``http://ollama:11434/v1``; falls back to ``ANTHROPIC_BASE_URL``
for deployments that already point one at a multi-protocol gateway), ``VEXA_LLM_API_KEY`` (falls
back ``ANTHROPIC_AUTH_TOKEN`` → ``ANTHROPIC_API_KEY``; optional — local runtimes need none),
``VEXA_LLM_MODEL`` (the deployment-default model), ``VEXA_LLM_EXTRA_BODY`` (a JSON object merged
into every request body — the escape hatch for server-specific parameters the OpenAI dialect has no
field for).

``VEXA_LLM_EXTRA_BODY`` exists because some servers make a non-standard parameter LOAD-BEARING. The
worked case: a self-hosted vLLM serving Qwen returns **0% valid JSON** in thinking mode — the model
spends its whole token budget reasoning — and 100% with
``{"chat_template_kwargs": {"enable_thinking": false}}``. Without a passthrough, such a deployment
cannot be used for structured output at all. Reserved keys (``model``, ``messages``) are never
overridden.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx

from llm.errors import LLMAuthError, LLMConfigError, LLMError
from llm.ports import CompletionResult


def _parse_extra_body(raw: object) -> dict:
    """Parse ``VEXA_LLM_EXTRA_BODY``. A malformed value is a CONFIG error, never a silent no-op:
    a deployment that believes it disabled thinking and did not would fail as bad output, far
    from the cause."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(str(raw))
    except ValueError as exc:
        raise LLMConfigError(f"VEXA_LLM_EXTRA_BODY is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMConfigError("VEXA_LLM_EXTRA_BODY must be a JSON object")
    return parsed


class OpenAICompatCompletion:
    name = "openai-compat"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 120.0,
                 extra_body: Optional[dict] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self._key = (api_key or os.environ.get("VEXA_LLM_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                     or os.environ.get("ANTHROPIC_API_KEY") or "")
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        self._extra = _parse_extra_body(extra_body if extra_body is not None
                                        else os.environ.get("VEXA_LLM_EXTRA_BODY"))
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 model: Optional[str] = None) -> CompletionResult:
        target = (model or "").strip() or self._model
        if not self._base:
            raise LLMConfigError(
                "no completion endpoint: set VEXA_LLM_BASE_URL (e.g. https://openrouter.ai/api/v1, "
                "http://ollama:11434/v1) — the openai-compat provider has no default host"
            )
        if not target:
            raise LLMConfigError(
                "no model: set VEXA_LLM_MODEL (deployment default) or a model in the workspace's "
                "agents/meeting.md"
            )
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            body = {**self._extra, "model": target, "messages": messages}  # reserved keys always win
            r = self._client.post(f"{self._base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"completion transport failure against {self._base}: {exc}") from exc
        if r.status_code in (401, 403):
            raise LLMAuthError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        try:
            choice = (r.json().get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed completion payload from {self._base}: {exc}") from exc
        return CompletionResult(text=str(text), model=target)
