"""model_router.py — which model a task runs on: rules inside the company's policy, and levels 1–5.

Router v0 (PLAN-8H 6.3, architecture decision G1). Two pieces, both read live from `_global/POLICIES.md`:

RULES — a task type → a model. The task types are ``chat``, ``routines``, ``events`` and ``home``.
``model_rules`` (a flow map, ``{routines: cheapest, events: 'level:2'}``) overlays the per-task keys
that already exist — ``model_chat_default``, ``model_routines``, ``model_event_agents``, ``model_home``
— so an install that never sets it routes exactly as before. A rule's value is ``cheapest`` (the
allowed model with the lowest market price, ``model_prices``), a catalog id, or ``level:N``. Every
answer is inside ``models_allowed``: a rule naming a model the policy does not allow routes to nothing
(the person's default applies), never around the policy.

LEVELS — 1 fastest … 5 most capable, for hosted plans, where people pick a level rather than a model
(the ``levels`` feature, off by default; self-host and app.dev keep the full picker). ``model_levels``
(``{1: <id>, …, 5: <id>}``) is the company's table; a level it leaves out, or fills with a model it
does not allow, is filled from the allowed models ordered by market price, cheapest first, spread over
the five levels. A chat's level is chosen once at its start and kept with the chat (prompt caching):
``POST /api/chat/level`` refuses to change a chat that already has one unless asked to switch.

This module decides; it stores nothing. The chat's pick is written by the one writer of a chat's
model (``api_shared.SessionIndex.set_model``).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

TASKS = ("chat", "routines", "events", "home")
LEGACY = {"chat": "model_chat_default", "routines": "model_routines", "events": "model_event_agents",
          "home": "model_home"}
LEVELS = (1, 2, 3, 4, 5)
WORDS = {1: "fastest", 2: "fast", 3: "balanced", 4: "thorough", 5: "most capable"}


def parse_map(raw: Any) -> dict:
    """``{a: b, c: 'd'}`` (one front-matter line) → ``{"a": "b", "c": "d"}``. Junk pairs are dropped."""
    if isinstance(raw, Mapping):
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if str(v).strip()}
    text = str(raw or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    out: dict = {}
    for pair in text.split(","):
        key, sep, val = pair.partition(":")
        key, val = key.strip().strip("'\"").lower(), val.strip().strip("'\"")
        if sep and key and val:
            out[key] = val
    return out


def _allowed(catalog, policy: Mapping[str, Any]) -> list:
    if catalog is None or getattr(catalog, "empty", True):
        return []
    v = str(policy.get("models_allowed") or "all").strip()
    allow = None if v.lower() == "all" else {m.strip() for m in v.split(",") if m.strip()}
    return [i for i in catalog.ids if allow is None or i in allow]


def _served_name(catalog, mid: str) -> str:
    decl = (getattr(catalog, "_by_id", {}) or {}).get(mid) or {}
    return str(decl.get("provider_model") or decl.get("model") or mid)


def _by_price(catalog, ids: list, prices) -> list:
    """``ids`` ordered by market price, cheapest first; a model no table names goes last, in catalog
    order (its price is unknown, so it is never taken for the cheap one)."""
    def key(pair):
        i, mid = pair
        p = (prices.price(_served_name(catalog, mid)) or prices.price(mid)) if prices is not None else None
        return (0, p.prompt + p.completion, i) if p is not None else (1, 0.0, i)
    return [mid for _, mid in sorted(enumerate(ids), key=key)]


def cheapest(catalog, policy: Mapping[str, Any], prices) -> str:
    ids = _allowed(catalog, policy)
    if not ids:
        return ""
    ranked = _by_price(catalog, ids, prices)
    first = ranked[0]
    if prices is not None and (prices.price(_served_name(catalog, first)) or prices.price(first)):
        return first
    # nothing priced: the catalog's own idea of small (the pre-router behaviour)
    from control_plane import home_model  # noqa: PLC0415
    return home_model.pick(catalog, {}, named="", allowed=ids)


def levels(catalog, policy: Mapping[str, Any], prices) -> dict:
    """``{1..5: catalog id}`` — the company's table, filled from the allowed models by price."""
    ids = _allowed(catalog, policy)
    if not ids:
        return {}
    ranked = _by_price(catalog, ids, prices)
    n = len(ranked)
    table = {lv: ranked[round((lv - 1) * (n - 1) / 4)] for lv in LEVELS}
    for k, v in parse_map(policy.get("model_levels")).items():
        try:
            lv = int(k)
        except ValueError:
            continue
        if lv in table and v in ids:
            table[lv] = v
    return table


def level_of(table: Mapping[int, str], model: str) -> Optional[int]:
    """The level a model id is at in ``table`` (the lowest, when it fills several), or None."""
    hits = [lv for lv, mid in table.items() if mid == model]
    return min(hits) if hits else None


def resolve(value: str, catalog, policy: Mapping[str, Any], prices) -> str:
    """One rule value → a catalog id inside the policy, or ``""``."""
    v = str(value or "").strip()
    if not v:
        return ""
    if v.lower() == "cheapest":
        return cheapest(catalog, policy, prices)
    if v.lower().startswith("level:"):
        try:
            return levels(catalog, policy, prices).get(int(v.split(":", 1)[1]), "")
        except ValueError:
            return ""
    return v if v in _allowed(catalog, policy) else ""


def rule(task: str, policy: Mapping[str, Any]) -> str:
    """The rule for a task: ``model_rules`` first, else the task's own key, else ``cheapest`` for
    background work and nothing (the person's default) for chat."""
    task = str(task or "").lower()
    v = parse_map(policy.get("model_rules")).get(task)
    if v:
        return v
    legacy = str(policy.get(LEGACY.get(task, "")) or "").strip()
    if legacy:
        return legacy
    return "" if task == "chat" else "cheapest"


def _legacy(catalog, task: str, policy: Mapping[str, Any]) -> str:
    """The choice before the router, unchanged: the task's own key, else ``cheapest`` as the catalog
    names it (``home_model.pick``: the first small-named model), inside ``models_allowed``."""
    from control_plane import budgets, home_model  # noqa: PLC0415
    want = str(policy.get(LEGACY.get(task, "")) or ("" if task == "chat" else "cheapest")).strip()
    allowed = budgets.allowed_list(dict(policy))
    if not want:
        return ""
    if want != "cheapest":
        return want if want in catalog.ids and (allowed is None or want in allowed) else ""
    return home_model.pick(catalog, {}, named="", allowed=allowed)


def for_task(catalog, task: str, policy: Optional[Mapping[str, Any]], prices=None) -> str:
    """The catalog id ``task`` runs on, or ``""`` (the person's default applies).

    WITHOUT A ``model_rules`` ENTRY FOR THE TASK THE CHOICE IS EXACTLY THE PRE-ROUTER ONE
    (:func:`_legacy`): the router changes nothing on an install until the company writes a rule."""
    pol = dict(policy or {})
    task = str(task or "").lower()
    if not parse_map(pol.get("model_rules")).get(task):
        return _legacy(catalog, task, pol) if catalog is not None and not getattr(catalog, "empty", True) else ""
    if prices is None:
        from control_plane import llm_budget  # noqa: PLC0415
        prices = llm_budget.default_prices()
    return resolve(rule(task, pol), catalog, pol, prices)
