"""anthropic.py — Anthropic directly, on the claude-code harness.

* ``auth: secret`` + ``secret_ref`` — an Anthropic API key, sent to ``https://api.anthropic.com`` as
  the API key (``x-api-key``), never as a bearer token.
* ``auth: subscription`` (the default when no ``secret_ref`` is declared) — the deployment's own
  Claude subscription, brokered to the worker exactly as without a catalog (the mounted credential
  file, or ``CLAUDE_CODE_OAUTH_TOKEN``). The route names no endpoint, so the CLI uses its own —
  Anthropic — which is the one place that credential may go, whatever gateway the deployment's
  ``ANTHROPIC_BASE_URL`` otherwise names."""
from __future__ import annotations

from typing import Mapping, Optional

from control_plane.model_providers.adapters import common
from control_plane.model_providers.port import (
    CRED_SECRET, CRED_SUBSCRIPTION, ModelRoute, RouteContext,
)

KIND = "anthropic"
HARNESS = "claude-code"
ENDPOINT = "https://api.anthropic.com"


class AnthropicAdapter:
    kind = KIND
    fields = ("auth", "secret_ref", "harness")

    def check(self, provider: Mapping, models: list[Mapping]) -> list[str]:
        problems = common.unexpected_fields(KIND, provider, self.fields)
        auth = common.auth_of(provider, default="subscription")
        if auth not in ("secret", "subscription"):
            problems.append("'auth' must be secret (an API key) or subscription (the "
                            "deployment's own)")
        if auth == "subscription" and provider.get("secret_ref"):
            problems.append("'secret_ref' is not used with auth: subscription")
        if provider.get("harness") not in (None, HARNESS):
            problems.append(f"'harness' must be {HARNESS}")
        if any(m.get("extra_body") for m in models):
            problems.append("'extra_body' reaches only the openai-agent harness; Anthropic models "
                            "run on claude-code")
        problems += [p for m in models if (p := common.needs_model(m))]
        return problems

    def harness(self, provider: Mapping, ctx: Optional[RouteContext] = None) -> str:
        return HARNESS

    def available(self, ctx: RouteContext) -> bool:
        return True

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext) -> ModelRoute:
        if common.auth_of(provider, default="subscription") == "secret":
            return ModelRoute(
                model_id=str(model["id"]), provider=provider_key, adapter=KIND, harness=HARNESS,
                base_url=ENDPOINT, credential_source=CRED_SECRET,
                credential=common.secret_value(str(model["id"]), provider_key, provider, ctx),
                provider_model=str(model["model"]), extra_body="",
                capabilities=common.capabilities(model), auth_header="x-api-key")
        return ModelRoute(
            model_id=str(model["id"]), provider=provider_key, adapter=KIND, harness=HARNESS,
            base_url="", credential_source=CRED_SUBSCRIPTION, credential="",
            provider_model=str(model["model"]), extra_body="",
            capabilities=common.capabilities(model))
