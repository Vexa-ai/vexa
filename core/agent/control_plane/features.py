"""features.py — one switch per feature that is built but not yet proven (founder, 2026-10-13: "we
will clean up and hide features that are unproven, while having those ready to continue in dev").

A feature here is CODE AND DATA THAT STAY. Switched off, it is not removed: its UI entry points are
hidden (the terminal reads ``GET /api/features``), its MCP tools are not listed (the served manifest
drops every tool whose route belongs to an off feature), and its routes refuse with one typed body,
``feature_disabled``. Rows it wrote stay where they are and are there again when it is switched on.

Where the answer comes from, in order — the later one wins:

    1. the install default, ``VEXA_FEATURES`` (env). Empty or unset — the release default — means
       every feature named here is OFF: each of them is unproven, which is why it is here at all.
       ``all`` switches every one on (app.dev); a comma list switches on just those; ``-name``
       after ``all`` takes one back (``all,-crm``).
    2. the company layer, ``features:`` in ``_global/POLICIES.md``, read per request: one line, a
       flow-style map, ``features: {crm: on, calls: off}``. An empty value answers nothing.

A name nobody registered is OFF, whatever either source says, so a feature that lands later is off
until it is registered here with its routes. Install-scope features (``scope: install``) are
answered by the env alone: the service that serves them reads no company layer.

A feature graduates by being deleted from FEATURES (its routes then match nothing and are always
served) — never by flipping a default here.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Mapping, Optional

logger = logging.getLogger("agent_api.features")

ENV = "VEXA_FEATURES"
POLICY_KEY = "features"
POLICIES_FILE = "POLICIES.md"
REASON = "feature_disabled"

#: Every switchable feature. ``routes`` are (method or "*", path regex) against agent-api's own
#: paths; a tool in ``core/agent/mcp.tools.v1.json`` whose route matches is that feature's tool.
#: ``elsewhere`` names surfaces another service hides from the same env (it cannot read this file).
FEATURES: dict[str, dict] = {
    "shared_chats": {
        "summary": "adding another person to a chat (the @ picker, Add people, the agent's chat_participant_add)",
        "routes": [("POST", r"^/api/sessions/[^/]+/participants/?$"),
                   ("GET", r"^/api/people/mentionable/?$"),
                   ("GET", r"^/api/sessions/[^/]+/share-preview/?$")],
    },
    "notifications": {
        "summary": "the bell, toasts and the notifications_list / notifications_mark_read / notify tools",
        "routes": [("*", r"^/api/notifications(/.*)?$")],
    },
    # listed BEFORE calls: its routes sit under /call, and the first match names the owner
    "call_show": {
        "summary": "showing a page to everyone in a call: it opens for all, follows the presenter, highlights (call_show / call_highlight)",
        "routes": [("POST", r"^/api/sessions/[^/]+/call/(show|highlight)/?$")],
    },
    "calls": {
        "summary": "LiveKit calls in a chat: the Call button, joining, the room's post-call proposals",
        "routes": [("*", r"^/api/sessions/[^/]+/call(/.*)?$"),
                   ("*", r"^/api/sessions/[^/]+/proposals(/.*)?$")],
    },
    "crm": {
        "summary": "the CRM: the record page, crm_* tools, crm-api",
        "scope": "install",
        "routes": [],
        "elsewhere": "crm-api refuses every route; the MCP lists no crm_* tool; the terminal hides the record page",
    },
    "browser_bots": {
        "summary": "browser sessions and bot accounts (sign a bot in once, bots join signed in)",
        "scope": "install",
        "routes": [],
        "elsewhere": "meeting-api refuses /bot-accounts/*; the MCP lists no browser_session_open / bot_accounts_* tool",
    },
    "folder_drop": {
        "summary": "dropping a folder to make a workspace",
        "routes": [("*", r"^/api/workspace/folder(/.*)?$")],
    },
    "chat_history_tools": {
        "summary": "the agent reading a person's other chats (chats_search, chat_read)",
        "routes": [("GET", r"^/api/chats/search/?$"), ("GET", r"^/api/chats/[^/]+/turns/?$")],
    },
    "calendar_write": {
        "summary": "calendar changes (create, move, cancel events) and mail drafts (save, edit)",
        "routes": [("POST", r"^/api/connections/calendar/event/(create|update|cancel)/?$"),
                   ("POST", r"^/api/connections/gmail/draft(/update)?/?$")],
    },
    "workspace_forget": {
        "summary": "forgetting a file and everything derived from it, with history rewritten",
        "routes": [("POST", r"^/api/workspace/forget/?$")],
    },
    "agent_messages": {
        "summary": "an agent sending a message to another chat's agent (message_agent)",
        "routes": [("POST", r"^/api/agents/message/?$")],
    },
    # THE DESKTOP (desktop D1, ADR desktop-cards). Install scope, like `crm` which it needs: crm-api
    # stores the placements and the arrangement and reads only the env. agent-api's half is the
    # sections read the terminal composes the desktop from (`routers/desktop.py`).
    "desktop": {
        "summary": "the desktop: workspaces, chosen agents and files, arranged by you or your agent",
        "scope": "install",
        "routes": [("POST", r"^/api/desktop/sections/?$")],
        "elsewhere": "crm-api refuses /desk/*; the MCP lists no desktop_* tool; the terminal shows the home without it",
    },
    # ROOMS (2026-10-15, decided: chats personal, rooms made in a workspace): a room's sensor stream,
    # its visibility and proactivity, a reply to the agent as an address, a room's wake, and — on —
    # nobody added to a chat. Off: shared chats behave exactly as before.
    "rooms": {
        "summary": "rooms: made in a workspace (open or closed), their sensor and proactivity; chats stay private",
        # NOT IN `all` (app.dev runs `all`): live shared chats must not change until the founder
        # approves the room UI — switched on only by name or by `_global/POLICIES.md`.
        "explicit": True,
        "routes": [("*", r"^/api/sessions/[^/]+/room/?$"), ("*", r"^/api/rooms(/.*)?$"),
                   ("*", r"^/api/workspace-links/[^/]+(/remove|/visibility)?/?$"),
                   ("*", r"^/api/calls/[^/]+/changes(/.*)?$")],
    },
    # TRANSCRIPT SIGNALS (2026-10-15): a small model listens to a live meeting against the agent's
    # brief and wakes it in the meeting's chat. Off: the brief tool is not served (the terminal
    # hides the highlights and the brief); the company's `transcript_signals` policy is the second
    # switch, on the wakes themselves.
    "transcript_signals": {
        "summary": "listening to a live meeting: the brief (meeting_listen), highlights, waking the agent in the meeting's chat",
        # NEW FEATURES ARE NEVER IN `all` (docs review 2026-10-16): on by name or by the company only
        "explicit": True,
        "routes": [("POST", r"^/api/meeting/listen/?$")],
    },
    # HOME2 (R3 + R1, 2026-10-16): the new home (status cards, the Now strip), the workspace page in
    # the right panel and the sidebar grouped by workspace (rooms, your chats, calls; no folders).
    # Needs `desktop`. EXPLICIT: not in `all` — it replaces what people rely on, so it is switched on
    # by name (`VEXA_FEATURES=all,home2`) or by the company layer (`features: {home2: on}`).
    "home2": {
        "summary": "the new home, the workspace page and the sidebar grouped by workspace",
        "explicit": True,
        "needs": ["desktop"],
        "routes": [("POST", r"^/api/desktop/(attention|refs)/?$")],
        "elsewhere": "the terminal shows the desktop home, the README and the folder sidebar",
    },
    # SHELL2 (0.13.4 lane UI-1, founder-approved mock `cards-mock-live`): the one-list sidebar
    # (+ New · Inbox · Meetings, the All · Rooms · Routines filter), Inbox and Meetings as lists in
    # the centre, "My desk" for the personal workspace, the kit's list rows, outlined primaries, two-way
    # panels and the phone fixes. Client-only: it renders data the existing routes already serve, so
    # it has no route of its own. Needs `home2`. EXPLICIT: not in `all` — it changes the sidebar people
    # rely on, so it is on by name (`VEXA_FEATURES=all,home2,shell2`) or by the company layer.
    "shell2": {
        "summary": "the approved shell: one-list sidebar, Inbox and Meetings in the centre, My desk, the phone fixes",
        "explicit": True,
        "needs": ["home2"],
        "routes": [],
        "elsewhere": "the terminal shows the home2 sidebar and the meetings in the file panel",
    },
    # CARDS ON THE DESKTOP (desktop D2, ADR desktop-cards). Install scope; needs `crm` and `desktop`.
    # Cards are CRM records, so every route and tool is crm-api's; agent-api serves none of it.
    "desktop_cards": {
        "summary": "cards on the desktop: CRM cards you are on, proposals to accept",
        "scope": "install",
        "needs": ["crm", "desktop"],
        "routes": [],
        "elsewhere": "crm-api refuses /card/* and the desk read carries no cards; the MCP lists no card_* tool; the terminal shows no Cards section and no Make a card",
    },
    # THE README PAGE (0.13.4 S7, PRD D17–D20, UI 7aa/7cc/7ee/7gg): the workspace page is its README,
    # rendered per viewer on the server (`POST /api/desktop/page`), with shared pins (`/api/pins`).
    # Needs `home2`. EXPLICIT: it replaces the workspace page people rely on, so it is on by name.
    "readme_page": {
        "summary": "the workspace page is its README, rendered for each viewer, with shared pins",
        "explicit": True,
        "needs": ["home2"],
        "routes": [("POST", r"^/api/desktop/page/?$"), ("GET", r"^/api/pins/?$"), ("POST", r"^/api/pins/?$")],
        "elsewhere": "the terminal shows the home2 workspace page (status block over the README)",
    },
    # VEXA CREDITS (PLAN-8H 6.2, decision G1): credits at market cost x the markup, 5-hour and weekly
    # windows, the usage meter, extra usage (data only). Off: nothing is debited or refused by window.
    "usage": {
        "summary": "the usage meter: credits, 5-hour and weekly windows, extra usage",
        # NOT IN `all` (app.dev runs `all`): a window can refuse turns — on only by name.
        "explicit": True,
        "routes": [("*", r"^/api/usage/?$")],
    },
    # MODEL LEVELS 1–5 (router v0, PLAN-8H 6.3): hosted plans pick a level, not a model. Self-host
    # and app.dev keep the full picker, so this stays off there.
    "levels": {
        "summary": "model levels 1–5 in place of the model picker (hosted plans)",
        # NOT IN `all` (app.dev runs `all` and keeps the full picker): on only by name.
        "explicit": True,
        "routes": [("GET", r"^/api/models/levels/?$"), ("POST", r"^/api/chat/level/?$")],
    },
    # PUBLIC PROFILES AND THE DIRECTORY (R4, registry ADR): a person publishes profile fields one by
    # one; an org's contact may link to one and shows its public fields live; the directory finds
    # published profiles without enumeration. Install scope: identity (admin-api) and crm-api serve
    # it and read only the env. EXPLICIT: not in `all`.
    "public_profiles": {
        "summary": "public profiles: publish your own profile field by field, linked contacts, the directory",
        "scope": "install",
        "explicit": True,
        "routes": [],
        "elsewhere": "admin-api refuses /user/profile*, /user/directory/*, /user/company-verification* and /internal/profiles/*; crm-api refuses /profile/*",
    },
    # OPEN WORKSPACES (R4): anyone signed in reads an open workspace's pages and open rooms, asks its
    # agent within limits and joins as its owners allow. Its routes (`/api/open*`) and router come
    # with lane 9's `open-workspaces` branch; registered here so the directory's entry point stays off
    # until then. When the two meet, lane 9's entry (with its routes) is the one to keep.
    "open_workspaces": {
        "summary": "open workspaces: anyone signed in reads them, asks their agent, and joins as the owners allow",
        "routes": [],
        "explicit": True,
        "elsewhere": "the terminal shows no directory and no Make open",
    },
    # Built on bbb, not in this release. Registered so they are off the moment they land; each adds
    # its routes here when it does.
    "home_stream": {"summary": "the home stream on a new chat", "routes": []},
    "people": {"summary": "people follow-ups and discovery", "routes": []},
    "event_agents": {"summary": "agents started by events, their sinks, routine claims", "routes": []},
}

_COMPILED = {name: [(m, re.compile(p)) for m, p in f.get("routes", ())] for name, f in FEATURES.items()}
_ON = frozenset({"on", "true", "yes", "1", "enabled"})
_OFF = frozenset({"off", "false", "no", "0", "disabled"})


def install_defaults(env: Optional[Mapping[str, str]] = None) -> dict[str, bool]:
    """What ``VEXA_FEATURES`` switches on. Unknown names are logged and ignored, never fatal."""
    raw = str((env if env is not None else os.environ).get(ENV) or "")
    out = {name: False for name in FEATURES}
    for token in (t.strip().lower() for t in raw.split(",")):
        if not token:
            continue
        on = not token.startswith("-")
        name = token.lstrip("-+").strip()
        if name == "all":
            # `explicit` features are not in `all`: they change what people already rely on, so
            # they are switched on only by name (`all,rooms`) or by the company layer.
            out = {n: (on and not FEATURES[n].get("explicit")) or (out.get(n, False) and not on)
                   for n in FEATURES}
        elif name in FEATURES:
            out[name] = on
        else:
            logger.warning("%s: %r is not a feature (%s)", ENV, name, ", ".join(FEATURES))
    return out


def policy_answers(raw_policies: Optional[str]) -> tuple[dict[str, bool], list[str]]:
    """The ``features:`` line of ``_global/POLICIES.md`` as {name: bool}, plus what could not be read."""
    from control_plane import front_page
    value = (front_page.front_matter(raw_policies or "").get(POLICY_KEY) or "").strip()
    problems: list[str] = []
    out: dict[str, bool] = {}
    if not value:
        return out, problems
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1]
    for pair in value.split(","):
        if not pair.strip():
            continue
        name, sep, answer = pair.partition(":")
        name, answer = name.strip().strip("'\"").lower(), answer.strip().strip("'\"").lower()
        if not sep or name not in FEATURES:
            problems.append(f"features: {name or pair.strip()!r} is not a feature")
            continue
        if FEATURES[name].get("scope") == "install":
            problems.append(f"features: {name} is set by the install ({ENV}), not here")
            continue
        if answer in _ON:
            out[name] = True
        elif answer in _OFF:
            out[name] = False
        else:
            problems.append(f"features: {name}: {answer!r} is neither on nor off")
    return out, problems


def resolve(raw_policies: Optional[str], env: Optional[Mapping[str, str]] = None) -> dict[str, bool]:
    out = install_defaults(env)
    answers, problems = policy_answers(raw_policies)
    for problem in problems:
        logger.warning("POLICIES.md %s", problem)
    out.update(answers)
    return out


def read_policies(global_dir: Optional[Path]) -> Optional[str]:
    if global_dir is None or not Path(global_dir).is_dir():
        return None
    try:
        from workspaces.shared import workspace_paths as wpaths
        return wpaths.read_text_inside(global_dir, POLICIES_FILE, max_bytes=1_000_000)
    except Exception:  # noqa: BLE001 — no readable company layer answers nothing; the env decides
        return None


ACCOUNTS_KEY = "features_accounts"


def account_answers(raw_policies: Optional[str]) -> dict[str, frozenset]:
    """``features_accounts:`` — a feature switched on for NAMED ACCOUNTS ONLY, the test-account
    switch (`BUILD-2026-10-16`: "rooms stays off on app.dev except for a test account"):
    ``features_accounts: {rooms: 12|34}``. Install-scope features are not switchable here."""
    from control_plane import front_page
    value = (front_page.front_matter(raw_policies or "").get(ACCOUNTS_KEY) or "").strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1]
    out: dict = {}
    for pair in value.split(","):
        name, sep, who = pair.partition(":")
        name = name.strip().strip("'\"").lower()
        if not sep or name not in FEATURES or FEATURES[name].get("scope") == "install":
            continue
        ids = frozenset(w.strip().strip("'\"") for w in who.split("|") if w.strip().strip("'\""))
        if ids:
            out[name] = ids
    return out


def effective(global_dir: Optional[Path], env: Optional[Mapping[str, str]] = None,
              subject: Optional[str] = None) -> dict[str, bool]:
    """Every feature, on or off, as this instance answers RIGHT NOW — for ``subject``, when named:
    a feature off for the company is on for an account ``features_accounts`` names."""
    raw = read_policies(global_dir)
    out = resolve(raw, env)
    if subject:
        for name, ids in account_answers(raw).items():
            if str(subject) in ids:
                out[name] = True
    return out


def on_for_anyone(global_dir: Optional[Path], name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Is ``name`` on for the company, or for at least one named account? (a background loop's
    question: it serves everyone and filters per item)."""
    raw = read_policies(global_dir)
    return bool(resolve(raw, env).get(name)) or bool(account_answers(raw).get(name))


def feature_of(method: str, path: str) -> Optional[str]:
    """The feature an agent-api route belongs to, or ``None`` for a route no switch covers."""
    m = (method or "").upper()
    for name, pats in _COMPILED.items():
        for meth, rx in pats:
            if (meth == "*" or meth == m) and rx.match(path or ""):
                return name
    return None


def refusal(name: str, *, for_account: bool = False) -> dict:
    """The one body every switched-off route answers with. ``for_account``: the feature is on for
    some named accounts (`features_accounts`), not this caller's."""
    if for_account:
        return {
            "status": "refused", "reason": REASON, "feature": name,
            "instruction": f"{name} is not enabled for this account. Do not retry it another way; say "
                           f"it is not available to them yet.",
            "tell_your_person": f"This isn't enabled for your account yet "
                                f"({FEATURES.get(name, {}).get('summary', name)}).",
        }
    return {
        "status": "refused",
        "reason": REASON,
        "feature": name,
        "instruction": f"{name} is switched off on this deployment. Do not retry it another way; say "
                       f"it is not available here.",
        "tell_your_person": f"This isn't switched on here yet ({FEATURES.get(name, {}).get('summary', name)}). "
                            f"An administrator can turn it on.",
    }


def visible_tools(tools: Iterable[dict], on: Mapping[str, bool]) -> list[dict]:
    """The manifest's tools minus those whose route belongs to a feature that is off."""
    kept = []
    for tool in tools:
        route = tool.get("route") or {}
        name = feature_of(str(route.get("method") or ""), str(route.get("path") or ""))
        if name is None or on.get(name, False):
            kept.append(tool)
    return kept


class FeatureGate:
    """ASGI middleware: a request for a route of a switched-off feature is answered 403
    ``feature_disabled`` before the route runs. The company layer is read only for such a route."""

    def __init__(self, app, *, global_dir, env: Optional[Mapping[str, str]] = None):
        self.app = app
        self.global_dir = global_dir
        self.env = env

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            name = feature_of(scope.get("method", ""), scope.get("path", ""))
            if name is not None:
                try:
                    root = self.global_dir() if callable(self.global_dir) else self.global_dir
                except Exception:  # noqa: BLE001
                    root = None
                subject = ""
                for k, v in scope.get("headers") or []:
                    if k.decode("latin-1").lower() == "x-user-id":
                        subject = v.decode("latin-1").strip()
                        break
                if not effective(root, self.env, subject=subject or None).get(name, False):
                    import json as _json
                    body = _json.dumps({"detail": refusal(
                        name, for_account=on_for_anyone(root, name, self.env))}).encode()
                    logger.info(_json.dumps({"event": "refused", "reason": REASON, "feature": name,
                                             "path": scope.get("path", "")}))
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)


def unmatched(served: Iterable[tuple]) -> list[str]:
    """Route patterns that match no served (method, path) — a switch that would protect nothing."""
    served = list(served)
    out = []
    for name, pats in _COMPILED.items():
        for meth, rx in pats:
            if not any((meth == "*" or meth == m) and rx.match(p) for m, p in served):
                out.append(f"{name}: {meth} {rx.pattern}")
    return out


def assert_served(routes) -> None:
    """Refuse the boot when a feature names a route this app does not serve (a renamed route would
    otherwise leave the feature always on)."""
    from control_plane import route_policy
    missing = unmatched(route_policy.served_keys(routes))
    if missing:
        raise RuntimeError("features name routes agent-api does not serve: " + "; ".join(missing))
