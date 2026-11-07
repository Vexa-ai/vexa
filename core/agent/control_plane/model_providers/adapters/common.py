"""common.py — the few rules every adapter applies the same way: which declaration fields a kind
takes, a model's capabilities, the extra body a turn sends, and a secret reference resolved.

Each is a function rather than a base class: an adapter is a small table plus a ``route``, and reads
best as one module with nothing inherited."""
from __future__ import annotations

import json
from typing import Mapping, Optional

from control_plane.model_providers.port import (
    CREDENTIAL_MISSING, EFFORT_UNSUPPORTED, Capabilities, ModelChoiceFault, RouteContext,
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
    efforts = caps.get("reasoning_efforts")
    default = caps.get("default_effort")
    return Capabilities(tool_calling=bool(caps.get("tool_calling", True)),
                        streaming=bool(caps.get("streaming", True)),
                        context_tokens=int(ctx) if isinstance(ctx, int) else None,
                        reasoning_efforts=tuple(str(e) for e in efforts) if isinstance(efforts, list) else (),
                        default_effort=str(default) if isinstance(default, str) else None)


def extra_body_dict(provider: Mapping, model: Mapping) -> dict:
    """The provider's extra body with the model's own fields over it."""
    return {**(provider.get("extra_body") or {}), **(model.get("extra_body") or {})}


def body_text(body: Mapping) -> str:
    """An extra body as the JSON text a route carries ("" = none)."""
    return json.dumps(dict(body), separators=(",", ":"), sort_keys=True) if body else ""


def extra_body(provider: Mapping, model: Mapping) -> str:
    """The provider's extra body with the model's own fields over it, as JSON text ("" = none)."""
    return body_text(extra_body_dict(provider, model))


def max_output_tokens(model: Mapping) -> Optional[int]:
    cap = model.get("max_output_tokens")
    return int(cap) if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None


def declared_efforts(model: Mapping) -> tuple[str, ...]:
    caps = model.get("capabilities") or {}
    efforts = caps.get("reasoning_efforts") if isinstance(caps, Mapping) else None
    return tuple(str(e) for e in efforts) if isinstance(efforts, list) else ()


def effort_problems(kind: str, model: Mapping, expressible: tuple[str, ...], how: str) -> list[str]:
    """What is wrong with one model's effort declaration on an adapter that can express
    ``expressible`` (sent as ``how``). A level it cannot send is refused at boot, never offered."""
    efforts = declared_efforts(model)
    caps = model.get("capabilities") or {}
    default = caps.get("default_effort") if isinstance(caps, Mapping) else None
    mid = model.get("id")
    out: list[str] = []
    if default is not None and not efforts:
        out.append(f"model {mid!r}: default_effort is set but reasoning_efforts is not")
    elif default is not None and default not in efforts:
        out.append(f"model {mid!r}: default_effort {default!r} is not one of its reasoning_efforts")
    bad = [e for e in efforts if e not in expressible]
    if bad:
        can = ", ".join(expressible) if expressible else "none at all"
        out.append(f"model {mid!r}: the {kind} adapter cannot send effort level(s) "
                   f"{', '.join(bad)}{f' as {how}' if how else ''} (it can send: {can})")
    return out


def check_effort(effort: str, model: Mapping, provider_key: str,
                 expressible: tuple[str, ...]) -> str:
    """The effort a turn runs at, checked: ``""`` (the provider's default) or a level this model
    offers AND its adapter can express. Anything else is a typed refusal, never a level dropped."""
    effort = str(effort or "").strip()
    if not effort:
        return ""
    offered = declared_efforts(model)
    name = str(model.get("display_name") or model.get("id") or "")
    if effort not in offered or effort not in expressible:
        levels = [e for e in offered if e in expressible]
        raise ModelChoiceFault(
            EFFORT_UNSUPPORTED, model=str(model.get("id") or ""), provider=provider_key,
            detail=(f"{name} offers the effort levels {', '.join(levels)} — not {effort}."
                    if levels else f"{name} has no effort control."),
            remedy="Pick one of those levels, or another model." if levels
            else "Leave the effort unset for this model, or pick another model.")
    return effort


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


def no_effort_control(kind: str, models: list[Mapping]) -> list[str]:
    """``effort_control`` chooses between two OpenAI-dialect fields; on any other adapter it is a
    setting nothing reads, and so is refused."""
    return [f"model {m.get('id')!r}: 'effort_control' is read only by the openai_compatible "
            f"adapter, not {kind}" for m in models if "effort_control" in m]
