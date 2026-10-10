"""openai_compatible.py — any OpenAI-compatible endpoint the operator runs or names: a self-hosted
vLLM or llama.cpp serving Qwen 3, a LiteLLM proxy, an inference gateway. Driven by the openai-agent
harness (this repo's own loop over ``POST {base_url}/chat/completions``).

Declaration: ``base_url`` (required, the OpenAI root, usually ending in ``/v1``), ``auth`` ``none`` or
``secret`` (+ ``secret_ref``), ``extra_body`` for server-specific request fields (a Qwen behind vLLM
needs ``{"chat_template_kwargs": {"enable_thinking": false}}``)."""
from __future__ import annotations

from typing import Mapping, Optional

from control_plane.model_providers.adapters import common
from control_plane.model_providers.port import (
    CRED_NONE, CRED_SECRET, ModelRoute, RouteContext,
)

KIND = "openai_compatible"
HARNESS = "openai-agent"


class OpenAICompatibleAdapter:
    kind = KIND
    fields = ("base_url", "auth", "secret_ref", "extra_body", "harness")

    def check(self, provider: Mapping, models: list[Mapping]) -> list[str]:
        problems = common.unexpected_fields(KIND, provider, self.fields)
        if not str(provider.get("base_url") or "").strip():
            problems.append("'base_url' is required (the endpoint's OpenAI-compatible root)")
        if common.auth_of(provider, default="none") not in ("none", "secret"):
            problems.append("'auth' must be none or secret")
        if provider.get("harness") not in (None, HARNESS):
            problems.append(f"'harness' must be {HARNESS} (the only harness that speaks this dialect)")
        problems += [p for m in models if (p := common.needs_model(m))]
        return problems

    def harness(self, provider: Mapping, ctx: Optional[RouteContext] = None) -> str:
        return HARNESS

    def available(self, ctx: RouteContext) -> bool:
        return True

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext) -> ModelRoute:
        secret = common.auth_of(provider, default="none") == "secret"
        return ModelRoute(
            model_id=str(model["id"]), provider=provider_key, adapter=KIND, harness=HARNESS,
            base_url=str(provider["base_url"]).rstrip("/"),
            credential_source=CRED_SECRET if secret else CRED_NONE,
            credential=(common.secret_value(str(model["id"]), provider_key, provider, ctx)
                        if secret else ""),
            provider_model=str(model["model"]), extra_body=common.extra_body(provider, model),
            capabilities=common.capabilities(model))
