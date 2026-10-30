"""openai_compat.py — the DEFAULT CompletionPort adapter: any OpenAI-compatible endpoint.

One dialect covers nearly every provider — OpenRouter, Ollama, vLLM, LM Studio, OpenAI itself, and
most gateways all speak ``POST {base}/chat/completions``. Raw httpx, no vendor SDK: the request is
~10 lines and a pinned SDK would be a heavier supply-chain surface than the protocol itself.

Config (constructor args win over env): ``VEXA_LLM_BASE_URL`` (required — e.g.
``https://openrouter.ai/api/v1``, ``http://ollama:11434/v1``; falls back to ``ANTHROPIC_BASE_URL``
for deployments that already point one at a multi-protocol gateway), ``VEXA_LLM_API_KEY`` (falls
back ``ANTHROPIC_AUTH_TOKEN`` → ``ANTHROPIC_API_KEY``; optional — local runtimes need none),
``VEXA_LLM_MODEL`` (the deployment-default model), ``VEXA_LLM_EXTRA_HEADERS`` (optional
``Name: Value`` lines — a gateway that wants a session/routing/entitlement header of its own,
Vexa-ai/vexa#1667; the auth header is never overridden from here).

401/403 is TERMINAL here and is never retried: a rejected credential is a configuration fact, not
a transient one, and the retry loop it used to feed is the ~70s "Working" hang of
Vexa-ai/vexa#1666. The 2xx body is parsed STRICTLY (``llm.dialects``) so an endpoint answering the
Anthropic shape at this path fails by name instead of yielding an empty completion.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

import httpx

from llm.dialects import openai_text, parse_headers
from llm.errors import LLMAuthError, LLMConfigError, LLMError
from llm.ports import CompletionResult


class OpenAICompatCompletion:
    name = "openai-compat"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 120.0,
                 extra_headers: Optional[Mapping] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self._key = (api_key or os.environ.get("VEXA_LLM_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                     or os.environ.get("ANTHROPIC_API_KEY") or "")
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        self._extra = parse_headers(extra_headers if extra_headers is not None
                                    else os.environ.get("VEXA_LLM_EXTRA_HEADERS"))
        # retries=0 (httpx's default): a 401 must surface on the FIRST answer, never be re-sent.
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
        headers = dict(self._extra)  # provider-required extras first — auth below always wins
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        try:
            r = self._client.post(f"{self._base}/chat/completions",
                                  json={"model": target, "messages": messages}, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"completion transport failure against {self._base}: {exc}") from exc
        if r.status_code in (401, 403):
            # TERMINAL — no retry, no second dialect. The credential and the endpoint disagree and
            # re-sending the same pair cannot change that (#1666).
            raise LLMAuthError(
                f"{r.status_code} from {self._base}/chat/completions — the endpoint rejected the "
                f"credential sent as 'Authorization: Bearer' (openai dialect). Not retried: "
                f"reconcile the key with the endpoint (VEXA_LLM_API_KEY / VEXA_LLM_BASE_URL, or "
                f"Settings \u2192 Models). Body: {r.text[:300]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        text = openai_text(r.text, base=self._base,
                           content_type=r.headers.get("content-type", ""))
        return CompletionResult(text=text, model=target)
