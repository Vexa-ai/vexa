"""home_model.py — the ONE model call a home composition may make (#1783).

The founder's cost concern, answered in code: the home is ranked by the cheapest model the
operator's catalog offers, at most once per composition, and never per page view. The model only
ORDERS cards it is handed (by id), may mark one wide, nest one in another, word the greeting and name
the jobs it sees — it never writes a card, so whatever it returns is checked against the ids it was
given and anything else is dropped (``home.arrange``).

WHICH MODEL. ``VEXA_HOME_MODEL`` (a catalog id) when the operator names one; otherwise the first
catalog model whose id or provider model reads as a small tier (haiku, mini, flash, nano, small,
lite). An Opus-class model is never used, named or not. A model whose credential is a subscription
(the CLI's, usable only inside a worker) cannot be called from here. With no eligible model the
composition is ranked by the deterministic score alone, and says so (``model: ""``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger("agent_api.home_model")

SMALL = re.compile(r"haiku|mini|flash|nano|small|lite", re.I)
NEVER = re.compile(r"opus", re.I)
MAX_TOKENS = 700
TIMEOUT_S = 20.0

INSTRUCTION = (
    "You arrange a person's home screen. You get candidate cards (id, kind, title, score, facts) and "
    "their lifecycle stage. Reply with ONE JSON object and nothing else: "
    '{"order": [ids, best first, at most 7], "wide": [ids that deserve full width, at most 2], '
    '"attach": [[parent_id, child_id], ...] only when the child is a suggestion about the parent, '
    '"greeting": {"title": "<=60 chars", "text": "<=200 chars, the day\'s state"}, '
    '"left_out": "<=160 chars naming the kinds you left out", '
    '"signals": {"<id>": "<=50 chars, why it ranks there"}, '
    '"jobs": [{"job": "<=100 chars, a job this person is trying to get done", "evidence": [ids]}]}. '
    "Prefer what is time-bound and what a real person is waiting on; at stage new or activating, keep "
    "one or two onboarding (try) cards near the top. Use only the ids given. Plain text, no markdown."
)


def pick(catalog, env: Optional[dict] = None, *, named: str = "", allowed: Optional[list] = None) -> str:
    """The catalog id to rank with, or ``""``. ``named`` is the company's ``model_home`` (``cheapest``
    or a catalog id); ``allowed`` its ``models_allowed`` (None = all). A named model the install does
    not offer falls back to the cheapest — it is shown as unavailable in Company mode, never a crash."""
    if catalog is None or getattr(catalog, "empty", True):
        return ""
    env = os.environ if env is None else env
    ids = [i for i in catalog.ids if allowed is None or i in allowed]
    named = (named if named and named != "cheapest" else (env.get("VEXA_HOME_MODEL") or "")).strip()
    if named and named in ids and not NEVER.search(named):
        return named
    for mid in ids:
        decl = (getattr(catalog, "_by_id", {}) or {}).get(mid) or {}
        text = " ".join(str(decl.get(k) or "") for k in ("display_name", "model", "provider_model")) + " " + mid
        if SMALL.search(text) and not NEVER.search(text):
            return mid
    return ""


def _ctx():
    from control_plane import dispatch  # noqa: PLC0415 — heavy module, only on a compose
    return dispatch.route_context({})


def _post(url: str, body: dict, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — the operator's endpoint
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _json_in(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


EXPAND = (
    "You elaborate ONE item on a person's screen, in place. You get the item as JSON blocks and the "
    "person's question if any. Reply with ONE JSON object and nothing else: {\"blocks\": [...]} using only "
    "these block types: title {text}, text {text, tone: why|plain}, row {text, meta?}, list {rows:[row]}, "
    "evidence {items:[{text, source}]}, group {label, blocks}. At most 8 blocks, plain text, no markdown, "
    "no HTML. Use only what the item says; if it does not say enough, say so in one text block and "
    "suggest what to ask."
)


def _usage_tokens(data: dict) -> int:
    from shared import usage as usage_mod  # noqa: PLC0415
    return usage_mod.tokens_of(data.get("usage") if isinstance(data, dict) else None)


def _usage_of(data: dict, model: str) -> dict:
    """The call's usage in the ledger's shape (``llm_budget.record_local``): the provider's model,
    its prompt (cache reads included) and completion tokens, and its own cost when it gave one."""
    u = (data.get("usage") if isinstance(data, dict) else None) or {}

    def n(*keys) -> int:
        total = 0
        for k in keys:
            v = u.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                total += int(v)
        return total
    out = {"model": model,
           "prompt_tokens": n("prompt_tokens", "input_tokens", "cache_creation_input_tokens",
                              "cache_read_input_tokens"),
           "completion_tokens": n("completion_tokens", "output_tokens"),
           "cache_read_tokens": n("cache_read_input_tokens")}
    if isinstance(u.get("cost"), (int, float)) and not isinstance(u.get("cost"), bool):
        out["cost"] = float(u["cost"])
    return out


def complete_json(catalog, system: str, user: str, *, post: Callable = _post, env: Optional[dict] = None,
                  named: str = "", allowed: Optional[list] = None) -> Optional[dict]:
    """One call to the cheapest eligible model, answered as one JSON object, or ``None``. The
    answer's ``_tokens`` is what the call used, for the budget."""
    mid = pick(catalog, env, named=named, allowed=allowed)
    if not mid:
        return None
    try:
        route = catalog.route(mid, _ctx(), admin=True)
    except Exception:  # noqa: BLE001
        return None
    # a local route (the operator's own server, e.g. a Qwen on the LAN) may need no key; a hosted
    # one without its key is not callable
    if not route.credential and not route.base_url:
        return None
    model = route.provider_model or mid
    base = (route.base_url or "https://api.anthropic.com").rstrip("/")
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base
    if route.auth_header == "x-api-key" or not route.base_url:
        status, body = post(f"{root}/v1/messages", {"model": model, "max_tokens": MAX_TOKENS, "system": system,
                                                    "messages": [{"role": "user", "content": user}]},
                            {"x-api-key": route.credential, "anthropic-version": "2023-06-01"})
        if not 200 <= status < 300:
            return None
        data = json.loads(body)
        text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict))
    else:
        status, body = post(f"{root}/v1/chat/completions", {"model": model, "max_tokens": MAX_TOKENS,
                                                             "messages": [{"role": "system", "content": system},
                                                                          {"role": "user", "content": user}]},
                            {"Authorization": f"Bearer {route.credential}"} if route.credential else {})
        if not 200 <= status < 300:
            return None
        data = json.loads(body)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    out = _json_in(text)
    if out is not None:
        out["_model"] = mid
        out["_tokens"] = _usage_tokens(data)
        out["_usage"] = _usage_of(data, model)
    return out


def ranker(catalog, *, post: Callable = _post, env: Optional[dict] = None, named: str = "",
           allowed: Optional[list] = None) -> tuple[Optional[Callable], str]:
    """(ranker, model id) — ``(None, "")`` when there is no eligible model."""
    mid = pick(catalog, env, named=named, allowed=allowed)
    if not mid:
        return None, ""
    try:
        route = catalog.route(mid, _ctx(), admin=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("home: model %s not routable (%s); ranking without it", mid, type(exc).__name__)
        return None, ""
    if not route.credential:
        return None, ""
    model = route.provider_model or mid
    base = (route.base_url or "https://api.anthropic.com").rstrip("/")
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base
    anthropic = route.auth_header == "x-api-key" or not route.base_url

    def rank(cands: list, stage: str) -> Optional[dict]:
        user = json.dumps({"stage": stage, "candidates": cands}, ensure_ascii=False)
        if anthropic:
            status, body = post(f"{root}/v1/messages",
                                {"model": model, "max_tokens": MAX_TOKENS, "system": INSTRUCTION,
                                 "messages": [{"role": "user", "content": user}]},
                                {"x-api-key": route.credential, "anthropic-version": "2023-06-01"})
            if not 200 <= status < 300:
                return None
            data = json.loads(body)
            text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict))
        else:
            status, body = post(f"{root}/v1/chat/completions",
                                {"model": model, "max_tokens": MAX_TOKENS,
                                 "messages": [{"role": "system", "content": INSTRUCTION},
                                              {"role": "user", "content": user}]},
                                {"Authorization": f"Bearer {route.credential}"})
            if not 200 <= status < 300:
                return None
            data = json.loads(body)
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        out = _json_in(text)
        if out is not None:
            out["_model"] = mid
            out["_tokens"] = _usage_tokens(data)
            out["_usage"] = _usage_of(data, model)
        return out

    return rank, mid
