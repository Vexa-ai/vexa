"""Unproven features are switches (`control_plane/features.py`, founder 2026-10-13).

Off — the release default — a feature's routes answer 403 `feature_disabled`, its MCP tools are not in
the served manifest, and `/api/features` says so for the terminal. `VEXA_FEATURES` is the install
default; `features:` in `_global/POLICIES.md` overlays it per request. Data and code stay.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from control_plane import features as F
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.test_api import _FakeIdentity, _FakeRuntime

H = {"X-User-Id": "u_jane", "X-User-Email": "jane@example.org"}
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "mcp.tools.v1.json"
SWITCHED = sorted(n for n, f in F.FEATURES.items() if f.get("routes"))


def _app(tmp_path, monkeypatch, features: str | None):
    if features is None:
        monkeypatch.delenv(F.ENV, raising=False)
    else:
        monkeypatch.setenv(F.ENV, features)
    seed = tmp_path / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    ws = tmp_path / "ws"
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(ws))))
    c.post("/api/workspace/init", headers=H)
    return c, ws


def _policy(ws, line: str):
    from control_plane import system_mounts
    g = system_mounts.global_root(load_settings(), str(ws))
    g.mkdir(parents=True, exist_ok=True)
    (g / "POLICIES.md").write_text(f"---\nkind: policies\n{line}\n---\n\n# Policies\n")


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────

def test_release_default_is_every_feature_off():
    assert F.install_defaults({}) == {n: False for n in F.FEATURES}
    assert F.install_defaults({F.ENV: ""}) == {n: False for n in F.FEATURES}


def test_all_a_list_and_a_take_back():
    on = F.install_defaults({F.ENV: "all"})
    # every feature but the explicit ones (switched on only by name, never by `all`)
    assert all(v for n, v in on.items() if not F.FEATURES[n].get("explicit"))
    assert not any(v for n, v in on.items() if F.FEATURES[n].get("explicit"))
    on = F.install_defaults({F.ENV: "crm, calls"})
    assert on["crm"] and on["calls"] and not on["notifications"]
    back = F.install_defaults({F.ENV: "all,-crm"})
    assert not back["crm"] and back["calls"]


def test_an_unknown_name_is_ignored_not_fatal():
    assert F.install_defaults({F.ENV: "teleport,calls"})["calls"] is True


def test_policies_line_overlays_the_install_default():
    raw = "---\nfeatures: {calls: on, notifications: off, teleport: on, crm: on}\n---\n"
    answers, problems = F.policy_answers(raw)
    assert answers == {"calls": True, "notifications": False}
    assert any("teleport" in p for p in problems)
    assert any("crm" in p and "install" in p for p in problems)   # install scope: env only
    eff = F.resolve(raw, {F.ENV: "notifications"})
    assert eff["calls"] is True and eff["notifications"] is False and eff["crm"] is False


def test_empty_policies_line_answers_nothing():
    assert F.policy_answers("---\nfeatures:\n---\n") == ({}, [])
    assert F.policy_answers("---\nfeatures: {}\n---\n") == ({}, [])


def test_the_desktop_is_registered_off_install_scoped_and_on_with_all():
    assert F.FEATURES["desktop"]["scope"] == "install"
    assert F.install_defaults({})["desktop"] is False
    assert F.install_defaults({F.ENV: "all"})["desktop"] is True
    assert F.feature_of("POST", "/api/desktop/sections") == "desktop"
    answers, problems = F.policy_answers("---\nfeatures: {desktop: on}\n---\n")
    assert answers == {} and any("desktop" in p and "install" in p for p in problems)


def test_desktop_cards_is_registered_off_install_scoped_needing_crm_and_desktop():
    f = F.FEATURES["desktop_cards"]
    assert f["scope"] == "install" and f["routes"] == []
    assert set(f["needs"]) == {"crm", "desktop"}
    assert "card" in f["summary"]
    assert F.install_defaults({})["desktop_cards"] is False
    assert F.install_defaults({F.ENV: "all"})["desktop_cards"] is True
    assert F.install_defaults({F.ENV: "all,-desktop_cards"})["desktop_cards"] is False
    answers, problems = F.policy_answers("---\nfeatures: {desktop_cards: on}\n---\n")
    assert answers == {} and any("desktop_cards" in p and "install" in p for p in problems)


def test_home2_is_registered_off_explicit_needing_the_desktop():
    f = F.FEATURES["home2"]
    assert f.get("explicit") is True and f["needs"] == ["desktop"] and "scope" not in f
    # explicit: never in `all`; on by name, or by the company layer
    assert F.install_defaults({})["home2"] is False and F.install_defaults({F.ENV: "all"})["home2"] is False
    assert F.install_defaults({F.ENV: "all,home2"})["home2"] is True
    assert F.policy_answers("---\nfeatures: {home2: on}\n---\n")[0] == {"home2": True}
    assert F.feature_of("POST", "/api/desktop/attention") == "home2"
    assert F.feature_of("POST", "/api/desktop/refs") == "home2"


def test_shell2_is_registered_off_explicit_needing_home2():
    """UI-1 (0.13.4): the approved shell is client-only — no route of its own — and off by default."""
    f = F.FEATURES["shell2"]
    assert f.get("explicit") is True and f["needs"] == ["home2"] and f["routes"] == [] and "scope" not in f
    assert F.install_defaults({})["shell2"] is False and F.install_defaults({F.ENV: "all"})["shell2"] is False
    assert F.install_defaults({F.ENV: "all,home2,shell2"})["shell2"] is True
    assert F.feature_of("POST", "/api/desktop/sections") == "desktop"


def test_a_later_feature_is_registered_off():
    for name in ("home_stream", "people", "event_agents", "rooms", "transcript_signals"):
        assert F.install_defaults({})[name] is False


# ── the registry stays true to what is served ────────────────────────────────────────────────────

def test_every_route_pattern_matches_a_served_route(tmp_path, monkeypatch):
    from control_plane import route_policy
    c, _ = _app(tmp_path, monkeypatch, "all")
    assert F.unmatched(route_policy.served_keys(c.app.routes)) == []


def test_a_pattern_matching_nothing_refuses_the_boot():
    with pytest.raises(RuntimeError, match="notifications"):
        F.assert_served([])


def test_every_switched_feature_owns_a_tool_or_says_why():
    tools = json.loads(MANIFEST.read_text())["tools"]
    owned = {F.feature_of(t["route"]["method"], t["route"]["path"]) for t in tools}
    # shared_chats, notifications, folder_drop, chat_history_tools, calendar_write, workspace_forget,
    # agent_messages each own at least one tool; calls is UI + routes only in this release, and the
    # desktop's agent tools are crm-api's (desktop_read / desktop_arrange*), its route here is the app's.
    # rooms is server routes only until the founder approves the room UI (its tools come with it).
    for name in SWITCHED:
        if name not in ("calls", "desktop", "rooms", "home2", "readme_page"):
            assert name in owned, name


def test_proven_tools_are_never_switched():
    tools = {t["name"]: t for t in json.loads(MANIFEST.read_text())["tools"]}
    for name in ("workspace_read", "workspace_write", "chat_participants", "chat_participant_remove",
                 "calendar_events", "chat_folders_list", "chat_name"):
        r = tools[name]["route"]
        assert F.feature_of(r["method"], r["path"]) is None, name


# ── the app ──────────────────────────────────────────────────────────────────────────────────────

CASES = [
    ("notifications", "GET", "/api/notifications"),
    ("notifications", "POST", "/api/notifications/notify"),
    ("calls", "POST", "/api/sessions/s-abc/call"),
    ("calls", "POST", "/api/sessions/s-abc/call/token"),
    ("shared_chats", "POST", "/api/sessions/s-abc/participants"),
    ("shared_chats", "GET", "/api/people/mentionable"),
    ("folder_drop", "POST", "/api/workspace/folder/plan"),
    ("chat_history_tools", "GET", "/api/chats/search"),
    ("chat_history_tools", "GET", "/api/chats/s-abc/turns"),
    ("calendar_write", "POST", "/api/connections/calendar/event/create"),
    ("calendar_write", "POST", "/api/connections/gmail/draft/update"),
    ("calendar_write", "POST", "/api/connections/gmail/draft"),
    ("workspace_forget", "POST", "/api/workspace/forget"),
    ("agent_messages", "POST", "/api/agents/message"),
    ("desktop", "POST", "/api/desktop/sections"),
    ("home2", "POST", "/api/desktop/attention"),
    ("home2", "POST", "/api/desktop/refs"),
]


@pytest.mark.parametrize("name,method,path", CASES)
def test_off_route_refuses_typed(tmp_path, monkeypatch, name, method, path):
    c, _ = _app(tmp_path, monkeypatch, None)
    r = c.request(method, path, headers=H, json={})
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "feature_disabled" and detail["feature"] == name
    assert detail["status"] == "refused" and detail["tell_your_person"]


@pytest.mark.parametrize("name,method,path", CASES)
def test_on_route_reaches_its_handler(tmp_path, monkeypatch, name, method, path):
    # `all` plus the explicit ones by name: every switch on
    c, _ = _app(tmp_path, monkeypatch, ",".join(["all", *(n for n, f in F.FEATURES.items() if f.get("explicit"))]))
    r = c.request(method, path, headers=H, json={})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    detail = body.get("detail") if isinstance(body, dict) else None
    assert not (isinstance(detail, dict) and detail.get("reason") == "feature_disabled"), r.text


def test_proven_routes_are_untouched_when_everything_is_off(tmp_path, monkeypatch):
    c, _ = _app(tmp_path, monkeypatch, None)
    assert c.get("/api/workspace/tree", headers=H).status_code != 403
    assert c.get("/api/sessions/s-abc/participants", headers=H).json().get("detail", {}) != F.refusal("shared_chats")


def test_policies_switches_on_live_without_a_restart(tmp_path, monkeypatch):
    c, ws = _app(tmp_path, monkeypatch, None)
    assert c.get("/api/notifications", headers=H).status_code == 403
    _policy(ws, "features: {notifications: on}")
    assert c.get("/api/notifications", headers=H).status_code != 403
    _policy(ws, "features: {notifications: off}")
    assert c.get("/api/notifications", headers=H).status_code == 403


def test_policies_switches_off_what_the_install_turned_on(tmp_path, monkeypatch):
    c, ws = _app(tmp_path, monkeypatch, "all")
    _policy(ws, "features: {workspace_forget: off}")
    r = c.post("/api/workspace/forget", headers=H, json={})
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "workspace_forget"


def test_features_route_answers_the_effective_map(tmp_path, monkeypatch):
    c, ws = _app(tmp_path, monkeypatch, "calls")
    body = c.get("/api/features", headers=H).json()
    assert body["features"]["calls"] is True and body["features"]["crm"] is False
    assert set(body["features"]) == set(F.FEATURES) and set(body["summaries"]) == set(F.FEATURES)
    _policy(ws, "features: {calls: off}")
    assert c.get("/api/features", headers=H).json()["features"]["calls"] is False


def test_served_manifest_drops_off_tools_and_keeps_the_rest(tmp_path, monkeypatch):
    every = {t["name"] for t in json.loads(MANIFEST.read_text())["tools"]}
    c, ws = _app(tmp_path, monkeypatch, None)
    off = {t["name"] for t in c.get("/.well-known/mcp-tools.json").json()["tools"]}
    for gone in ("notifications_list", "notifications_mark_read", "notify", "chat_participant_add",
                 "workspace_folder_import_status", "chats_search", "chat_read", "calendar_event_create",
                 "calendar_event_update", "calendar_event_cancel", "gmail_draft_update", "gmail_draft_create",
                 "workspace_forget", "message_agent"):
        assert gone not in off, gone
    assert {"workspace_read", "chat_participants", "gmail_search", "calendar_events"} <= off
    explicit = ",".join(n for n, f in F.FEATURES.items() if f.get("explicit"))
    c2, _ = _app(tmp_path / "b", monkeypatch, "all," + explicit if explicit else "all")
    assert {t["name"] for t in c2.get("/.well-known/mcp-tools.json").json()["tools"]} == every
    c3, _ = _app(tmp_path / "c", monkeypatch, "all")
    # every tool but an EXPLICIT feature's, which `all` never switches on (meeting_listen: transcript_signals)
    explicit = {t["name"] for t in json.loads(MANIFEST.read_text())["tools"]
                if F.FEATURES.get(F.feature_of(t["route"]["method"], t["route"]["path"]) or "", {}).get("explicit")}
    assert "meeting_listen" in explicit
    assert {t["name"] for t in c3.get("/.well-known/mcp-tools.json").json()["tools"]} == every - explicit


def test_a_feature_can_be_on_for_named_accounts_only(tmp_path, monkeypatch):
    """`features_accounts` — the generic test-account switch, for every switchable feature: the
    terminal's flag read and the route gate both answer per caller."""
    c, ws = _app(tmp_path, monkeypatch, "all")
    _policy(ws, "features_accounts: {rooms: 7|8, notifications: 9}")
    mine = c.get("/api/features", headers={"X-User-Id": "7"}).json()["features"]
    other = c.get("/api/features", headers={"X-User-Id": "5"}).json()["features"]
    assert mine["rooms"] is True and other["rooms"] is False
    assert c.get("/api/rooms?workspace=team", headers={"X-User-Id": "5"}).status_code == 403
    assert c.get("/api/rooms?workspace=team", headers={"X-User-Id": "7"}).status_code != 403
    assert F.account_answers("---\nfeatures_accounts: {crm: 7}\n---\n") == {}      # install scope: never here
    assert F.account_answers("---\nfeatures_accounts: {rooms: 7|8}\n---\n") == {"rooms": frozenset({"7", "8"})}


def test_a_listed_tool_of_an_account_only_feature_is_refused_for_everyone_else(tmp_path, monkeypatch):
    """The open manifest lists the tools of a feature on for any named account; every other caller —
    a person or their agent — is refused at call time, clearly, before the route runs."""
    c, ws = _app(tmp_path, monkeypatch, "all")
    _policy(ws, "features_accounts: {transcript_signals: 7}")
    tools = {t["name"] for t in c.get("/.well-known/mcp-tools.json").json()["tools"]}
    assert "meeting_listen" in tools
    for who in ({"X-User-Id": "5"},
                {"X-User-Id": "5", "x-user-regime": "human", "x-user-delegation-workspaces": "*"}):
        r = c.post("/api/meeting/listen", headers=who, json={"meeting": "1", "brief": {}})
        assert r.status_code == 403
        d = r.json()["detail"]
        assert d["reason"] == "feature_disabled" and "not enabled for this account" in d["instruction"]
    r = c.post("/api/meeting/listen", headers={"X-User-Id": "7"}, json={"meeting": "1", "brief": {}})
    assert r.status_code != 403                                           # the named account reaches it
    _policy(ws, "kind: policies")
    assert "meeting_listen" not in {t["name"] for t in c.get("/.well-known/mcp-tools.json").json()["tools"]}
