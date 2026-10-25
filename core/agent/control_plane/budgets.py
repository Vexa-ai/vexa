"""budgets.py — the company's budgets and model policy, read live from `_global/POLICIES.md` (#1783).

Founder, 2026-10-13: *"this should be globally set-up policy — budgets for tokens for users and
routines, model policies etc. Keep it there in a simple way now."* And the split: PROVIDERS (endpoints,
keys, which provider serves which model id — `VEXA_MODEL_CATALOG`, the credential env) stay in install
config; everything about what is ALLOWED and what is the DEFAULT lives here, in the company layer the
admin edits in Company mode. Config declares what is available; `_global` decides what is allowed.

READ LIVE: every call reads the file (through `home.read_global`), so an edit applies on the next
turn with no restart. An install's environment only supplies the DEFAULTS (`VEXA_BUDGET_*`,
`VEXA_FLOWS_USER_RUNS_PER_DAY`, …). A value this module cannot read keeps the default for that key —
a budget must never become unlimited because of a typo.

A PERSON MAY ONLY NARROW: `narrow(admin, mine)` takes the lower of each number and the intersection of
each model list; a person's setting can never widen the admin's answer.

THE KEYS (one line each, also documented in POLICIES.md):

    budget_tokens_per_user_day     tokens one person's agent turns may use in a rolling day (all of them)
    budget_tokens_routines_share   the most of that, in percent, routines and other background work may use
    routine_runs_per_day           runs a person's routines may start a day (flows reads it live)
    routine_min_interval_minutes   the shortest schedule a routine may have
    home_compose_per_day           model-ranked home compositions per person a day
    home_expand_per_day            in-place elaborations per person a day
    event_agent_runs_per_day       agent runs started by an outside event (mail, a meeting) per person a day
    event_agent_max_trust          the most an event-started run may do: read | draft | act
    model_chat_default             the model a new chat starts on
    models_allowed                 the models anyone may pick, comma-separated, or `all`
    model_routines                 the model routines run on (`cheapest` = the smallest allowed)
    model_home                     the model that ranks the home (`cheapest`)
    model_event_agents             the model event-started runs use (`cheapest`)
    models_for                     who may use which model: `model=all|email|@domain|group:name; …`
                                   (absent: the catalog's `allowed_subjects`, its one-time seed)
    model_rules                    the router's rules, task → model: `{routines: cheapest, events: 'level:2'}`
                                   (chat · routines · events · home); overlays the four keys above
    model_levels                   hosted levels 1–5 → catalog ids: `{1: <id>, …, 5: <id>}`; a gap is
                                   filled from the allowed models by market price
    model_intent_router            the small model the fast intent router asks when its rules are unsure
                                   (a catalog id or `cheapest`; empty: `model_home`)
    subscription_token_proxy       whether a model subscription credential may reach agent workers:
                                   `deny` (the default) or `allow`
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Optional

from control_plane import front_page

INT_KEYS = {
    "budget_tokens_per_user_day": ("VEXA_BUDGET_TOKENS_PER_USER_DAY", 2_000_000, 1, 10**10),
    "budget_tokens_routines_share": ("VEXA_BUDGET_TOKENS_ROUTINES_SHARE", 50, 0, 100),
    "routine_runs_per_day": ("VEXA_FLOWS_USER_RUNS_PER_DAY", 48, 0, 10_000),
    "routine_min_interval_minutes": ("VEXA_FLOWS_USER_MIN_INTERVAL_MINUTES", 10, 1, 10_080),
    "home_compose_per_day": ("VEXA_HOME_COMPOSE_PER_DAY", 14, 0, 500),
    "home_expand_per_day": ("VEXA_HOME_EXPAND_PER_DAY", 40, 0, 1000),
    "event_agent_runs_per_day": ("VEXA_EVENT_AGENT_RUNS_PER_DAY", 30, 0, 10_000),
    # PRD 0.13.4 S2: how many days a MAILED invite's link signs its one address in with one click
    # (never past the invite's own expiry). 0 = invite links never sign anybody in.
    "invite_signin_days": ("", 7, 0, 30),
}
TRUST = ("read", "draft", "act")
STR_KEYS = {
    "event_agent_max_trust": ("VEXA_EVENT_AGENT_MAX_TRUST", "draft"),
    "model_chat_default": ("VEXA_MODEL_CHAT_DEFAULT", ""),
    "models_allowed": ("VEXA_MODELS_ALLOWED", "all"),
    "model_routines": ("VEXA_MODEL_ROUTINES", "cheapest"),
    "model_home": ("VEXA_HOME_MODEL", "cheapest"),
    "model_event_agents": ("VEXA_MODEL_EVENT_AGENTS", "cheapest"),
    "models_for": ("", ""),
    # THE ROUTER (model_router.py, router v0): task type → model, overlaying the keys above; and the
    # levels 1–5 table hosted plans pick from (the `levels` feature). Both empty by default.
    "model_rules": ("", ""),
    "model_levels": ("", ""),
    # THE FAST INTENT ROUTER (QB-5, `intent_router`): its small model; empty falls back to `model_home`.
    "model_intent_router": ("VEXA_MODEL_INTENT_ROUTER", ""),
    # A MODEL SUBSCRIPTION IN AGENT WORKERS (`dispatch.subscription_proxy_allowed`): a consumer
    # subscription is one person's licence, so `deny` unless the admin says `allow`. The company layer
    # is the one place it is set; a person cannot change it (`narrow` leaves it the admin's).
    "subscription_token_proxy": ("", "deny"),
    # THE COMPANY'S APPEARANCE DEFAULT (Settings → Appearance): what a person sees until they pick
    # their own. No environment variable — the company layer is the one place it is set.
    "appearance_accent": ("", ""),
    "appearance_font_ui": ("", ""),
    "appearance_font_reading": ("", ""),
    # THE HOME'S TWO SWITCHES (founder 2026-10-15: "for now let's just disable these cards, let's
    # clean up to release"): the composed cards OFF by default; the People strip on
    "home_cards": ("VEXA_HOME_CARDS", "off"),
    "home_people": ("VEXA_HOME_PEOPLE", "on"),
}
KEYS = tuple(INT_KEYS) + tuple(STR_KEYS)
WARN_AT = 0.8


def _int(v: str) -> Optional[int]:
    v = (v or "").strip().replace(",", "").replace("_", "").replace(" ", "")
    return int(v) if v.isdigit() else None


def defaults(env: Optional[dict] = None) -> dict:
    env = os.environ if env is None else env
    out: dict = {}
    for k, (name, dflt, lo, hi) in INT_KEYS.items():
        n = _int(env.get(name, "")) if name else None
        out[k] = n if n is not None and lo <= n <= hi else dflt
    for k, (name, dflt) in STR_KEYS.items():
        out[k] = (env.get(name) or dflt).strip() if name else dflt
    if out["event_agent_max_trust"] not in TRUST:
        out["event_agent_max_trust"] = "draft"
    return out


def resolve(raw: Optional[str], env: Optional[dict] = None) -> dict:
    """The admin's answers over the install defaults. A value that cannot be read keeps the default."""
    out = defaults(env)
    attrs = front_page.front_matter(raw or "")
    for k, (_n, _d, lo, hi) in INT_KEYS.items():
        n = _int(str(attrs.get(k, "")))
        if n is not None and lo <= n <= hi:
            out[k] = n
    for k in STR_KEYS:
        v = str(attrs.get(k, "") or "").strip()
        if not v:
            continue
        if k == "event_agent_max_trust" and v not in TRUST:
            continue
        out[k] = v
    # THE HOME'S SWITCHES: an install that sets `VEXA_HOME_CARDS` / `VEXA_HOME_PEOPLE` explicitly means
    # it — it overrides the seeded policy default (the release ships `home_cards: off`)
    env = os.environ if env is None else env
    for k in ("home_cards", "home_people"):
        name = STR_KEYS[k][0]
        if str(env.get(name) or "").strip():
            out[k] = str(env[name]).strip()
    return out


def read(global_dir: Optional[Path], env: Optional[dict] = None) -> dict:
    from control_plane import home  # noqa: PLC0415
    return resolve(home.read_global(global_dir, "POLICIES.md"), env)


def on(p: dict, key: str) -> bool:
    """A yes/no policy key: on, true, yes or 1."""
    return str(p.get(key) or "").strip().lower() in ("on", "true", "yes", "1")


def allowed_list(p: dict) -> Optional[list]:
    v = str(p.get("models_allowed") or "all").strip()
    return None if v.lower() == "all" else [m.strip() for m in v.split(",") if m.strip()]


def narrow(admin: dict, mine: Optional[dict]) -> dict:
    """A person's own settings, applied only where they are stricter than the admin's."""
    out = dict(admin)
    for k, v in (mine or {}).items():
        if k in INT_KEYS:
            n = _int(str(v))
            if n is not None and n < out[k]:
                out[k] = n
        elif k == "models_allowed":
            theirs = [m.strip() for m in str(v).split(",") if m.strip()]
            base = allowed_list(out)
            keep = [m for m in theirs if base is None or m in base]
            if keep:
                out[k] = ",".join(keep)
        elif k == "event_agent_max_trust" and v in TRUST and TRUST.index(v) < TRUST.index(out[k]):
            out[k] = v
    return out


def models_for(p: dict) -> Optional[dict]:
    """``models_for`` parsed: ``{model: [who…]}``, or None when the policy does not say (the catalog's
    own `allowed_subjects` then still decide — the one-time seed)."""
    raw = str(p.get("models_for") or "").strip()
    if not raw:
        return None
    out: dict = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        model, who = part.split("=", 1)
        out[model.strip()] = [w.strip().lower() for w in who.split("|") if w.strip()]
    return out


def may_use(p: dict, model: str, *, email: str = "", groups: Iterable[str] = ()) -> bool:
    allow = allowed_list(p)
    if allow is not None and model not in allow:
        return False
    rules = models_for(p)
    if rules is None or model not in rules:
        return True
    who = rules[model]
    e = (email or "").lower()
    return ("all" in who or (e and e in who) or (e and "@" + e.split("@")[-1] in who)
            or any(f"group:{g.lower()}" in who for g in groups))


def model_status(p: dict, available: Iterable[str]) -> list:
    """Every model the policy names, and whether the install offers it — `unavailable`, never a crash."""
    have = set(available)
    named = set(allowed_list(p) or []) | set((models_for(p) or {}).keys())
    for k in ("model_chat_default", "model_routines", "model_home", "model_event_agents", "model_intent_router"):
        v = str(p.get(k) or "")
        if v and v != "cheapest":
            named.add(v)
    return [{"model": m, "status": "available" if m in have else "unavailable"} for m in sorted(named)]


# ── the rolling day ─────────────────────────────────────────────────────────────────────────

def verdict(p: dict, used: dict, *, background: bool) -> dict:
    """What today's usage means for the next turn. ``used`` is ``usage.rolling`` (total, routines…).

    Background work (routines, event agents, home) stops at its share of the budget or at the
    budget; an interactive turn warns from 80% and stops at 100%."""
    budget = max(1, int(p["budget_tokens_per_user_day"]))
    total = int(used.get("total", 0))
    bg = int(used.get("routines", 0)) + int(used.get("events", 0)) + int(used.get("home", 0))
    share = budget * int(p["budget_tokens_routines_share"]) // 100
    frac = total / budget
    if background:
        if total >= budget:
            return {"stop": True, "why": "the daily token budget is used up", "fraction": frac}
        if bg >= share:
            return {"stop": True, "why": f"background work used its {p['budget_tokens_routines_share']}% of the daily budget",
                    "fraction": frac}
        return {"stop": False, "fraction": frac}
    if total >= budget:
        return {"stop": True, "why": "the daily token budget is used up — ask your admin to raise it", "fraction": frac}
    return {"stop": False, "warn": frac >= WARN_AT, "fraction": frac}


def day_key(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


_CLIENTS: dict = {}


def usage_client(redis_url: Optional[str]):
    """One Redis client per URL for the usage ledger, or None (then nothing is counted or blocked)."""
    if not redis_url:
        return None
    if redis_url not in _CLIENTS:
        try:
            import redis  # noqa: PLC0415
            _CLIENTS[redis_url] = redis.from_url(redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001
            return None
    return _CLIENTS[redis_url]


def mine_path(desk: Optional[Path]) -> Optional[Path]:
    return Path(desk) / ".vexa" / "limits.json" if desk else None


def read_mine(desk: Optional[Path]) -> dict:
    """A person's own narrower settings (`<desk>/.vexa/limits.json`, outside git)."""
    import json  # noqa: PLC0415
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    if not desk or not Path(desk).is_dir():
        return {}
    raw = wpaths.read_text_inside(desk, ".vexa/limits.json", max_bytes=20_000)
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def effective(global_dir: Optional[Path], desk: Optional[Path] = None, env: Optional[dict] = None) -> dict:
    return narrow(read(global_dir, env), read_mine(desk))


_WARNED: dict = {}


def gate(subject: str, *, background: bool, policy: dict, used: dict, notifier=None,
         note=None, now: Optional[float] = None) -> Optional[dict]:
    """Before a turn runs: ``None`` to go ahead, or the refusal to answer with. Background work that
    is stopped says so where the person looks — the bell, and a line in the chat (``note(text)``);
    an interactive turn is warned once a day from 80% (the bell) and stopped at 100%."""
    v = verdict(policy, used, background=background)
    pct = int(round(100 * v["fraction"]))
    day = day_key(now)
    if v.get("stop"):
        text = (f"Paused: {v['why']} ({pct}% of {policy['budget_tokens_per_user_day']:,} tokens today). "
                "It resumes as the day rolls on, or when your admin raises the budget." if background else
                f"Stopped: {v['why']} ({pct}% of {policy['budget_tokens_per_user_day']:,} tokens today).")
        if (subject, day, "stop", background) not in _WARNED:
            _WARNED[(subject, day, "stop", background)] = True
            if notifier is not None:
                notifier.emit([subject], kind="budget", actor="vexa:budget", actor_name="Vexa",
                              object_kind="none", object_id=f"budget-{day}", object_name=text[:180])
        if background and note is not None:
            try:
                note(text)
            except Exception:  # noqa: BLE001
                pass
        return {"refused": "budget_paused" if background else "budget_exhausted", "note": text, "percent": pct}
    if v.get("warn") and (subject, day, "warn") not in _WARNED:
        _WARNED[(subject, day, "warn")] = True
        if notifier is not None:
            notifier.emit([subject], kind="budget", actor="vexa:budget", actor_name="Vexa", object_kind="none",
                          object_id=f"budget-warn-{day}",
                          object_name=f"{pct}% of today's token budget is used. At 100% chat stops until the day rolls on; your admin can raise it.")
    return None
