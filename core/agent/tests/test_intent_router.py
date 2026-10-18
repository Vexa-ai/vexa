"""The fast intent router (QB-5; graph rows AW-fast and TU-route).

Founder: "we need router that would understand the intent before calling a big model to understand if
they want something simple". Benchmark: "call @megan", "open the OeNB page", "invite Ana" resolve in
under a second with the right action; an ambiguous request falls through to the agent; the router never
takes an outbound action without the usual approval; every message leaves a route receipt (route,
confidence, latency).

Two layers: the pure router (``control_plane/intent_router``), and the chat route acting on it, in the
call-person rig (a real app over sqlite, a fake meeting-api and LiveKit). Synthetic people only.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from control_plane import intent_router as R
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_call_person import H, SECRET, _Meetings, _Rooms, _git
from tests.test_session_routes import _FakeIdentity, _FakeReader, _FakeRuntime


class _Clock:
    """A fake clock: each read advances by ``step`` seconds."""

    def __init__(self, step=0.0001):
        self.t, self.step = 100.0, step

    def __call__(self):
        self.t += self.step
        return self.t


# ── the rules: the benchmark phrases, no model, well under a second ─────────────────────────────

@pytest.mark.parametrize("text,intent,args", [
    ("call @megan", "call_person", {"person": "@megan"}),
    ("Call Megan", "call_person", {"person": "Megan"}),
    ("please call Megan Ortiz", "call_person", {"person": "Megan Ortiz"}),
    ("ring tester-b", "call_person", {"person": "tester-b"}),
    ("start a vexa call with Megan", "call_person", {"person": "Megan"}),
    ("open the OeNB page", "open_entity", {"query": "OeNB"}),
    ("show me the OeNB page", "open_entity", {"query": "OeNB"}),
    ("invite Ana", "invite_person", {"person": "Ana"}),
    ("invite Ana to the OeNB workspace", "invite_person", {"person": "Ana", "workspace": "OeNB"}),
    ("schedule a call with Megan tomorrow at 3", "schedule", {"person": "Megan", "when": "tomorrow at 3"}),
])
def test_rules_recognise_the_benchmark_phrases_fast(text, intent, args):
    def no_model(*_a):
        raise AssertionError("the rules decide these; no model is asked")
    r = R.route(text, complete=no_model, clock=_Clock())
    assert (r.intent, r.args, r.source, r.confidence) == (intent, args, "rules", 1.0)
    assert r.latency_ms < 1000
    t0 = time.perf_counter()
    R.route(text)                                   # measured, not only on the fake clock
    assert (time.perf_counter() - t0) < 1.0


def test_one_at_mention_rides_the_call_as_the_account_id():
    r = R.route("call @megan", {"mentions": ["acct-77"]})
    assert r.args == {"person": "@megan", "mention": "acct-77"}
    assert "mention" not in R.route("call Megan", {"mentions": ["acct-77"]}).args


@pytest.mark.parametrize("text", [
    "can you call megan and then draft the notes",
    "what did we decide about ADFS?",
    "call megan, then summarise the doc",
    "don't call megan yet",
    "did you call megan",
    "thanks",
    "write a long summary of every meeting we had with the bank last quarter please and thanks",
])
def test_ambiguous_or_complex_requests_are_none(text):
    asked = []
    r = R.route(text, complete=lambda *a: asked.append(a) or {"intent": "call_person", "args": {"person": "megan"},
                                                             "confidence": 1.0})
    assert r.intent == "none" and not asked


@pytest.mark.parametrize("text", ["send the email to Ana", "publish the page", "post the notes to the team",
                                  "email Ana the invite", "share the OeNB page with Ana", "delete the OeNB page"])
def test_outbound_phrasing_is_never_routed_and_never_reaches_the_model(text):
    asked = []
    r = R.route(text, complete=lambda *a: asked.append(a) or {"intent": "open_entity",
                                                             "args": {"query": "OeNB"}, "confidence": 1.0})
    assert r.intent == "none" and r.reason == "outbound_verb" and not asked


def test_invite_and_schedule_are_marked_outbound():
    assert R.route("invite Ana").outbound and R.route("schedule a meeting with Ana").outbound
    assert not R.route("call Ana").outbound


# ── the model step: only when the rules are unsure ──────────────────────────────────────────────

def test_a_confident_model_routes_what_the_rules_were_unsure_of():
    seen = []

    def complete(system, user):
        seen.append(user)
        return {"intent": "call_person", "args": {"person": "megan"}, "confidence": 0.93, "_model": "small-1"}
    r = R.route("give megan a ring", complete=complete)
    assert seen == ["give megan a ring"]
    assert (r.intent, r.args, r.source, r.model) == ("call_person", {"person": "megan"}, "model", "small-1")


@pytest.mark.parametrize("answer,reason", [
    ({"intent": "call_person", "args": {"person": "megan"}, "confidence": 0.5}, "model_low_confidence"),
    ("not json at all", "model_garbage"),
    ({"intent": "call_person", "args": {"person": "megan"}}, "model_garbage"),
    ({"intent": "launch_rockets", "args": {}, "confidence": 1.0}, "model_unknown_intent"),
    ({"intent": "call_person", "args": {"person": "Zed"}, "confidence": 0.99}, "model_invented_args"),
    ({"intent": "call_person", "args": {}, "confidence": 0.99}, "model_missing_args"),
    (None, "model_garbage"),
])
def test_low_confidence_garbage_or_invented_names_fall_through(answer, reason):
    r = R.route("give megan a ring", complete=lambda *_a: answer)
    assert r.intent == "none" and r.reason == reason


def test_a_slow_model_falls_through_inside_the_deadline():
    def slow(*_a):
        time.sleep(1.5)
        return {"intent": "call_person", "args": {"person": "megan"}, "confidence": 1.0}
    t0 = time.perf_counter()
    r = R.route("give megan a ring", complete=slow, timeout_s=0.05)
    assert r.intent == "none" and r.reason == "model_timeout" and (time.perf_counter() - t0) < 1.0


def test_a_raising_model_falls_through():
    def boom(*_a):
        raise RuntimeError("provider down")
    assert R.route("give megan a ring", complete=boom).intent == "none"


# ── aligned with the worker's imperative gate ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["call @megan", "Call Megan", "ring tester-b", "please call Megan Ortiz",
                                  "start a vexa call with Megan"])
def test_every_routed_call_is_one_the_worker_gate_also_names_call_person(text):
    from worker.engine import imperative_preamble
    assert R.route(text).intent == "call_person"
    assert "`call_person`" in imperative_preamble(text)


# ── open_entity: the person's own pages ─────────────────────────────────────────────────────────

def test_find_pages_matches_name_title_and_alias_only_in_the_given_mounts(tmp_path):
    a = tmp_path / "team" / "kg" / "entities" / "company"
    a.mkdir(parents=True)
    (a / "OeNB.md").write_text("---\ntitle: Oesterreichische Nationalbank\naliases: [OeNB, Austrian central bank]\n---\nx")
    (a / "acme-corp.md").write_text("---\ntitle: Acme Corp\n---\n")
    other = tmp_path / "secret" / "kg" / "entities" / "company"
    other.mkdir(parents=True)
    (other / "OeNB.md").write_text("x")
    mounts = [("team", str(tmp_path / "team"))]
    assert R.find_pages(mounts, "oenb") == [{"workspace": "team", "path": "kg/entities/company/OeNB.md",
                                             "title": "OeNB"}]
    assert R.find_pages(mounts, "Austrian central bank")[0]["path"].endswith("OeNB.md")
    assert R.find_pages(mounts, "acme corp")[0]["path"].endswith("acme-corp.md")
    assert R.find_pages(mounts, "nobody") == []
    assert len(R.find_pages(mounts + [("secret", str(tmp_path / "secret"))], "OeNB")) == 2


def test_the_call_event_is_the_one_the_harness_derives_from_the_tool_answer():
    from llm.tool_events import _call_event
    answer = {"session_id": "s-abc123", "room_ref": "[[room:s-abc123]]", "ui_action": "join_call",
              "with": {"subject": "tester-b", "name": "Tester B"}, "already_live": False,
              "call": {"room": "call-fake0001", "meeting_id": 501}, "rang": ["tester-b"]}
    assert R.call_event(answer) == _call_event(answer)
    assert R.call_line(answer) == "Calling Tester B… [[room:s-abc123]]"


# ── the chat route acting on it ─────────────────────────────────────────────────────────────────

def _rig(tmp_path, monkeypatch, features="all,rooms", policies=""):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", features)
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "internal-test-secret-0123456789abcdef")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    if policies:
        (root / "_global" / "POLICIES.md").write_text(f"---\n{policies}\n---\n")
    idx = m.InMemoryMembershipIndex()
    (root / "team").mkdir()
    _git(root / "team")
    m.ensure_owner(root, "team", "alice", index=idx, email="alice@acme.test")
    for who in ("bob", "tester-a", "tester-b"):
        m.grant_membership(root, "team", who, "contributor", added_by="alice", index=idx, email=f"{who}@acme.test")
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"), ui_url="https://app.test")
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    dispatched: list = []

    def _dispatch(inv, **kw):
        dispatched.append(inv)
        return "unit-1"
    dispatcher.dispatch = _dispatch
    lk = _Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret=SECRET)
    _Rooms.rooms = {}
    bus: list = []
    meetings = _Meetings()
    sessions = _Sessions()
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=sessions, session_store=store, membership_index=idx, livekit=lk,
                     call_share_redeemer=lambda *_a: True,
                     notify_publish=lambda ch, payload: bus.append((ch, json.loads(payload))),
                     call_starter=meetings)
    c = TestClient(app, raise_server_exceptions=False)
    return type("W", (), {"c": c, "store": store, "root": root, "bus": bus, "meetings": meetings,
                          "dispatched": dispatched, "sessions": sessions})


@pytest.fixture
def w(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch)
    yield rig
    rig.store.close()


def _events(resp) -> list:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _say(w, prompt, who="alice", session="c-1", path="/api/chat", **extra):
    return w.c.post(path, json={"prompt": prompt, "session": session, **extra}, headers=H(who))


def _prompt(inv) -> str:
    return str(inv["start"]["entrypoint"]["inline"])


def test_call_person_runs_directly_with_no_turn_and_the_terminal_joins(w):
    r = _say(w, "call tester-b", turn_id="t-1")
    assert r.status_code == 200, r.text
    ev = _events(r)
    assert [e["type"] for e in ev] == ["route", "message-delta", "call", "turn-complete"]
    route, line, call = ev[0], ev[1], ev[2]
    assert route["route"] == "call_person" and route["executed"] is True and route["outcome"] == "executed"
    assert route["source"] == "rules" and route["confidence"] == 1.0 and route["latency_ms"] < 1000
    sid = call["chat"]
    assert call["room"].startswith("call-fake") and call["with"] and line["text"].startswith("Calling ")
    assert f"[[room:{sid}]]" in line["text"]
    assert not w.dispatched                                     # no agent turn at all
    assert len(w.meetings.calls) == 1 and w.store.active_call(sid)["owner"] == "alice"
    # the receipt is an entry in the session's own log (arch review V5: no store of its own), and the
    # conversation reads naturally on reload — the receipt is never a chat line, never heard, never unread
    row = w.store.by_name("alice", "c-1")
    receipts = w.store.receipts(row["id"])
    assert receipts[-1]["route"] == "call_person" and receipts[-1]["executed"] is True
    texts = [(n["author"], n["text"]) for n in w.store.notes(row["id"])]
    assert ("alice", "call tester-b") in texts and any(a == "vexa" and "Calling" in t for a, t in texts)
    assert all(a != w.store.RECEIPT_AUTHOR for a, _t in texts)
    assert all(n["author"] != w.store.RECEIPT_AUTHOR for n in w.store.take_unheard(row["id"]))


def test_a_retry_of_the_same_message_replays_and_never_calls_twice(w):
    first = _events(_say(w, "call tester-b", turn_id="t-9"))
    again = _events(_say(w, "call tester-b", turn_id="t-9"))
    assert first == again and len(w.meetings.calls) == 1 and not w.dispatched


@pytest.mark.parametrize("text", ["can you call megan and then draft the notes", "what did we decide about ADFS?"])
def test_ambiguous_requests_go_to_the_full_agent_unchanged(w, text):
    r = _say(w, text)
    assert r.status_code == 200
    ev = _events(r)
    assert ev[0]["type"] == "route" and ev[0]["route"] == "none" and ev[0]["executed"] is False
    assert len(w.dispatched) == 1 and _prompt(w.dispatched[0]).endswith(text)
    assert not w.meetings.calls


def test_the_dispatched_turn_is_byte_identical_with_the_router_off(tmp_path, monkeypatch):
    on = _rig(tmp_path / "on", monkeypatch)
    _say(on, "what did we decide about ADFS?")
    off = _rig(tmp_path / "off", monkeypatch, features="all,rooms,-intent_router")
    _say(off, "what did we decide about ADFS?")
    assert _prompt(on.dispatched[0]) == _prompt(off.dispatched[0])
    on.store.close()
    off.store.close()


def test_someone_outside_the_directory_falls_through_and_nothing_is_made(w):
    ev = _events(_say(w, "call zed"))
    assert ev[0]["executed"] is False and ev[0]["outcome"] == "refused:not_in_directory"
    assert len(w.dispatched) == 1 and not w.meetings.calls and not w.store.list_for("zed")


def test_an_ambiguous_person_falls_through_so_the_agent_asks_one_question(w):
    ev = _events(_say(w, "call tester"))
    assert ev[0]["outcome"] == "refused:ambiguous_person" and len(w.dispatched) == 1 and not w.meetings.calls


def test_calls_feature_off_falls_through(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch, features="all")          # `rooms` is explicit: off under `all`
    ev = _events(_say(rig, "call tester-b"))
    assert ev[0]["executed"] is False and ev[0]["outcome"] == "refused:feature_disabled"
    assert len(rig.dispatched) == 1 and not rig.meetings.calls
    rig.store.close()


def _receipts(rig) -> list:
    row = rig.store.by_name("alice", "c-1")
    return rig.store.receipts(row["id"]) if row else []


def test_receipts_are_session_log_entries_kept_to_the_newest_fifty(w):
    row = w.store.create("alice", name="c-log")
    for i in range(55):
        w.store.add_receipt(row["id"], {"route": "none", "i": i})
    got = w.store.receipts(row["id"])
    assert len(got) == w.store.RECEIPTS_KEPT and got[0]["i"] == 5 and got[-1]["i"] == 54
    assert w.store.notes(row["id"]) == [] and w.store.take_unheard(row["id"]) == []


def test_a_submission_nobody_watches_is_never_routed(w):
    r = _say(w, "call tester-b", path="/api/chat/submit")
    assert r.status_code == 200 and len(w.dispatched) == 1 and not w.meetings.calls
    assert not _receipts(w)


def test_a_turn_other_people_read_is_never_routed(w):
    sid = w.c.post("/api/rooms", json={"workspace": "team", "name": "venue", "visibility": "closed",
                                       "members": ["bob"]}, headers=H("alice")).json()["session_id"]
    r = _say(w, "call tester-b", session=sid, to_agent=True)
    assert r.status_code == 200, r.text
    assert not any(e.get("type") in ("route", "call") for e in _events(r))
    assert not w.meetings.calls


def test_a_routine_turn_is_never_routed(w):
    r = w.c.post("/api/chat", json={"prompt": "call tester-b", "session": "c-r"},
                 headers=H("alice", **{"x-vexa-flow-regime": "autonomous", "x-vexa-flow": "daily",
                                       "x-vexa-flow-step": "agent_run",
                                       "x-internal-secret": "internal-test-secret-0123456789abcdef"}))
    assert not w.meetings.calls
    assert not any(e.get("type") == "route" for e in _events(r))


@pytest.mark.parametrize("text,outcome", [
    ("invite Ana", "outbound_needs_approval"),
    ("invite tester-b to the team workspace", "outbound_needs_approval"),
    ("schedule a call with tester-b tomorrow at 3", "outbound_needs_approval"),
    ("send the email to Ana", "fell_through"),
    ("publish the page", "fell_through"),
])
def test_outbound_asks_are_never_executed_by_the_router(w, text, outcome):
    ev = _events(_say(w, text))
    assert ev[0]["type"] == "route" and ev[0]["executed"] is False and ev[0]["outcome"] == outcome
    # the full agent gets the person's words unchanged — it asks before anything leaves
    assert len(w.dispatched) == 1 and _prompt(w.dispatched[0]).endswith(text)
    # nothing was sent, written to a calendar, called or published
    assert not w.meetings.calls and not w.bus
    assert not any(w.store.room(r["id"]) for r in w.store.list_for("alice"))      # no room was opened
    assert not (w.root / ".invites").exists() or not any((w.root / ".invites").rglob("*.json"))


def test_open_entity_opens_the_one_matching_page(w):
    page = w.root / "team" / "kg" / "entities" / "company"
    page.mkdir(parents=True)
    (page / "OeNB.md").write_text("---\ntitle: OeNB\n---\nThe bank.\n")
    ev = _events(_say(w, "open the OeNB page"))
    assert [e["type"] for e in ev] == ["route", "message-delta", "open", "turn-complete"], ev
    assert ev[2] == {"type": "open", "target": "team/kg/entities/company/OeNB.md", "workspace": "team",
                     "path": "kg/entities/company/OeNB.md"}
    assert "[[page:team/kg/entities/company/OeNB.md]]" in ev[1]["text"] and not w.dispatched


def test_open_entity_with_no_match_falls_through(w):
    ev = _events(_say(w, "open the Nowhere page"))
    assert ev[0]["outcome"] == "not_found" and len(w.dispatched) == 1


def test_the_switch_off_by_policy_means_no_routing_at_all(tmp_path, monkeypatch):
    rig = _rig(tmp_path, monkeypatch, policies="features: {intent_router: off}")
    ev = _events(_say(rig, "call tester-b"))
    assert not any(e.get("type") == "route" for e in ev)
    assert len(rig.dispatched) == 1 and not rig.meetings.calls and not _receipts(rig)
    rig.store.close()


def test_the_receipt_is_logged_with_route_confidence_latency(w, capsys):
    _say(w, "call tester-b")
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    got = [x for x in lines if x.get("event") == "intent_route"]
    assert got and {"route", "confidence", "latency_ms", "source", "executed"} <= set(got[-1]["fields"])


def test_a_routed_first_message_reads_back_in_the_chats_history(w):
    _say(w, "call tester-b")
    h = w.c.get("/api/sessions/c-1/history", headers=H("alice"))
    assert h.status_code == 200, h.text
    said = [(x["author"], x["text"]) for x in h.json().get("messages", [])]
    assert said[0] == ("alice", "call tester-b") and said[1][0] == "vexa" and said[1][1].startswith("Calling ")
