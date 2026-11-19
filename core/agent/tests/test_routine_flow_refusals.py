"""A routine (a person's flow) refused the same way three runs in a row is paused, and says why in
its own chat and in the Routines list (ADR-0046; the rule the old scheduler path had)."""
from __future__ import annotations

import json

from control_plane import routine_flow_refusals as rfr
from control_plane import routine_flows as rf
from tests.test_routine_flows import FakeFlows, _as, flows, world  # noqa: F401 — fixtures

TURN = {"flow": "check-email", "scope": "user:7", "version": 1, "step": "agent_run"}


class Store:
    def __init__(self):
        self.rows, self.notes = {}, []

    def by_name(self, owner, name):
        return self.rows.get((owner, name))

    def create(self, owner, *, name=None, **_):
        row = {"id": f"s-{len(self.rows)}", "owner": owner, "name": name, "flow": None, "title": ""}
        self.rows[(owner, name)] = row
        return row

    def update(self, sid, **kw):
        for r in self.rows.values():
            if r["id"] == sid:
                r.update({k: v for k, v in kw.items() if v is not None})

    def add_note(self, sid, author, text):
        self.notes.append((sid, author, text))


def _runs(*reasons):
    events, n = [], 0
    for reason in reasons:
        n += 1
        events.append((f"{n}-0", {"type": "message-delta", "text": "…"}))
        n += 1
        events.append((f"{n}-0", {"type": "done", **({"refused": {"tool": "gmail_inbox",
                                                                   "reason": reason}} if reason else {})}))
    return events


def _reader(events):
    return lambda unit, cursor: [e for e in events if not cursor or e[0] > cursor]


def _flows():
    f = FakeFlows()
    f.submit("7", "check-email", "*/15 * * * *", "read my mail")
    return f


def test_three_refused_runs_in_a_row_pause_the_routine_and_say_why(tmp_path):
    flows, store = _flows(), Store()
    (tmp_path / "7").mkdir()
    out = rfr.before_run("7", TURN, "u-auto", read=_reader(_runs("policy", "policy", "policy")),
                         workspaces_dir=tmp_path, client=flows, store=store)
    assert out and out["refused"] == "routine_paused" and out["reason"] == "policy"
    assert flows.rows[-1]["status"] == "retired", "the flow is paused"
    assert store.notes and "policy" in store.notes[-1][2], "the reason is in the routine's chat"
    card = rf.card_from_flow("7", flows.rows[-1], state=rf.load_flow_state(tmp_path, "7"))
    assert "3 runs in a row" in card["paused_reason"] and card["enabled"] is False


def test_a_run_that_was_not_refused_resets_the_count(tmp_path):
    flows = _flows()
    (tmp_path / "7").mkdir()
    out = rfr.before_run("7", TURN, "u", read=_reader(_runs("policy", "policy", None, "policy")),
                         workspaces_dir=tmp_path, client=flows)
    assert out is None and flows.rows[-1]["status"] == "active"


def test_runs_are_counted_once_across_calls(tmp_path):
    flows = _flows()
    (tmp_path / "7").mkdir()
    events = _runs("policy", "policy")
    assert rfr.before_run("7", TURN, "u", read=_reader(events), workspaces_dir=tmp_path,
                          client=flows) is None
    assert rfr.before_run("7", TURN, "u", read=_reader(events), workspaces_dir=tmp_path,
                          client=flows) is None, "the same two runs are not counted twice"
    events += [("9-0", {"type": "done", "refused": {"reason": "policy"}})]
    assert rfr.before_run("7", TURN, "u", read=_reader(events), workspaces_dir=tmp_path,
                          client=flows)["refused"] == "routine_paused"


def test_only_a_persons_own_routine_turn_is_counted(tmp_path):
    flows = _flows()
    for turn in ({**TURN, "step": "deliver_message"}, {**TURN, "scope": "instance"},
                 {**TURN, "scope": "user:8"}):
        assert rfr.before_run("7", turn, "u", read=_reader(_runs("p", "p", "p")),
                              workspaces_dir=tmp_path, client=flows) is None
    assert flows.rows[-1]["status"] == "active"


def test_an_unreadable_stream_counts_nothing(tmp_path):
    (tmp_path / "7").mkdir()
    assert rfr.before_run("7", TURN, "u", read=lambda u, c: None, workspaces_dir=tmp_path,
                          client=_flows()) is None


def test_a_notice_lands_in_the_chat_the_routine_runs_in(tmp_path):
    store = Store()
    assert rf.post_to_routine_chat(store, "7", "check-email", "Not run: daily limit", session="meet-86")
    (owner, name), row = next(iter(store.rows.items()))
    assert name == "meet-86" and row["flow"] == "user:7/check-email"
    assert store.notes[-1][1:] == ("vexa", "Not run: daily limit")


def test_a_flow_notice_is_a_line_not_a_turn(world):  # noqa: F811
    client, _, _ = world
    headers = {"X-User-Id": "7", "X-Internal-Secret": "agent-test-internal-secret",
               "X-Vexa-Flow": "user:7/brief", "X-Vexa-Flow-Step": "notice",
               "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow-Name": "brief",
               "X-Vexa-Flow-Scope": "user:7", "X-Vexa-Flow-Version": "0"}
    r = client.post("/api/chat/submit", json={"prompt": "Not run: daily limit", "session": "pchat-x"},
                    headers=headers)
    assert r.status_code == 200 and r.json()["ok"] is True and "unit" not in r.json()
    rows = client.get("/api/sessions", headers=_as()).json()
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    row = next(x for x in rows if x.get("session") == "pchat-x")
    assert (row.get("routine") or {}).get("flow") == "brief", "the chat is the routine's at once"


def test_a_concern_chat_moves_stage_and_the_rail_reads_it(world):  # noqa: F811
    client, _, _ = world
    headers = {"X-User-Id": "7", "X-Internal-Secret": "agent-test-internal-secret",
               "X-Vexa-Flow": "concern", "X-Vexa-Flow-Step": "stage", "X-Vexa-Flow-Regime": "autonomous",
               "X-Vexa-Flow-Name": "concern", "X-Vexa-Flow-Scope": "user:7", "X-Vexa-Flow-Version": "0"}
    for stage in ("waiting", "resolved"):
        r = client.post("/api/chat/submit", json={"prompt": f"Stage: {stage}.", "session": "concern-abc"},
                        headers={**headers, "X-Vexa-Concern-Stage": stage})
        assert r.status_code == 200 and r.json()["stage"] == stage
    rows = client.get("/api/sessions", headers=_as()).json()
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    assert next(x for x in rows if x["session"] == "concern-abc")["concern"]["stage"] == "resolved"
    bad = client.post("/api/chat/submit", json={"prompt": "x", "session": "concern-abc"},
                      headers={**headers, "X-Vexa-Concern-Stage": "lost"})
    assert bad.status_code == 422
    person = client.post("/api/chat/submit", json={"prompt": "x", "session": "concern-abc"},
                         headers={"X-Vexa-Flow-Step": "stage", "X-Vexa-Concern-Stage": "archived", **_as()})
    assert person.status_code in (403, 501), "only the internal tier moves a stage this way"


def test_a_concern_keeps_its_key_and_flow_while_its_stage_moves():
    from control_plane.api_shared import _concern_of
    a = _concern_of({"key": "meeting_id:m-1", "flow": "meeting-follow-up", "stage": "opened"}, None)
    b = _concern_of({"stage": "waiting"}, json.dumps(a))
    assert b["key"] == "meeting_id:m-1" and b["flow"] == "meeting-follow-up" and b["stage"] == "waiting"
    assert _concern_of({"stage": "nonsense"}, json.dumps(b))["stage"] == "waiting"


def test_a_sinks_event_card_is_a_line_and_never_a_routine_mark(world):  # noqa: F811
    client, _, _ = world
    headers = {"X-User-Id": "7", "X-Internal-Secret": "agent-test-internal-secret",
               "X-Vexa-Flow": "user:7/acme-mail", "X-Vexa-Flow-Step": "notice",
               "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow-Name": "acme-mail",
               "X-Vexa-Flow-Scope": "user:7", "X-Vexa-Flow-Version": "1", "X-Vexa-Flow-Kind": "sink"}
    r = client.post("/api/chat/submit", json={"prompt": "⇢ mail.received · from ann", "session": "pchat-s"},
                    headers=headers)
    assert r.status_code == 200 and "unit" not in r.json()
    rows = client.get("/api/sessions", headers=_as()).json()
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    assert not any(x.get("session") == "pchat-s" and x.get("routine") for x in rows)


def test_a_held_write_is_not_a_refusal_and_never_counts_toward_a_pause():
    """app.dev 2026-10-18: a routine that maintains a shared workspace from the person's mail holds
    its write for their confirm on every run. That is waiting on the person, not a refused run."""
    held = {"type": "done", "refused": {"tool": "entity_upsert", "reason": "private_sources_to_shared"}}
    refused = {"type": "done", "refused": {"tool": "gmail_search", "reason": "human_session_required"}}
    out = rfr.count_runs({}, [("1-0", held), ("2-0", held), ("3-0", held)])
    assert out["count"] == 0 and out["reason"] == "" and out["cursor"] == "3-0"
    out = rfr.count_runs({}, [("1-0", refused), ("2-0", held), ("3-0", refused)])
    assert out == {"reason": "human_session_required", "count": 1, "cursor": "3-0"}
