"""catalog.py — the operator's model catalog: read once at boot, refused whole if anything in it is
wrong, then asked three things per person — which models may I pick, which do I run on before I
pick, and what route does this one take.

THE DECLARATION is ``VEXA_MODEL_CATALOG``, one JSON object validated against
``models.v1#/$defs/Catalog`` (P14): the providers this deployment reaches and the models it offers
on each. Unset or empty, there is no catalog and nothing in this package runs: every turn routes
exactly as it did before the catalog existed (``dispatch.overlay_model_config``).

REFUSED WHOLE, AT BOOT, NAMING EVERY PROBLEM (P14, P18). A catalog that is half-right is one whose
wrong half shows up as a person's turn failing an hour later. So :func:`parse` collects every
problem — the schema's, the adapters', and this module's own — and raises one :class:`CatalogError`
listing them all, without ever repeating a value (a problem names the field; a mis-pasted key
stays out of the log). The rules this module owns:

  * an ``adapter`` is a kind ``adapters.ADAPTERS`` has (the schema's enum is held to that table);
  * no two models share an ``id``; at most one is the ``default``; every model's ``provider`` is
    declared;
  * nothing secret-shaped is written inline — a credential is a ``secret_ref`` and nothing else —
    and every ``secret_ref`` names a ``VEXA_MODEL_SECRET_*`` variable set in agent-api's environment;
  * every model can call tools: each harness is an agent loop, and a model that cannot is one no
    turn could use.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Iterable, Mapping, Optional, Union

from contracts import validate_model_catalog_errors
from control_plane.model_providers.adapters import ADAPTERS, common
from control_plane.model_providers.port import (
    NOT_CONFIGURED, NOT_PERMITTED, UNKNOWN_MODEL, ModelChoiceFault, ModelRoute, RouteContext,
)

logger = logging.getLogger("agent_api.model_catalog")

ENV = "VEXA_MODEL_CATALOG"

#: A dictionary key that names a credential (``api_key``, ``token``, ``x-api-key``,
#: ``client_secret``…). Allowed only as ``secret_ref``. Whole-name, so ``max_tokens`` and
#: ``context_tokens`` — counts, not credentials — are not mistaken for one.
_SECRET_NAME = re.compile(r"^(?:.*[_-])?(?:api[_-]?key|key|token|secret|passw(?:or)?d|"
                          r"authori[sz]ation|bearer|credentials?|cookie)$", re.IGNORECASE)
#: A value that IS a credential: the common key prefixes and a bearer header.
_SECRET_VALUE = re.compile(r"(?:^|[^A-Za-z0-9])(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}|\bbearer\s+\S{8,}|"
                           r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}|\bxox[abprs]-",
                           re.IGNORECASE)


class CatalogError(ValueError):
    """The declared catalog cannot be used. ``problems`` names each one; no value is repeated."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{ENV} is refused — " + "; ".join(problems))


AdminCheck = Union[bool, Callable[[], bool]]


def _path(parts: Iterable) -> str:
    return "/".join(str(p) for p in parts) or "(top level)"


def _schema_problem(err) -> str:
    """A jsonschema error, without the instance value it would otherwise quote — a credential
    pasted where a reference belongs must not reach the boot log through the refusal itself."""
    where = _path(err.absolute_path)
    v = err.validator
    if v in ("additionalProperties", "required"):
        return f"{where}: {err.message}"     # these name keys, never values
    if v in ("enum", "const"):
        return f"{where}: must be one of {err.validator_value!r}"
    if v == "pattern":
        return f"{where}: does not match {err.validator_value}"
    if v == "type":
        return f"{where}: must be of type {err.validator_value}"
    if v in ("minItems", "minProperties", "minLength", "maxLength", "minimum", "maximum"):
        return f"{where}: violates {v} {err.validator_value}"
    return f"{where}: is not valid ({v})"


def _inline_secrets(node, at: list) -> list[str]:
    """Every place a credential is written as a value — named by path, never quoted."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k != "secret_ref" and _SECRET_NAME.search(str(k)):
                out.append(f"{_path(at + [k])}: a credential is never written into the catalog — "
                           "declare it as the provider's secret_ref (env:VEXA_MODEL_SECRET_<NAME>)")
            else:
                out += _inline_secrets(v, at + [k])
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _inline_secrets(v, at + [i])
    elif isinstance(node, str) and _SECRET_VALUE.search(node):
        out.append(f"{_path(at)}: holds a credential-shaped value — declare a secret_ref "
                   "(env:VEXA_MODEL_SECRET_<NAME>) on the provider instead")
    return out


#: The only variables a ``secret_ref`` may name (R1797-1). agent-api's environment holds its own
#: secrets too — the internal API secret, the database URL, the deployment's model credentials — and
#: a catalog entry that could name one would send it, as a bearer token, to whatever endpoint that
#: entry declares. A dedicated prefix keeps the two sets apart by construction, and the Helm chart
#: refuses to deliver anything else. These operator-named variables sit outside
#: `gate:config-contract`'s declared keys on purpose: the prefix is their contract.
SECRET_PREFIX = "VEXA_MODEL_SECRET_"


def secret_from_env(env: Mapping[str, str]) -> Callable[[str], str]:
    """``secret_ref`` → value, read from ``env`` at call time (``env:VEXA_MODEL_SECRET_<NAME>``).
    Unknown schemes, names without the prefix and unset names resolve to ``""``, which the adapter
    turns into a typed refusal."""
    def resolve(ref: str) -> str:
        scheme, _, name = str(ref or "").partition(":")
        if scheme != "env" or not name.startswith(SECRET_PREFIX) or len(name) == len(SECRET_PREFIX):
            return ""
        return (env.get(name) or "").strip()
    return resolve


def parse(raw: Optional[str], env: Optional[Mapping[str, str]] = None) -> "Catalog":
    """The catalog ``raw`` declares, or :class:`CatalogError` naming everything wrong with it.
    ``env`` (default: this process's) is where every ``secret_ref`` must resolve."""
    env = os.environ if env is None else env
    text = (raw or "").strip()
    if not text:
        return Catalog(None)
    try:
        decl = json.loads(text)
    except ValueError as exc:
        raise CatalogError([f"is not valid JSON ({exc.msg} at line {exc.lineno} column "
                            f"{exc.colno})"]) from None
    problems = _inline_secrets(decl, [])
    problems += [_schema_problem(e) for e in validate_model_catalog_errors(decl)]
    # The rules the schema cannot state run whether or not it passed, over whatever is well-shaped,
    # so one refusal names everything wrong rather than the first layer of it.
    body = decl if isinstance(decl, dict) else {}
    providers = {k: p for k, p in (body.get("providers") or {}).items() if isinstance(p, dict)} \
        if isinstance(body.get("providers"), dict) else {}
    models = [m for m in (body.get("models") or []) if isinstance(m, dict)] \
        if isinstance(body.get("models"), list) else []
    seen: set[str] = set()
    for m in models:
        mid = m.get("id")
        if mid in seen:
            problems.append(f"model id {mid!r} is declared twice")
        seen.add(mid)
        if m.get("provider") not in providers:
            problems.append(f"model {mid!r}: provider {m.get('provider')!r} is not declared")
        caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
        if caps.get("tool_calling") is False:
            problems.append(f"model {mid!r}: every harness is an agent loop, so a model without "
                            "tool calling cannot run a turn")
    defaults = [str(m.get("id")) for m in models if m.get("default") is True]
    if len(defaults) > 1:
        problems.append(f"more than one default model: {', '.join(defaults)}")
    resolve = secret_from_env(env)
    for key, p in providers.items():
        adapter = ADAPTERS.get(p.get("adapter"))
        if adapter is None:
            continue                    # the schema's enum has already named it
        on_it = [m for m in models if m.get("provider") == key]
        problems += [f"providers/{key}: {msg}" for msg in adapter.check(p, on_it)]
        ref = p.get("secret_ref")
        if isinstance(ref, str) and (not ref.startswith(f"env:{SECRET_PREFIX}") or ref == f"env:{SECRET_PREFIX}"):
            problems.append(f"providers/{key}: secret_ref must name a variable "
                            f"{SECRET_PREFIX}<NAME> — a catalog may not reach agent-api's own "
                            "secrets")
        elif isinstance(ref, str) and not resolve(ref):
            problems.append(f"providers/{key}: secret_ref {ref} is not set in agent-api's "
                            "environment")
    if problems:
        raise CatalogError(problems)
    return Catalog(decl)


def load(env: Optional[Mapping[str, str]] = None) -> "Catalog":
    """The catalog this process was configured with (``VEXA_MODEL_CATALOG``) — called at boot."""
    env = os.environ if env is None else env
    return parse(env.get(ENV), env)


class Catalog:
    """A parsed, validated catalog. ``Catalog(None)`` is the empty one: no catalog declared."""

    def __init__(self, decl: Optional[dict]) -> None:
        self._providers: dict = dict((decl or {}).get("providers") or {})
        self._models: list = list((decl or {}).get("models") or [])
        self._by_id = {m["id"]: m for m in self._models}

    # ── shape ──────────────────────────────────────────────────────────────────────────────
    @property
    def empty(self) -> bool:
        return not self._models

    @property
    def ids(self) -> list[str]:
        return [m["id"] for m in self._models]

    def has(self, model_id: str) -> bool:
        return model_id in self._by_id

    def _adapter(self, model: Mapping):
        return ADAPTERS[self._providers[model["provider"]]["adapter"]]

    # ── per person ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _admin(admin: AdminCheck) -> bool:
        return bool(admin() if callable(admin) else admin)

    def _permitted(self, model: Mapping, admin: AdminCheck) -> bool:
        return model.get("access", "everyone") != "admins" or self._admin(admin)

    def _usable(self, model: Mapping, ctx: RouteContext, admin: AdminCheck) -> bool:
        return self._permitted(model, admin) and self._adapter(model).available(ctx)

    def visible(self, ctx: RouteContext, *, admin: AdminCheck) -> list[dict]:
        """The models this person may pick, in the catalog's order."""
        return [m for m in self._models if self._usable(m, ctx, admin)]

    def default_for(self, ctx: RouteContext, *, admin: AdminCheck) -> Optional[str]:
        """What this person runs on before they pick: their own ``default_model`` (Settings →
        Models, user over platform), else the catalog's default, else the first model they may use.
        A stored default that is gone or not theirs is skipped and logged — a stale preference
        never stops a turn — and ``None`` means the catalog offers them nothing."""
        mine = str((ctx.subject_config or {}).get("default_model") or "").strip()
        if mine:
            m = self._by_id.get(mine)
            if m is not None and self._usable(m, ctx, admin):
                return mine
            logger.warning("default_model %r is not a model this person may use — the catalog's "
                           "default applies", mine)
        usable = self.visible(ctx, admin=admin)
        flagged = next((m["id"] for m in usable if m.get("default")), None)
        return flagged or (usable[0]["id"] if usable else None)

    def choose(self, explicit: str, ctx: RouteContext, *, admin: AdminCheck) -> dict:
        """The model a turn runs on: ``explicit`` (the chat's own pick) when there is one, else the
        person's default. An explicit pick is never silently replaced — a pick that cannot run is a
        typed refusal, because running some other model is the failure P18 forbids."""
        pick = str(explicit or "").strip()
        if pick:
            m = self._by_id.get(pick)
            if m is None:
                raise ModelChoiceFault(
                    UNKNOWN_MODEL, model=pick,
                    detail=f"The model {pick!r} is not offered on this deployment any more.",
                    remedy="Pick another model for this chat.")
            if not self._permitted(m, admin):
                raise ModelChoiceFault(
                    NOT_PERMITTED, model=pick, provider=m["provider"],
                    detail=f"{m['display_name']} is open to admins only on this deployment.",
                    remedy="Pick another model for this chat.")
            if not self._adapter(m).available(ctx):
                raise ModelChoiceFault(
                    NOT_CONFIGURED, model=pick, provider=m["provider"],
                    detail=f"{m['display_name']} runs on your own endpoint, and you have not set one.",
                    remedy="Set a custom endpoint under Settings → Models, or pick another model.")
            return m
        fallback = self.default_for(ctx, admin=admin)
        if fallback is None:
            raise ModelChoiceFault(
                NOT_CONFIGURED, model="",
                detail="No model on this deployment is open to you.",
                remedy="Ask an operator to offer one in the model catalog.")
        return self._by_id[fallback]

    def effort(self, model: Mapping, picked: str = "") -> str:
        """The effort ``model`` runs at: the chat's own pick, else the entry's ``default_effort``,
        else ``""`` (the provider's own default). Checked, not trusted: a pick the model does not
        offer, or its adapter cannot send, is a typed ``effort_unsupported`` refusal."""
        provider = self._providers[model["provider"]]
        adapter = self._adapter(model)
        caps = model.get("capabilities") or {}
        chosen = str(picked or "").strip() or str(caps.get("default_effort") or "")
        return common.check_effort(chosen, model, model["provider"],
                                   adapter.efforts(provider, model))

    def choose_with_effort(self, explicit: str, effort: str, ctx: RouteContext, *,
                           admin: AdminCheck) -> tuple[dict, str]:
        """``choose`` and ``effort`` together — what a pick is checked against before it is
        stored, so a stored pick is always one the next turn can run."""
        m = self.choose(explicit, ctx, admin=admin)
        return m, self.effort(m, effort)

    def route(self, explicit: str, ctx: RouteContext, *, admin: AdminCheck,
              effort: str = "") -> ModelRoute:
        """The port, end to end: the chosen model at the chosen effort, resolved by its provider's
        adapter."""
        m, level = self.choose_with_effort(explicit, effort, ctx, admin=admin)
        provider = self._providers[m["provider"]]
        return self._adapter(m).route(m, m["provider"], provider, ctx, effort=level)

    def entry(self, model_id: str, ctx: RouteContext) -> Optional[dict]:
        """One model as a person sees it (``models.v1#/$defs/ModelListEntry``), or None."""
        m = self._by_id.get(model_id)
        if m is None:
            return None
        provider = self._providers[m["provider"]]
        adapter = ADAPTERS[provider["adapter"]]
        caps = (m.get("capabilities") or {})
        return {"id": m["id"], "display_name": m["display_name"], "provider": m["provider"],
                "adapter": provider["adapter"], "harness": adapter.harness(provider, ctx),
                "capabilities": {"tool_calling": bool(caps.get("tool_calling", True)),
                                 "streaming": bool(caps.get("streaming", True)),
                                 "context_tokens": caps.get("context_tokens"),
                                 # only levels the adapter can send — the boot check already
                                 # refused a catalog listing any other, so this is belt and braces
                                 "reasoning_efforts": [e for e in common.declared_efforts(m)
                                                       if e in adapter.efforts(provider, m)],
                                 "default_effort": caps.get("default_effort")},
                "access": m.get("access", "everyone"), "default": bool(m.get("default"))}

    def listing(self, ctx: RouteContext, *, admin: AdminCheck,
                selected: Optional[str] = None, with_selected: bool = False,
                selected_effort: Optional[str] = None) -> dict:
        """``models.v1#/$defs/ModelList`` for one person — what ``GET /api/models/catalog`` serves.
        Carries no endpoint, credential or request setting: those never leave agent-api."""
        out: dict = {"models": [self.entry(m["id"], ctx) for m in self.visible(ctx, admin=admin)],
                     "default": self.default_for(ctx, admin=admin)}
        if with_selected:
            out["selected"] = (selected or "").strip() or None
            out["selected_effort"] = (selected_effort or "").strip() or None
        return out
