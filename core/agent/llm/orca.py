"""orca.py — a named CompletionPort adapter for OrcaRouter.

OrcaRouter (https://www.orcarouter.ai) is an OpenAI-compatible router/gateway: one endpoint in
front of routed frontier models, with gateway-level, zero-trust security for AI agents on the same
endpoint. It speaks ``POST {base}/chat/completions`` like ``openai_compat``, but ships a default
host so a deployment opts in with just ``VEXA_LLM_PROVIDER=orcarouter`` (+ key/model) — no
endpoint to type, mirroring the ``anthropic`` adapter's hardcoded default. ``VEXA_LLM_BASE_URL``
still overrides for self-hosted/proxied installs.

Config (constructor args win over env): ``VEXA_LLM_BASE_URL`` (default
``https://api.orcarouter.ai/v1``), ``VEXA_LLM_API_KEY`` (falls back ``ANTHROPIC_AUTH_TOKEN`` →
``ANTHROPIC_API_KEY``), ``VEXA_LLM_MODEL``.
"""
from __future__ import annotations

from typing import Optional

import httpx

from llm.openai_compat import OpenAICompatCompletion

_DEFAULT_BASE = "https://api.orcarouter.ai/v1"


class OrcaRouterCompletion(OpenAICompatCompletion):
    """CompletionPort adapter for the OrcaRouter gateway — a named openai-compat endpoint."""

    name = "orcarouter"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 120.0,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        super().__init__(base_url=base_url, default_base=_DEFAULT_BASE, api_key=api_key,
                         model=model, timeout=timeout, transport=transport)
