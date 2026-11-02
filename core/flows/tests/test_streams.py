"""Streams: events for one recipient, kept until acknowledged (factory concept, 1 Nov 2026, PRD L3.2).

What it closes: agents learned of work by timer. A recipient (a role such as `ceo`, or a subject) now
reads `GET /streams/{recipient}`, and an item stays there until `POST /streams/{recipient}/ack`, so a
session that restarts reads what it missed. Then `card_due_alarms`: a card's Due puts `card.due_soon`
(one working day before) and `card.due` (on the day) into its owner's stream.
"""
from __future__ import annotations

import calendar
import json

import pytest

from flows import EventType, FakeClock, Registry, admit, keyed, stream, tick
from flows_defs import crm as crm_pack
from flows_steps import stream as stream_steps
from sqlite_double import SqliteDB

from test_subject_bearer import admin as operator, bearer, client, fa, identity, rows  # noqa: F401 — fixtures

T0 = 1_788_000_000.0


# ── the engine ─────────────────────────────────────────────────────────────────────────────────────
def test_an_item_is_appended_once_per_recipient_and_source_event():
    db = SqliteDB()
    a = stream.append(db, T0, recipient="ceo", source_event_id="e1", event_type="card.due",
                      refs={"card": "c1", "_origin": ["x"], "_key": "k"}, reaction_id="r1")
    assert a["status"] == "appended"
    again = stream.append(db, T0 + 5, recipient="ceo", source_event_id="e1", event_type="card.due",
                          refs={"card": "c1"}, reaction_id="r1")
    assert again == {"item_id": a["item_id"], "status": "duplicate"}
    other = stream.append(db, T0, recipient="software", source_event_id="e1", event_type="card.due",
                          refs={"card": "c1"})
    assert other["status"] == "appended" and other["item_id"] != a["item_id"]
    (item,) = stream.unacked(db, "ceo")
    assert item["refs"] == {"card": "c1"}, "the engine's own _ keys are not kept"
    assert item["event_type"] == "card.due" and item["created_at"] == T0


def test_unacked_items_come_oldest_first_and_page_after_an_item():
    db = SqliteDB()
    ids = [stream.append(db, T0 + i, recipient="ceo", source_event_id=f"e{i}", event_type="t",
                         refs={"n": i})["item_id"] for i in (2, 0, 1)]
    assert [i["refs"]["n"] for i in stream.unacked(db, "ceo")] == [0, 1, 2]
    first = stream.unacked(db, "ceo", limit=1)[0]["item_id"]
    assert first == ids[1]
    assert [i["refs"]["n"] for i in stream.unacked(db, "ceo", after=first)] == [1, 2]
    with pytest.raises(stream.StreamError):
        stream.unacked(db, "ceo", after="not-an-id")


def test_ack_removes_items_twice_is_a_no_op_and_writes_an_ack_signal():
    db = SqliteDB()
    db.execute("INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow, "
               "flow_version, step, status, attempt, next_run_at, created_at, updated_at) VALUES "
               "('r1','e1','t','{}','f',1,'to_stream','done',1,0,:t,:t)", {"t": T0})
    a = stream.append(db, T0, recipient="ceo", source_event_id="e1", event_type="t", refs={},
                      reaction_id="r1")["item_id"]
    b = stream.append(db, T0, recipient="ceo", source_event_id="e2", event_type="t", refs={})["item_id"]
    theirs = stream.append(db, T0, recipient="software", source_event_id="e3", event_type="t",
                           refs={})["item_id"]
    out = stream.ack(db, T0 + 1, "ceo", [a, theirs, "00000000000000000-000000000000"],
                     actor="900001", reason="read")
    assert out == {"acked": [a], "already": [], "unknown": [theirs, "00000000000000000-000000000000"]}
    assert [i["item_id"] for i in stream.unacked(db, "ceo")] == [b]
    assert stream.ack(db, T0 + 2, "ceo", [a], actor="900001") == {"acked": [], "already": [a], "unknown": []}
    assert [i["item_id"] for i in stream.unacked(db, "software")] == [theirs], "another stream is untouched"
    (sig,) = db.execute("SELECT kind, actor, reason, created_at FROM signal WHERE reaction_id = 'r1'")
    assert sig[0] == "ack" and sig[1] == "900001" and sig[3] == T0 + 1
    assert json.loads(sig[2]) == {"recipient": "ceo", "item_id": a, "reason": "read"}
    assert db.execute("SELECT acked_at, acked_by FROM stream_item WHERE item_id = :i", {"i": a}) == [(T0 + 1, "900001")]


def test_a_recipient_is_a_plain_name_and_refs_are_bounded():
    db = SqliteDB()
    for bad in ("", "a b", "../ceo", "x" * 201):
        with pytest.raises(stream.StreamError):
            stream.append(db, T0, recipient=bad, source_event_id="e", event_type="t", refs={})
    with pytest.raises(stream.StreamError):
        stream.append(db, T0, recipient="ceo", source_event_id="e", event_type="t",
                      refs={"big": "x" * (stream.MAX_REFS + 1)})
    assert stream.check_recipient("crm:b602f559-ac45-5dd8-a7dd-c1ee4daeab24")


# ── the step, run by the engine ───────────────────────────────────────────────────────────────────
def _rig(params):
    db, clock, reg = SqliteDB(), FakeClock(T0), Registry()
    step = stream_steps.register(reg, db)
    assert stream_steps.register(reg, db) is step, "a second registration answers the bound step"
    reg.flow(name="to_ceo", version=1, on=EventType("factory.test"), steps=[step], params=params)
    return db, clock, reg


def test_to_stream_appends_the_triggering_event_to_the_param_recipient():
    db, clock, reg = _rig({"recipient": "ceo"})
    assert admit(db, reg, clock, source_event_id="ev-1", event_type="factory.test",
                 subject_refs={"card": "c1", "owner": "software"}) == 1
    while tick(db, reg, clock):
        pass
    (item,) = stream.unacked(db, "ceo")
    assert item["event_type"] == "factory.test" and item["refs"] == {"card": "c1", "owner": "software"}
    assert stream.unacked(db, "software") == [], "the param outranks refs.owner"
    assert admit(db, reg, clock, source_event_id="ev-1", event_type="factory.test",
                 subject_refs={"card": "c1"}) == 0
    assert len(stream.unacked(db, "ceo")) == 1


def test_to_stream_falls_back_to_refs_owner_and_refuses_no_recipient():
    db, clock, reg = _rig({})
    admit(db, reg, clock, source_event_id="ev-1", event_type="factory.test", subject_refs={"owner": "business"})
    admit(db, reg, clock, source_event_id="ev-2", event_type="factory.test", subject_refs={"card": "c2"})
    while tick(db, reg, clock):
        pass
    assert [i["refs"] for i in stream.unacked(db, "business")] == [{"owner": "business"}]
    assert db.execute("SELECT status FROM reaction WHERE source_event_id = 'ev-2::to_ceo'") == [("failed",)]


# ── the API ──────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def streams():
    fa.db.execute("DELETE FROM stream_item")
    yield
    fa.db.execute("DELETE FROM stream_item")


def _put(recipient, n):
    return stream.append(fa.db, T0 + n, recipient=recipient, source_event_id=f"probe-{recipient}-{n}",
                         event_type="factory.probe", refs={"n": n})["item_id"]


def test_the_operator_reads_a_stream_and_ack_removes_items(client, streams):  # noqa: F811
    a, b = _put("ceo", 1), _put("ceo", 2)
    r = client.get("/streams/ceo", headers=operator())
    assert r.status_code == 200, r.text
    assert [i["item_id"] for i in r.json()["items"]] == [a, b]
    r = client.post("/streams/ceo/ack", headers={**operator(), "X-Actor": "ceo-session"},
                    json={"ids": [a], "reason": "seen"})
    assert r.json() == {"recipient": "ceo", "acked": [a], "already": [], "unknown": []}
    assert [i["item_id"] for i in client.get("/streams/ceo", headers=operator()).json()["items"]] == [b]
    assert client.post("/streams/ceo/ack", headers=operator(), json={"ids": [a]}).json()["already"] == [a]
    assert fa.db.execute("SELECT acked_by FROM stream_item WHERE item_id = :i", {"i": a}) == [("operator:ceo-session",)]
    assert client.get(f"/streams/ceo?after={b}", headers=operator()).json()["items"] == []


def test_wait_holds_the_request_until_the_deadline_when_nothing_is_there(client, streams, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fa, "STREAM_POLL_S", 0.01)
    r = client.get("/streams/ceo?wait=0.05", headers=operator())
    assert r.status_code == 200 and r.json()["items"] == []
    assert fa.STREAM_WAIT_MAX_S == 25.0


def test_a_person_reads_their_own_stream_and_no_one_elses(client, identity, streams):  # noqa: F811
    mine, ben = _put("126", 1), _put("512", 1)
    r = client.get("/streams/126", headers=bearer("anna-key"))
    assert r.status_code == 200 and [i["item_id"] for i in r.json()["items"]] == [mine]
    assert client.get("/streams/anna@vexa.test", headers=bearer("anna-key")).status_code == 200
    r = client.get("/streams/512", headers=bearer("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_stream"
    assert "items" not in r.text
    r = client.post("/streams/512/ack", headers=bearer("anna-key"), json={"ids": [ben]})
    assert r.status_code == 403
    assert [i["item_id"] for i in stream.unacked(fa.db, "512")] == [ben], "nothing acked"
    r = client.post("/streams/126/ack", headers=bearer("anna-key"), json={"ids": [mine, ben]})
    assert r.json()["acked"] == [mine] and r.json()["unknown"] == [ben]
    assert fa.db.execute("SELECT acked_by FROM stream_item WHERE item_id = :i", {"i": mine}) == [("126",)]


def test_a_role_stream_is_its_declared_owners_and_otherwise_the_operators(client, identity, streams,  # noqa: F811
                                                                         monkeypatch):
    _put("ceo", 1)
    assert client.get("/streams/ceo", headers=bearer("anna-key")).status_code == 403
    monkeypatch.setenv("VEXA_FLOWS_STREAM_OWNERS", json.dumps({"ceo": "126"}))
    r = client.get("/streams/ceo", headers=bearer("anna-key"))
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    assert client.get("/streams/ceo", headers=bearer("ben-key")).status_code == 403
    monkeypatch.setenv("VEXA_FLOWS_STREAM_OWNERS", "not json")
    assert client.get("/streams/ceo", headers=bearer("anna-key")).status_code == 403


def test_no_credential_and_a_bad_recipient_are_refused(client, streams):  # noqa: F811
    assert client.get("/streams/ceo").status_code == 401
    assert client.post("/streams/ceo/ack", json={"ids": ["x"]}).status_code == 401
    assert client.get("/streams/a b", headers=operator()).status_code == 422
    assert client.post("/streams/ceo/ack", headers=operator(), json={"ids": []}).status_code == 422


# ── card_due_alarms ───────────────────────────────────────────────────────────────────────────────
def _utc(y, m, d):
    return float(calendar.timegm((y, m, d, 0, 0, 0)))


def test_one_working_day_before_a_monday_is_the_friday():
    assert crm_pack.due_times("2026-11-09") == (_utc(2026, 11, 6), _utc(2026, 11, 9))
    assert crm_pack.due_times("2026-11-11") == (_utc(2026, 11, 10), _utc(2026, 11, 11))
    assert crm_pack.due_times("2026-11-08") == (_utc(2026, 11, 6), _utc(2026, 11, 8))
    assert crm_pack.due_times("") is None and crm_pack.due_times("soon") is None


def _crm(monkeypatch, cards):
    import flows_steps.common as common
    sent = []
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm-api:8300")
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flows-service-key-crm")

    def http(method, url, headers, body, timeout=20):
        path = url.replace("http://crm-api:8300", "")
        sent.append((path, body))
        if path != "/internal/cards/due":
            return 404, {}
        rec = (body or {}).get("record")
        return 200, {"cards": [c for c in cards if not rec or c["id"] == rec]}
    monkeypatch.setattr(common, "http", http)
    return sent


def _due_rig(now):
    db, clock, reg = SqliteDB(), FakeClock(now), Registry()
    crm_pack.build(reg, db)
    return db, clock, reg


def _run(db, reg, clock):
    while tick(db, reg, clock):
        pass
    from flows_worker.__main__ import keyed_pass
    keyed_pass(db, reg, clock, {"loop_limit": 3, "max_hops": 8, "runs_per_day": 100, "messages_per_day": 100})
    while tick(db, reg, clock):
        pass


def _changed(db, reg, clock, card, rev):
    return admit(db, reg, clock, source_event_id=f"crm-{card}-{rev}", event_type="crm.record.changed",
                 subject_refs={"org_id": "t", "record_id": card, "revision": rev})


def test_a_card_with_a_due_puts_due_soon_then_due_into_its_owners_stream(monkeypatch):
    card = {"id": "c1", "due": "2026-11-09", "owner": "ceo", "state": "Doing", "open": True}
    sent = _crm(monkeypatch, [card])
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    assert _changed(db, reg, clock, "c1", 2) >= 1
    _run(db, reg, clock)
    assert ("/internal/cards/due", {"record": "c1"}) in sent
    armed = dict(db.execute("SELECT key, fire_at FROM keyed_schedule WHERE flow = 'card_due_alarms' "
                            "AND status = 'armed'"))
    assert armed == {"c1:soon": _utc(2026, 11, 6), "c1:due": _utc(2026, 11, 9)}
    assert stream.unacked(db, "ceo") == [], "nothing before its time"

    clock.advance(_utc(2026, 11, 6) - clock.now() + 1)          # Friday, one working day before
    _run(db, reg, clock)
    (soon,) = stream.unacked(db, "ceo")
    assert soon["event_type"] == "card.due_soon"
    assert soon["refs"] == {"card": "c1", "due": "2026-11-09", "owner": "ceo", "fire_at": _utc(2026, 11, 6)}

    clock.advance(_utc(2026, 11, 9) - clock.now() + 1)          # the due date
    _run(db, reg, clock)
    assert [i["event_type"] for i in stream.unacked(db, "ceo")] == ["card.due_soon", "card.due"]
    _run(db, reg, clock)
    assert len(stream.unacked(db, "ceo")) == 2, "each alarm once"


def test_the_founders_due_reaches_the_ceo_and_still_names_the_founder(monkeypatch):
    """2 Nov truth check: card.due for a founder card (d6bbdf24, Waiting founder) said owner «ceo»."""
    card = {"id": "c7", "due": "2026-11-09", "owner": "founder", "state": "To do", "open": True}
    _crm(monkeypatch, [card])
    db, clock, reg = _due_rig(_utc(2026, 11, 9) + 1)
    _changed(db, reg, clock, "c7", 2)
    _run(db, reg, clock)
    (due,) = stream.unacked(db, "ceo")
    assert (due["event_type"], due["refs"]["owner"], due["refs"]["to"]) == ("card.due", "founder", "ceo")
    assert stream.unacked(db, "founder") == []


def test_a_moved_due_moves_the_alarm_and_a_closed_card_cancels_it(monkeypatch):
    card = {"id": "c1", "due": "2026-11-09", "owner": "ceo", "state": "Doing", "open": True}
    _crm(monkeypatch, [card])
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    _changed(db, reg, clock, "c1", 2)
    _run(db, reg, clock)
    card["due"] = "2026-11-12"
    card["owner"] = "software"
    _changed(db, reg, clock, "c1", 3)
    _run(db, reg, clock)
    rows = dict(db.execute("SELECT key, fire_at FROM keyed_schedule WHERE flow = 'card_due_alarms' "
                           "AND status = 'armed'"))
    assert rows == {"c1:soon": _utc(2026, 11, 11), "c1:due": _utc(2026, 11, 12)}
    (payload,) = db.execute("SELECT payload FROM keyed_schedule WHERE key = 'c1:due'")
    assert json.loads(payload[0])["owner"] == "software"
    card["open"] = False
    _changed(db, reg, clock, "c1", 4)
    _run(db, reg, clock)
    assert db.execute("SELECT DISTINCT status FROM keyed_schedule WHERE flow = 'card_due_alarms'") == [("cancelled",)]


def test_an_overdue_card_alarms_now_and_the_hourly_run_finds_cards_and_drops_gone_ones(monkeypatch):
    cards = [{"id": "late", "due": "2026-10-20", "owner": "founder", "state": "Doing", "open": True},
             {"id": "c2", "due": "2026-11-20", "owner": "business", "state": "Doing", "open": True},
             {"id": "nobody", "due": "2026-11-20", "owner": "", "state": "Doing", "open": True}]
    sent = _crm(monkeypatch, cards)
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    run = reg.steps["arm_card_due"]
    from flows import StepCtx
    from flows.model import Reaction
    ctx = StepCtx(reaction=Reaction("r", "s", "schedule.due", {}, crm_pack.DUE_RECHECK_FLOW, 1, "arm_card_due",
                                    "running", 1, 0, None, None, None), effect_key="k", prior={},
                  clock_now=clock.now())
    out = run(ctx)
    assert ("/internal/cards/due", {}) in sent and out.result["cards"] == 3
    _run(db, reg, clock)
    (late,) = stream.unacked(db, "ceo")   # a card waiting on the founder: to the ceo
    assert late["event_type"] == "card.due" and late["refs"]["card"] == "late"
    assert db.execute("SELECT status FROM keyed_schedule WHERE key = 'late:soon'") == [], "no soon for a passed date"
    assert db.execute("SELECT count(*) FROM keyed_schedule WHERE key LIKE 'nobody:%'") == [(0,)], "no owner, no alarm"
    cards.pop(1)                                                  # c2 left the open-with-Due set
    run(ctx)
    assert db.execute("SELECT DISTINCT status FROM keyed_schedule WHERE key LIKE 'c2:%'") == [("cancelled",)]


def test_a_missed_alarm_fires_late_on_the_next_hourly_run(monkeypatch):
    card = {"id": "c1", "due": "2026-11-09", "owner": "ceo", "state": "Doing", "open": True}
    _crm(monkeypatch, [card])
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    _changed(db, reg, clock, "c1", 2)
    _run(db, reg, clock)
    clock.advance(_utc(2026, 11, 6) - clock.now() + keyed.CATCHUP_S + 60)   # the worker was down
    _run(db, reg, clock)
    assert db.execute("SELECT status FROM keyed_schedule WHERE key = 'c1:soon'") == [("missed",)]
    assert stream.unacked(db, "ceo") == []
    _changed(db, reg, clock, "c1", 3)                              # or the hourly run: same step
    _run(db, reg, clock)
    assert [i["event_type"] for i in stream.unacked(db, "ceo")] == ["card.due_soon"]


def test_the_due_flows_are_registered():
    db, clock, reg = _due_rig(T0)
    assert crm_pack.DUE_ARM_FLOW in {f.name for f in reg.match("crm.record.changed")}
    assert {f.name: f.cron for f in reg.schedules()}[crm_pack.DUE_RECHECK_FLOW] == crm_pack.DUE_RECHECK_CRON
    assert reg.newest("instance", crm_pack.DUE_FLOW).steps == ("to_stream",)



# ── subscription by default: a card's change reaches its owner's stream, a source's failure the factory's ──
def _change_crm(monkeypatch, answers):
    import flows_steps.common as common
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm-api:8300")
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flows-service-key-crm")
    monkeypatch.setenv("VEXA_FLOWS_STREAM_OWNERS", json.dumps({"ceo": "900001", "software": "900002"}))

    def http(method, url, headers, body, timeout=20):
        path = url.replace("http://crm-api:8300", "")
        if path == "/internal/cards/change":
            return 200, answers[(body["record"], body["revision"])]
        if path == "/internal/cards/due":
            return 200, {"cards": []}
        return 404, {}
    monkeypatch.setattr(common, "http", http)


def test_a_card_change_reaches_its_owner_once_no_one_else_and_not_again_after_ack(monkeypatch):
    base = {"card": "c1", "created": False, "state": "Review", "owner": "software", "was_owner": "software",
            "open": True}
    _change_crm(monkeypatch, {
        ("c1", 2): {**base, "revision": 2, "changed": ["State"], "actor": "crm:stage"},
        ("c1", 3): {**base, "revision": 3, "changed": [], "actor": "reading:github/delivery"},
        ("c1", 4): {**base, "revision": 4, "changed": ["Holder", "HoldUntil"], "actor": "900002"},
        ("c2", 2): {**base, "card": "c2", "revision": 2, "changed": ["Started"], "owner": "founder",
                    "was_owner": "founder", "actor": "900004"},
    })
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    for card, rev in (("c1", 2), ("c1", 3), ("c1", 4), ("c2", 2)):
        _changed(db, reg, clock, card, rev)
    _run(db, reg, clock)
    _changed(db, reg, clock, "c1", 2)                                      # the same event again
    _run(db, reg, clock)
    (item,) = stream.unacked(db, "software")                               # once; no reading, no own claim
    assert item["event_type"] == "card.changed"
    assert item["refs"] == {"card": "c1", "revision": 2, "changed": ["State"], "state": "Review",
                            "owner": "software", "by": "crm:stage"}
    (press,) = stream.unacked(db, "ceo")                                   # the founder's card: to the ceo
    assert press["refs"]["card"] == "c2" and press["refs"]["changed"] == ["Started"]
    assert stream.ack(db, clock.now(), "software", [item["item_id"]], actor="900002")["acked"] == [item["item_id"]]
    assert stream.unacked(db, "software") == []
    _changed(db, reg, clock, "c1", 2)
    _run(db, reg, clock)
    assert stream.unacked(db, "software") == [], "an acked change is not delivered again"


def test_a_new_owner_and_the_old_one_are_both_told(monkeypatch):
    _change_crm(monkeypatch, {("c1", 5): {"card": "c1", "revision": 5, "created": False, "changed": ["Waiting"],
                                          "state": "Doing", "owner": "business", "was_owner": "software",
                                          "actor": "900001", "open": True}})
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    _changed(db, reg, clock, "c1", 5)
    _run(db, reg, clock)
    assert [len(stream.unacked(db, r)) for r in ("business", "software", "ceo")] == [1, 1, 0]


def test_a_failing_source_reaches_the_factory_once_per_fault():
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    now = clock.now()
    for name, status, failures, reason in (("gmail-info", "degraded", 3, "read failed (401)"),
                                           ("discord", "active", 0, None), ("ga4", "active", 1, "read failed (500)")):
        db.execute("INSERT INTO sensor (scope, name, kind, connection_id, interval_s, version, status, mode, next_due_at, "
                   "failures, reason, created_at) VALUES ('instance', :n, 'reading', :c, 60, 1, :s, 'live', :t, :f, :r, :t)",
                   {"n": name, "c": f"{name}/x", "s": status, "t": now, "f": failures, "r": reason})
    step = reg.steps["source_faults_to_owner"]
    from flows import StepCtx
    from flows.model import Reaction
    ctx = StepCtx(reaction=Reaction("r", "s", "schedule.due", {}, crm_pack.SOURCE_FAULTS_FLOW, 1,
                                    "source_faults_to_owner", "running", 1, 0, None, None, None), effect_key="k",
                  prior={}, clock_now=now)
    step(ctx)
    step(ctx)                                                              # the next run: the same faults
    got = stream.unacked(db, "factory")
    assert sorted(i["refs"]["sensor"] for i in got) == ["ga4", "gmail-info"]
    assert all(i["event_type"] == "source.failing" for i in got)
    db.execute("UPDATE sensor SET reason = 'read failed (403)' WHERE name = 'gmail-info'")
    step(ctx)
    assert len(stream.unacked(db, "factory")) == 3, "a new reason is a new fault"


def test_a_sense_finding_reaches_the_role_once_and_a_bad_one_is_refused():
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    refs = {"role": "software", "row": "card f4bd75e1", "reason": "done_not_in_main", "view": "software",
            "fact": "merged into codex/v0.13.4, default main"}
    for _ in range(2):
        admit(db, reg, clock, source_event_id="sense:software:card f4bd75e1:done_not_in_main:2026-11-02",
              event_type="factory.sense", subject_refs=refs)
        _run(db, reg, clock)
    (item,) = stream.unacked(db, "software")
    assert item["event_type"] == "sense.finding"
    assert item["refs"] == {"row": "card f4bd75e1", "reason": "done_not_in_main", "view": "software",
                            "fact": "merged into codex/v0.13.4, default main"}
    admit(db, reg, clock, source_event_id="sense:x", event_type="factory.sense",
          subject_refs={"role": "anyone", "row": "card 1", "reason": "done_not_in_main"})
    admit(db, reg, clock, source_event_id="sense:y", event_type="factory.sense",
          subject_refs={"role": "ceo", "row": "card 1", "reason": "free text"})
    _run(db, reg, clock)
    assert stream.unacked(db, "anyone") == [] and stream.unacked(db, "ceo") == []


def test_a_truth_check_run_reaches_each_role_once_with_its_own_lies():
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    lie = {"class": "review_without_open_pr", "view": "software", "row": "card c1", "claim": "Review",
           "truth": "no open PR", "source": "gh pr list Vexa-ai/vexa"}
    for role, lies in (("software", [lie]), ("ceo", [lie, {**lie, "row": "card c2"}])):
        for _ in range(2):
            admit(db, reg, clock, source_event_id=f"validate:2026-11-02T12:30:00Z:{role}", event_type="factory.validate",
                  subject_refs={"role": role, "at": "2026-11-02T12:30:00Z", "total_lies": "2",
                                "by_class": {"review_without_open_pr": 2}, "lies": lies})
            _run(db, reg, clock)
    (sw,) = stream.unacked(db, "software")
    assert sw["event_type"] == "validate.result"
    assert (sw["refs"]["total_lies"], sw["refs"]["mine"], sw["refs"]["lies"][0]["row"]) == (2, 1, "card c1")
    assert len(stream.unacked(db, "ceo")) == 1 and stream.unacked(db, "ceo")[0]["refs"]["mine"] == 2
    admit(db, reg, clock, source_event_id="validate:x", event_type="factory.validate",
          subject_refs={"role": "anyone", "total_lies": "1", "by_class": {}})
    _run(db, reg, clock)
    assert stream.unacked(db, "anyone") == []



# ── support cards: a support message makes its thread's one card with the SLA's Due and alarms ─────────────────────
SUPPORT = {"readings": {"discord/bug-reports": "discord", "gmail-dmitry/inbox": "email_customers",
                        "gmail-info/inbox": "email"},
           "discord_guild": "9", "ours": {"discord": [".dima__"], "email_domains": ["vexa.ai"]},
           "sla": {"discord": {"remind_h": 4, "due_h": 8}, "email": {"due_bd": 1},
                   "email_customer": {"remind_h": 4, "due_h": 24}}}


def _support_rig(monkeypatch, now, answers):
    import flows_steps.common as common
    from flows_sensors import reading as rk
    sent = []
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm-api:8300")
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flows-service-key-crm")
    monkeypatch.setattr(rk, "load_config", lambda path="": {"support": SUPPORT})

    def http(method, url, headers, body, timeout=20):
        path = url.replace("http://crm-api:8300", "")
        sent.append((path, body))
        if path.startswith("/internal/cards/support/"):
            return 200, answers(path, body)
        return 200, {"cards": []}
    monkeypatch.setattr(common, "http", http)
    db, clock, reg = _due_rig(now)
    return db, clock, reg, sent


def _discord(db, reg, clock, msg, frm, reply_to=None, at=None):
    refs = {"message": msg, "channel": "2", "from": frm, "at": at,
            "_evidence": {"reading": "discord/bug-reports", "read_at": clock.now()}}
    if reply_to:
        refs["reply_to"] = reply_to
    return admit(db, reg, clock, source_event_id=f"discord-{msg}", event_type="business.support", subject_refs=refs)


def test_a_discord_question_makes_one_card_with_its_sla_and_alarms_into_support(monkeypatch):
    t0 = _utc(2026, 11, 2) + 9 * 3600                                     # Monday 09:00Z
    cards = {}

    def answers(path, body):
        if path.endswith("/inbound"):
            new = body["source"] not in cards
            cards.setdefault(body["source"], "card-1")
            return {"card": cards[body["source"]], "status": "created" if new else "open", "customer": False}
        return {"card": cards.get(body["source"]), "status": "done"}
    db, clock, reg, sent = _support_rig(monkeypatch, t0, answers)
    _discord(db, reg, clock, "300", "anurag2069", at="2026-11-02T09:00:00Z")
    _run(db, reg, clock)
    (path, body), = [x for x in sent if x[0].startswith("/internal/cards/support/")]
    assert path.endswith("/inbound") and body["source"] == "https://discord.com/channels/9/2/300"
    assert body["due"] == "2026-11-02" and body["customers_only"] is False   # L179: due 8 h after
    armed = dict(db.execute("SELECT key, fire_at FROM keyed_schedule WHERE status = 'armed'"))
    assert armed == {"card-1:sla_remind": t0 + 4 * 3600, "card-1:sla_due": t0 + 8 * 3600}

    _discord(db, reg, clock, "301", "anurag2069", reply_to="300", at="2026-11-02T09:10:00Z")   # same thread
    _run(db, reg, clock)
    inbound = [b for p, b in sent if p.endswith("/inbound")]
    assert [b["source"] for b in inbound] == ["https://discord.com/channels/9/2/300"] * 2      # the same card

    clock.advance(4 * 3600 + 1)
    _run(db, reg, clock)
    assert [i["event_type"] for i in stream.unacked(db, "support")] == ["support.remind"]
    assert stream.unacked(db, "support")[0]["refs"]["card"] == "card-1"

    _discord(db, reg, clock, "302", ".dima__", reply_to="300")                                  # our reply
    _run(db, reg, clock)
    assert [p for p, _ in sent][-1] == "/internal/cards/support/reply"
    assert db.execute("SELECT DISTINCT status FROM keyed_schedule WHERE key LIKE 'card-1:sla_%' "
                      "AND status = 'armed'") == [], "our reply cancels the alarms"
    clock.advance(5 * 3600)
    _run(db, reg, clock)
    assert [i["event_type"] for i in stream.unacked(db, "support")] == ["support.remind"], "no deadline after the reply"


def test_a_mail_from_a_non_customer_in_the_founders_mailbox_makes_no_card(monkeypatch):
    db, clock, reg, sent = _support_rig(monkeypatch, _utc(2026, 11, 2) + 9 * 3600,
                                        lambda path, body: {"card": None, "status": "not_a_customer"})
    admit(db, reg, clock, source_event_id="g-1", event_type="business.support",
          subject_refs={"message": "m1", "thread": "t1", "from": "Pat <pat@prospect.test>",
                        "_evidence": {"reading": "gmail-dmitry/inbox", "read_at": clock.now()}})
    _run(db, reg, clock)
    (body,) = [b for p, b in sent if p.endswith("/inbound")]
    assert body["customers_only"] is True and body["source"] == "https://mail.google.com/mail/u/0/#all/t1"
    assert db.execute("SELECT count(*) FROM keyed_schedule WHERE key LIKE '%sla%'") == [(0,)]
    assert stream.unacked(db, "support") == []


def test_a_reading_outside_the_support_list_and_our_own_new_post_are_no_ticket(monkeypatch):
    db, clock, reg, sent = _support_rig(monkeypatch, _utc(2026, 11, 2), lambda p, b: {})
    admit(db, reg, clock, source_event_id="a-1", event_type="business.support",
          subject_refs={"message": "5", "channel": "2", "from": "x", "_evidence": {"reading": "discord/announcements",
                                                                                 "read_at": clock.now()}})
    _discord(db, reg, clock, "6", ".dima__")
    _run(db, reg, clock)
    assert not [p for p, _ in sent if p.startswith("/internal/cards/support/")]


def test_an_outsiders_issue_opens_a_card_and_a_members_later_comment_closes_it(monkeypatch):
    from flows_sensors import reading as rk
    t0 = _utc(2026, 11, 2) + 9 * 3600
    db, clock, reg, sent = _support_rig(monkeypatch, t0, lambda p, b: {'card': 'card-7', 'status': 'created'
                                                                        if p.endswith('/inbound') else 'done'})
    cfg = {'support': {**SUPPORT, 'readings': {'github/vexa-issues': 'github'}, 'sla': {'github': {'due_h': 24}}},
           'delivery': {'connection': 'github'}, 'connections': {'github': {'transport': 'http_json'}}}
    monkeypatch.setattr(rk, 'load_config', lambda path='': cfg)
    comments = []
    issue = {'html_url': 'https://github.com/Vexa-ai/vexa/issues/5', 'title': 'Bot stuck', 'state': 'open',
             'author_association': 'NONE', 'created_at': '2026-11-02T09:00:00Z'}
    monkeypatch.setattr(rk, 'http_get', lambda conn, path, params: comments if path.endswith('/comments') else issue)

    def ev(n):
        return admit(db, reg, clock, source_event_id=f'gh-{n}', event_type='business.reading',
                     subject_refs={'subject': issue['html_url'], 'url': issue['html_url'],
                                   '_evidence': {'reading': 'github/vexa-issues', 'read_at': clock.now()}})
    ev(1)
    _run(db, reg, clock)
    (body,) = [b for p, b in sent if p.endswith('/inbound')]
    assert body['due'] == '2026-11-03' and body['title'] == 'Bot stuck'                 # L178: 24 h
    comments.append({'author_association': 'MEMBER', 'created_at': '2026-11-02T11:00:00Z'})
    ev(2)
    _run(db, reg, clock)
    assert [p for p, _ in sent if p.startswith('/internal/cards/support/')][-1].endswith('/reply')



# ── the card heartbeat: a check that finds nothing new is a missed beat, escalated owner → manager → ceo ──────────
def test_a_new_card_beats_and_missed_beats_go_to_its_owner_then_the_manager_then_the_ceo(monkeypatch):
    import flows_steps.common as common
    t0 = _utc(2026, 11, 2)
    card = {"id": "c1", "owner": "software", "open": True, "state": "Doing", "every_s": 3600,
            "next_check_at": "2026-11-02T01:00:00+00:00", "mark": "m1"}
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm-api:8300")
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flows-service-key-crm")

    def http(method, url, headers, body, timeout=20):
        path = url.replace("http://crm-api:8300", "")
        if path == "/internal/cards/beat":
            return 200, {"cards": [dict(card)]}
        if path == "/internal/cards/change":
            return 200, {"card": None}
        return 200, {"cards": []}
    monkeypatch.setattr(common, "http", http)
    db, clock, reg = _due_rig(t0)
    _changed(db, reg, clock, "c1", 1)                                          # the card is made: its first beat
    _run(db, reg, clock)
    assert dict(db.execute("SELECT key, fire_at FROM keyed_schedule WHERE flow = 'card_heartbeat'")) == {"c1:beat": t0 + 3600}

    def beat():
        clock.advance(3600 + 1)
        _run(db, reg, clock)
    beat()                                                                     # nothing new: 1st miss → owner
    (one,) = stream.unacked(db, "software")
    assert one["event_type"] == "card.beat_missed" and one["refs"] == {"card": "c1", "owner": "software", "misses": 1,
                                                                       "state": "Doing"}
    beat()                                                                     # 2nd → the manager
    assert [i["refs"]["misses"] for i in stream.unacked(db, "factory")] == [2]
    beat()                                                                     # 3rd → the ceo
    assert [i["refs"]["misses"] for i in stream.unacked(db, "ceo")] == [3]
    card["mark"] = "m2"                                                        # a new fact: the count resets
    beat()
    beat()
    assert [i["refs"]["misses"] for i in stream.unacked(db, "software")] == [1, 1]
    card["open"] = False                                                       # closed: it stops beating
    beat()
    assert db.execute("SELECT status FROM keyed_schedule WHERE key = 'c1:beat'") == [("fired",)]
    beat()
    assert len(stream.unacked(db, "software")) == 2


def test_the_hourly_run_arms_open_cards_without_a_beat_and_drops_gone_ones(monkeypatch):
    import flows_steps.common as common
    from flows import StepCtx
    from flows.model import Reaction
    cards = [{"id": "a", "owner": "ceo", "open": True, "every_s": 86400, "next_check_at": None, "mark": "x"}]
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm-api:8300")
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flows-service-key-crm")
    monkeypatch.setattr(common, "http", lambda m, url, h, body, timeout=20: (200, {"cards": [c for c in cards
                        if not (body or {}).get("record") or c["id"] == body["record"]]}))
    db, clock, reg = _due_rig(_utc(2026, 11, 2))
    run = reg.steps["arm_card_beat"]
    ctx = StepCtx(reaction=Reaction("r", "s", "schedule.due", {}, crm_pack.BEAT_RECHECK_FLOW, 1, "arm_card_beat",
                                    "running", 1, 0, None, None, None), effect_key="k", prior={}, clock_now=clock.now())
    run(ctx)
    assert [k for (k,) in db.execute("SELECT key FROM keyed_schedule WHERE status = 'armed'")] == ["a:beat"]
    cards.clear()
    run(ctx)
    assert db.execute("SELECT status FROM keyed_schedule WHERE key = 'a:beat'") == [("cancelled",)]
