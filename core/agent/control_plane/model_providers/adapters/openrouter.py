"""openrouter.py — OpenRouter: any model it serves, on either harness, whichever the provider
declares.

* ``harness: openai-agent`` (the default) — this repo's loop over OpenRouter's OpenAI-compatible
  ``https://openrouter.ai/api/v1``. Works for any model OpenRouter serves with tool calling.
* ``harness: claude-code`` — the claude CLI against OpenRouter's Anthropic-compatible
  ``https://openrouter.ai/api``, with the key as the bearer token. Every model tier the CLI names
  (``ANTHROPIC_DEFAULT_*_MODEL``) is pinned to the chosen model, so no background call asks
  OpenRouter for a model id it does not serve.

The endpoint is fixed by the adapter: a provider that is not OpenRouter is an ``openai_compatible``
one. The key is required — OpenRouter has no keyless access, and a keyless claude-code route would
let the CLI fall back to the deployment's mounted subscription file."""
from __future__ import annotations

from typing import Mapping, Optional

from control_plane.model_providers.adapters import common
from control_plane.model_providers.port import CRED_SECRET, ModelRoute, RouteContext

KIND = "openrouter"
#: OpenRouter's ``reasoning.effort`` levels, sent on the openai-agent harness. On claude-code the
#: CLI writes Anthropic's ``output_config.effort`` instead, which this adapter cannot vouch that
#: OpenRouter honours for every model it routes — so that harness offers no effort control.
EFFORTS = {"openai-agent": ("none", "minimal", "low", "medium", "high", "xhigh"),
           "claude-code": ()}
ENDPOINTS = {"openai-agent": "https://openrouter.ai/api/v1",
             "claude-code": "https://openrouter.ai/api"}
DEFAULT_HARNESS = "openai-agent"


class OpenRouterAdapter:
    kind = KIND
    fields = ("harness", "auth", "secret_ref", "extra_body")

    def check(self, provider: Mapping, models: list[Mapping]) -> list[str]:
        problems = common.unexpected_fields(KIND, provider, self.fields)
        harness = self.harness(provider)
        if harness not in ENDPOINTS:
            problems.append(f"'harness' must be one of {sorted(ENDPOINTS)}")
        if common.auth_of(provider, default="secret") != "secret" or not provider.get("secret_ref"):
            problems.append("OpenRouter needs a key: set auth: secret and secret_ref: env:NAME")
        if harness == "claude-code" and (provider.get("extra_body")
                                         or any(m.get("extra_body") for m in models)):
            problems.append("'extra_body' reaches only the openai-agent harness; this provider "
                            "runs on claude-code, which would not send it")
        problems += [p for m in models if (p := common.needs_model(m))]
        problems += common.no_effort_control(KIND, models)
        if harness in ENDPOINTS:
            for m in models:
                problems += common.effort_problems(
                    KIND, m, EFFORTS[harness],
                    "reasoning.effort" if harness == "openai-agent"
                    else "anything on claude-code (use harness: openai-agent for effort control)")
        return problems

    def harness(self, provider: Mapping, ctx: Optional[RouteContext] = None) -> str:
        return str(provider.get("harness") or DEFAULT_HARNESS)

    def efforts(self, provider: Mapping, model: Mapping) -> tuple[str, ...]:
        return EFFORTS.get(self.harness(provider), ())

    def available(self, ctx: RouteContext) -> bool:
        return True

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext, effort: str = "") -> ModelRoute:
        harness = self.harness(provider)
        effort = common.check_effort(effort, model, provider_key, self.efforts(provider, model))
        body = common.extra_body_dict(provider, model)
        if effort:
            # OpenRouter's own field: https://openrouter.ai/docs — `reasoning: {effort}`.
            body["reasoning"] = {**(body.get("reasoning") or {}), "effort": effort}
        return ModelRoute(
            model_id=str(model["id"]), provider=provider_key, adapter=KIND, harness=harness,
            base_url=ENDPOINTS[harness], credential_source=CRED_SECRET,
            credential=common.secret_value(str(model["id"]), provider_key, provider, ctx),
            provider_model=str(model["model"]),
            extra_body=common.body_text(body) if harness == "openai-agent" else "",
            capabilities=common.capabilities(model), effort=effort,
            max_output_tokens=common.max_output_tokens(model))
