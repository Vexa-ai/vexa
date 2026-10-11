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
        problems += common.no_effort_control(KIND, models)
        for m in models:
            if common.declared_efforts(m) or (m.get("capabilities") or {}).get("default_effort"):
                problems.append(f"model {m.get('id')!r}: effort on a custom entry is the person's "
                                "own setting (Settings → Models), not the catalog's")
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

    def efforts(self, provider: Mapping, model: Mapping) -> tuple[str, ...]:
        return ()           # the person's own Settings → Models effort, never a chat pick

    def route(self, model: Mapping, provider_key: str, provider: Mapping,
              ctx: RouteContext, effort: str = "") -> ModelRoute:
        cfg = dict(ctx.subject_config or {})
        model_id = str(model["id"])
        common.check_effort(effort, model, provider_key, ())
        base_url = model_endpoint.custom_base_url(cfg)
        if not base_url:
            raise ModelChoiceFault(
                NOT_CONFIGURED, model=model_id, provider=provider_key,
                detail="This model runs on your own endpoint, and you have not set one.",
                remedy="Set a custom endpoint under Settings → Models, or pick another model.")
        harness = self.harness(provider, ctx)
        key = str(cfg.get("api_key") or "").strip()
        # THE SAME RULE THE PRE-CATALOG ROUTE ASKS (`model_endpoint.route_refusal`): the operator's
        # gate on the endpoint, and on claude-code the person's own key — that CLI signs in from its
        # config directory when it has none, and anything there is the deployment's.
        refusal = ctx.endpoint_refusal(base_url, key, harness)
        if refusal:
            raise ModelChoiceFault(
                ENDPOINT_REFUSED, model=model_id, provider=provider_key,
                detail=f"Your endpoint cannot carry this turn: {refusal}.",
                remedy="Fix it under Settings → Models, or pick another model.")
        own = str(cfg.get("model") or "").strip()
        return ModelRoute(
            model_id=model_id, provider=provider_key, adapter=KIND,
            harness=harness, base_url=base_url,
            credential_source=CRED_SUBJECT, credential=key,
            provider_model=own if own and ctx.model_allowed(own) else ctx.deployment_model,
            extra_body=str(cfg.get("extra_body") or "").strip(),
            capabilities=common.capabilities(model),
            effort=str(cfg.get("effort") or "").strip(),
            max_output_tokens=common.max_output_tokens(model))
