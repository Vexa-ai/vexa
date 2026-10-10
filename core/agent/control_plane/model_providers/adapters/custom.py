"""custom.py — the person's own endpoint, from their Settings → Models (``mode: custom``).

This is the per-person route that existed before the catalog, offered as one catalog entry. The
operator declares it (``{"adapter": "custom"}``, nothing else) and decides who may pick it; each
person supplies the endpoint, key, model, extra body and harness under Settings → Models, and the
gate is unchanged: the endpoint must pass ``VEXA_MODEL_BASE_URL_ALLOW`` (``model_endpoint``), the
person's model must pass ``VEXA_MODEL_ALLOWLIST``, and the person's key — the empty string included
— is the only credential that endpoint receives. The entry is offered only to a person who has an
endpoint set.

What differs from the pre-catalog path is the refusal: chosen explicitly, an endpoint the gate
refuses is a typed fault and no turn, rather than a turn that silently runs on the deployment's
own model."""
from __future__ import annotations

from typing import Mapping, Optional

from control_plane import model_endpoint
from control_plane.model_providers.adapters import common
from control_plane.model_providers.port import (
    CRED_SUBJECT, ENDPOINT_REFUSED, HARNESSES, NOT_CONFIGURED, ModelChoiceFault, ModelRoute,
    RouteContext,
)

KIND = "custom"


class CustomAdapter:
    kind = KIND
    fields: tuple[str, ...] = ()

    def check(self, provider: Mapping, models: list[Mapping]) -> list[str]:
        problems = common.unexpected_fields(KIND, provider, self.fields)
        for m in models:
            for f in ("model", "extra_body"):
                if f in m:
                    problems.append(f"model {m.get('id')!r}: {f!r} is the person's own setting on a "
                                    "custom entry (Settings → Models), not the catalog's")
        return problems

    def harness(self, provider: Mapping, ctx: Optional[RouteContext] = None) -> str:
        runner = str(((ctx.subject_config if ctx else {}) or {}).get("runner") or "").strip()
        if runner in HARNESSES:
            return runner
        return ctx.deployment_runner if ctx else "claude-code"

    def available(self, ctx: RouteContext) -> bool:
        return model_endpoint.has_custom_endpoint(dict(ctx.subject_config or {}))

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext) -> ModelRoute:
        cfg = dict(ctx.subject_config or {})
        model_id = str(model["id"])
        base_url = model_endpoint.custom_base_url(cfg)
        if not base_url:
            raise ModelChoiceFault(
                NOT_CONFIGURED, model=model_id, provider=provider_key,
                detail="This model runs on your own endpoint, and you have not set one.",
                remedy="Set a custom endpoint under Settings → Models, or pick another model.")
        refusal = ctx.endpoint_refusal(base_url)
        if refusal:
            raise ModelChoiceFault(
                ENDPOINT_REFUSED, model=model_id, provider=provider_key,
                detail=f"Your endpoint is not allowed on this deployment: {refusal}.",
                remedy="Point Settings → Models at an allowed endpoint, or pick another model.")
        harness = self.harness(provider, ctx)
        key = str(cfg.get("api_key") or "").strip()
        if harness == "claude-code" and not key:
            # The claude CLI on an endpoint with no key of its own falls back to whatever credential
            # its home holds, and that is the deployment's, never the person's. The openai-agent
            # harness has no such fallback, so a keyless endpoint is fine there.
            raise ModelChoiceFault(
                NOT_CONFIGURED, model=model_id, provider=provider_key,
                detail="Your endpoint has no API key, and the claude-code harness needs one.",
                remedy="Add the endpoint's key under Settings → Models, choose the openai-agent "
                       "harness, or pick another model.")
        own = str(cfg.get("model") or "").strip()
        return ModelRoute(
            model_id=model_id, provider=provider_key, adapter=KIND,
            harness=harness, base_url=base_url,
            credential_source=CRED_SUBJECT, credential=key,
            provider_model=own if own and ctx.model_allowed(own) else ctx.deployment_model,
            extra_body=str(cfg.get("extra_body") or "").strip(),
            capabilities=common.capabilities(model))
