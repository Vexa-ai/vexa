"""anthropic_api.py — CompletionPort adapter for the Anthropic Messages API (and compatibles).

For deployments that point completions directly at ``api.anthropic.com`` — or at any endpoint
speaking the Messages dialect (LiteLLM proxies, DeepSeek/GLM/Kimi Anthropic-compatible endpoints).
Raw httpx, no vendor SDK (same doctrine as ``openai_compat``). Named ``anthropic_api`` to never
shadow the ``anthropic`` pip package.

Config (constructor args win over env): ``VEXA_LLM_BASE_URL`` (default ``https://api.anthropic.com``),
``VEXA_LLM_API_KEY`` (falls back ``ANTHROPIC_AUTH_TOKEN`` → ``ANTHROPIC_API_KEY``),
``VEXA_LLM_MODEL``, ``VEXA_LLM_MAX_TOKENS`` (the Messages API requires max_tokens; default 4096),
``VEXA_LLM_EXTRA_HEADERS`` (optional ``Name: Value`` lines for a gateway that requires its own
session/routing header — Vexa-ai/vexa#1667).

401/403 is TERMINAL and never retried, and the 2xx body is parsed STRICTLY (``llm.dialects``):
an endpoint answering the OpenAI ``chat.completion`` shape at ``/v1/messages`` fails by name
instead of yielding an empty completion (Vexa-ai/vexa#1666).
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

import httpx

from llm.dialects import anthropic_text, parse_headers
from llm.errors import LLMAuthError, LLMConfigError, LLMError
from llm.ports import CompletionResult

_DEFAULT_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


def _max_tokens() -> int:
    try:
        return int(os.environ.get("VEXA_LLM_MAX_TOKENS", "4096"))
    except ValueError:
        return 4096


class AnthropicCompletion:
    name = "anthropic"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 120.0,
                 extra_headers: Optional[Mapping] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or _DEFAULT_BASE).rstrip("/")
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
        if not target:
            raise LLMConfigError(
                "no model: set VEXA_LLM_MODEL (deployment default) or a model in the workspace's "
                "agents/meeting.md"
            )
        payload: dict = {
            "model": target,
            "max_tokens": _max_tokens(),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        headers = dict(self._extra)  # provider-required extras first — auth below always wins
        headers.update({"x-api-key": self._key, "anthropic-version": _API_VERSION})
        try:
            r = self._client.post(f"{self._base}/v1/messages", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"completion transport failure against {self._base}: {exc}") from exc
        if r.status_code in (401, 403):
            # TERMINAL — no retry. Re-sending the same credential to the same endpoint cannot
            # change the answer; the loop it used to feed is the ~70s hang of #1666.
            raise LLMAuthError(
                f"{r.status_code} from {self._base}/v1/messages — the endpoint rejected the "
                f"credential sent as 'x-api-key' (anthropic dialect). Not retried: reconcile "
                f"the key with the endpoint (VEXA_LLM_API_KEY / VEXA_LLM_BASE_URL, or Settings "
                f"\u2192 Models). Body: {r.text[:300]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        text = anthropic_text(r.text, base=self._base,
                              content_type=r.headers.get("content-type", ""))
        return CompletionResult(text=text, model=target)
