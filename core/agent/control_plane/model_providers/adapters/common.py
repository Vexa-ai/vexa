"""common.py — the few rules every adapter applies the same way: which declaration fields a kind
takes, a model's capabilities, the extra body a turn sends, and a secret reference resolved.

Each is a function rather than a base class: an adapter is a small table plus a ``route``, and reads
best as one module with nothing inherited."""
from __future__ import annotations

import json
from typing import Mapping, Optional

from control_plane.model_providers.port import (
    CREDENTIAL_MISSING, Capabilities, ModelChoiceFault, RouteContext,
)


def unexpected_fields(kind: str, provider: Mapping, allowed: tuple[str, ...]) -> list[str]:
    """A declaration field this adapter does not take is refused, never ignored: a field the
    operator wrote and nothing reads is a setting that silently does nothing."""
    extra = sorted(k for k in provider if k != "adapter" and k not in allowed)
    return [f"provider field {k!r} is not used by the {kind} adapter (it takes: "
            f"{', '.join(allowed) or 'nothing'})" for k in extra]


def capabilities(model: Mapping) -> Capabilities:
    caps = model.get("capabilities") or {}
    ctx = caps.get("context_tokens")
    return Capabilities(tool_calling=bool(caps.get("tool_calling", True)),
                        streaming=bool(caps.get("streaming", True)),
                        context_tokens=int(ctx) if isinstance(ctx, int) else None)


def extra_body(provider: Mapping, model: Mapping) -> str:
    """The provider's extra body with the model's own fields over it, as JSON text ("" = none)."""
    merged = {**(provider.get("extra_body") or {}), **(model.get("extra_body") or {})}
    return json.dumps(merged, separators=(",", ":"), sort_keys=True) if merged else ""


def auth_of(provider: Mapping, *, default: str) -> str:
    """The provider's ``auth``, or the kind's default: ``secret`` when a reference is declared."""
    auth = provider.get("auth")
    if auth:
        return str(auth)
    return "secret" if provider.get("secret_ref") else default


def secret_value(model_id: str, provider_key: str, provider: Mapping,
                 ctx: RouteContext) -> str:
    """The value the provider's ``secret_ref`` names. Missing is a typed refusal that names the
    REFERENCE — the value is never in a message — and never a request sent without a key."""
    ref = str(provider.get("secret_ref") or "")
    value = ctx.secret(ref) if ref else ""
    if not value:
        raise ModelChoiceFault(
            CREDENTIAL_MISSING, model=model_id, provider=provider_key,
            detail=f"The credential for {provider_key} ({ref or 'no secret_ref'}) is not set on "
                   "this deployment.",
            remedy="An operator must provide that secret to agent-api, or pick another model.")
    return value


def needs_model(model: Mapping) -> Optional[str]:
    """Every non-custom model names its id at the provider."""
    if not str(model.get("model") or "").strip():
        return f"model {model.get('id')!r}: 'model' (the id at the provider) is required"
    return None
