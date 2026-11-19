"""THE DELIVERY GATE (PRD 0.13.4 S6; D15 flows owns delivery, D32 the attention budget, M4 fixed bodies).

The S6 acceptance line, as a test: leave Valeriia's card unseen — in-app first, one email after the
idle window; opening the link shows the card; decide, and no second email arrives. Around it: the
cap counts only deliveries outside the Inbox, quiet hours hold in the person's own clock, the digest
batches what is still unseen, rooms interrupt only for what should, and every send is at most once.
"""
from __future__ import annotations

from flows import Registry, admit
from flows_delivery import policy as P, run, store
from flows_defs import nudges
from nudge_rig import T0, rig

V = "126"


def _card(ports, cid="c1", **kw):
    ports.cards[cid] = {"open": True, "next_actor": V, "title": "Interview guide reviewed",
                        "from_name": "Dmitry", "workspace_name": "Growth", **kw}
    return f"card:{cid}"


def _arm(db, clock, ref, kind="card", at=None, **kw):
    return store.arm(db, clock.now(), person=V, ref=ref, kind=kind, at=at if at is not None else clock.now(),
                     link=f"/decide/{ref.split(':', 1)[1]}" if kind == "card" else kw.pop("link", "/r/chat/s1"),
                     **kw)


def _row(db, ref):
    return store.get(db, V, ref)


# ── the S6 acceptance line ──────────────────────────────────────────────────────────────────────

def test_in_app_first_one_email_after_idle_and_nothing_after_it_is_seen():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    assert [r for _, r, _ in ports.in_apps] == [ref] and ports.sent == [], "in-app first, no mail yet"
    clock.advance(9 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [], "the idle window (10 min) has not passed"
    clock.advance(60)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1, "one email after the idle window"
    to, subject, body, link = ports.sent[0]
    assert to == "valeriia@example.test" and subject == "Waiting on you: Interview guide reviewed"
    assert "Open to decide: https://app.vexa.test/decide/c1" in body and link.endswith("/decide/c1")
    assert _row(db, ref)["stage"] == "digest"
    # she opens the link: the landing marks it seen in crm, and nothing else goes out
    ports.cards["c1"]["seen"] = True
    clock.advance(3600)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1, "seen: the digest drops it, no second email"
    assert _row(db, ref)["outcome"] == "seen"


def test_a_decision_before_the_idle_window_sends_no_email_at_all():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    ports.cards["c1"]["acted"] = True
    clock.advance(11 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [] and _row(db, ref)["outcome"] == "acted"


def test_nothing_happens_before_next_check_at():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref, at=T0 + 3600)
    run.tick(db, clock, ports)
    assert ports.in_apps == [] and _row(db, ref)["stage"] == "armed"
    clock.advance(3600)
    run.tick(db, clock, ports)
    assert len(ports.in_apps) == 1


def test_unseen_after_the_email_lands_in_the_next_hourly_digest_once():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    clock.advance(50 * 60)          # 15:00 Lisbon
    run.tick(db, clock, ports)
    assert len(ports.sent) == 2
    subject, body = ports.sent[1][1], ports.sent[1][2]
    assert subject == "1 thing waits on you" and "Growth" in body and "/decide/c1" in body
    assert "Only what is still unseen" in body
    clock.advance(3 * 3600)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 2, "an item rides one digest, then stays in the Inbox"
    assert _row(db, ref)["outcome"] == "digested"


# ── the attention budget (D32): only deliveries outside the Inbox count ─────────────────────────

def test_the_cap_counts_emails_not_in_app_notices_and_overflow_waits_for_the_digest():
    db, clock, ports = rig()
    ports.policy = {"budget": 2, "digest": "daily", "digest_at": "18:00"}
    refs = [_card(ports, f"c{i}") for i in range(4)]
    for r in refs:
        _arm(db, clock, r)
    run.tick(db, clock, ports)
    assert len(ports.in_apps) == 4, "in-app notices are free"
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 2, "budget 2: two emails, the rest wait"
    assert store.sent_today(db, V, P.local_day(clock.now(), ports.tz)) == 2
    assert {_row(db, r)["stage"] for r in refs} == {"digest"}
    clock.advance(4 * 3600)         # 18:20 Lisbon: digest time, but today's budget is spent
    run.tick(db, clock, ports)
    assert len(ports.sent) == 2, "the digest is a delivery too: it waits for tomorrow"
    clock.advance(14 * 3600)        # 08:20 next day, after quiet hours
    run.tick(db, clock, ports)
    assert len(ports.sent) == 3 and ports.sent[2][1] == "4 things wait on you", "one digest, four items"


def test_global_floor_and_ceiling_clamp_the_persons_rule():
    lim = P.Limits(idle_floor_min=5, budget_ceiling=12)
    pol = P.normalize({"idle_min": 1, "budget": 500}, lim)
    assert pol["idle_min"] == 5 and pol["budget"] == 12
    pol = P.normalize({"budget": 0, "idle_min": 99999}, lim)
    assert pol["budget"] == 1 and pol["idle_min"] == 240
    pol = P.normalize({"channels": {"email": True, "whatsapp": True}}, lim)
    assert pol["channels"]["whatsapp"] is False, "WhatsApp is 0.13.5 (decision 8): never sent"
    assert P.normalize("garbage", lim) == P.normalize({}, lim)


# ── quiet hours, in the person's own clock ──────────────────────────────────────────────────────

def test_quiet_hours_hold_the_email_until_morning_local_time():
    db, clock, ports = rig()
    clock.advance(8 * 3600 + 55 * 60)     # 22:55 Lisbon
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    assert len(ports.in_apps) == 1, "the Inbox is always on"
    clock.advance(10 * 60)                # 23:05
    run.tick(db, clock, ports)
    assert ports.sent == []
    row = _row(db, ref)
    assert row["stage"] == "notified" and row["wake_at"] == P.utc(2026, 10, 17, 7, 0), "08:00 Lisbon"
    clock.advance(row["wake_at"] - clock.now())
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1


def test_quiet_hours_follow_the_persons_zone_not_utc():
    pol = P.normalize({"quiet": {"on": True, "from": "22:00", "to": "08:00"}})
    at = P.utc(2026, 10, 16, 21, 30)      # 21:30 UTC
    assert P.in_quiet(at, pol, "Europe/Lisbon") is True, "22:30 in Lisbon"
    assert P.in_quiet(at, pol, "America/New_York") is False, "17:30 in New York"
    assert P.in_quiet(at, P.normalize({"quiet": {"on": False}}), "Europe/Lisbon") is False


# ── rooms: what interrupts ──────────────────────────────────────────────────────────────────────

def _event(reg_db, refs, event="nudge.requested"):
    db, clock = reg_db
    reg = Registry()
    nudges.build(reg, db)
    reg.refresh_from_db(db)
    from flows import tick
    n = admit(db, reg, clock, source_event_id=f"e-{refs.get('ref')}-{refs.get('revision')}-{refs.get('next_actor')}-{event}", event_type=event, subject_refs=refs)
    while tick(db, reg, clock):
        pass
    return n


def test_a_plain_room_message_never_interrupts():
    db, clock, ports = rig()
    _event((db, clock), {"person": V, "kind": "message", "ref": "msg:m1", "who": "Maya", "room_name": "#onboarding"})
    assert store.get(db, V, "msg:m1") is None


def test_a_mention_rides_the_notice_agent_api_already_wrote():
    db, clock, ports = rig()
    ports.notices["nm1"] = {"read": False}
    _event((db, clock), {"person": V, "kind": "mention", "ref": "mention:m1", "who": "Maya",
                         "room_name": "#onboarding", "workspace_name": "Product planning",
                         "room": "s1", "link": "/r/chat/s1", "notice_id": "nm1"})
    run.tick(db, clock, ports)
    assert ports.in_apps == [], "not posted twice"
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1 and ports.sent[0][1] == "Maya mentioned you"
    assert "Maya mentioned you in #onboarding" in ports.sent[0][2]


def test_a_room_set_to_nothing_does_not_interrupt_but_the_item_stays_in_the_inbox():
    db, clock, ports = rig()
    ports.policy = {"rooms": {"room:s1": "none"}}
    ports.notices["nm1"] = {"read": False}
    _event((db, clock), {"person": V, "kind": "mention", "ref": "mention:m1", "who": "Maya",
                         "room_name": "#garden", "room": "s1", "link": "/r/chat/s1", "notice_id": "nm1"})
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [] and store.get(db, V, "mention:m1")["outcome"] == "muted"


def test_a_workspace_level_applies_when_the_room_has_none():
    pol = P.normalize({"rooms": {"ws:growth": "none", "room:s2": "all"}})
    assert P.level_for(pol, room="s1", workspace="growth") == "none"
    assert P.level_for(pol, room="s2", workspace="growth") == "all"
    assert P.interrupts("message", "all") is False
    assert all(P.interrupts(k, "mentions") for k in P.INTERRUPTING)


def test_a_missed_call_waits_for_the_digest_and_is_dropped_if_answered():
    db, clock, ports = rig()
    ports.notices["nc1"] = {"read": False}
    _event((db, clock), {"person": V, "kind": "call", "ref": "call:k1", "who": "Sara",
                         "room_name": "#onboarding", "link": "/r/chat/s1", "notice_id": "nc1"})
    run.tick(db, clock, ports)
    assert store.get(db, V, "call:k1")["stage"] == "digest" and ports.sent == []
    clock.advance(3600)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1 and "Missed call from Sara in #onboarding" in ports.sent[0][2]


# ── the card envelope (S5 → flows) ──────────────────────────────────────────────────────────────

def _card_fact(db, clock, **kw):
    refs = {"object": "card", "org_id": "o1", "record_id": "c1", "revision": 3, "kind": "commitment",
            "state": "offered", "next_actor": V, "next_check_at": "2026-10-16T13:00:00Z", **kw}
    return _event((db, clock), refs, event="crm.record.changed")


def test_a_card_fact_arms_its_next_actor_and_a_closed_card_ends_it():
    db, clock, ports = rig()
    _card_fact(db, clock)
    row = store.get(db, V, "card:c1")
    assert row["stage"] == "armed" and row["wake_at"] == T0 and row["link"] == "/decide/c1"
    _card_fact(db, clock, revision=4, state="done")
    assert store.get(db, V, "card:c1")["outcome"] == "closed"


def test_a_new_next_actor_ends_the_previous_ones_nudge():
    db, clock, ports = rig()
    _card_fact(db, clock)
    _card_fact(db, clock, revision=4, next_actor="200")
    assert store.get(db, V, "card:c1")["outcome"] == "reassigned"
    assert store.get(db, "200", "card:c1")["stage"] == "armed"


def test_a_moved_check_moves_the_row_and_a_later_check_rearms_a_finished_one():
    db, clock, ports = rig()
    ref = _card(ports)
    assert _arm(db, clock, ref, at=T0 + 60) == "armed"
    assert _arm(db, clock, ref, at=T0 + 120) == "moved"
    assert _arm(db, clock, ref, at=T0 + 120) == "unchanged"
    store.finish(db, clock.now(), V, ref, "seen")
    assert _arm(db, clock, ref, at=T0 - 60) == "unchanged", "a redelivered old check changes nothing"
    clock.advance(10)
    assert _arm(db, clock, ref, at=T0 + 86400) == "rearmed"


# ── failure: never a guess, never twice, always attributable ────────────────────────────────────

def test_an_unreachable_crm_sends_nothing_and_asks_again():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    ports.attention_down = True
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [] and _row(db, ref)["stage"] == "notified"
    ports.attention_down = False
    clock.advance(60)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 1


def test_identity_down_defers_rather_than_guessing_preferences():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    ports.person_down = True
    run.tick(db, clock, ports)
    assert ports.in_apps == [] and _row(db, ref)["stage"] == "armed"


def test_a_receipt_taken_before_a_crash_means_the_email_is_not_sent_twice():
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    clock.advance(10 * 60)
    # a previous worker reserved this exact email and died before settling it
    row = _row(db, ref)
    sid = f"{V}|{ref}|single|v{row['version']}|a{row['attempts']}"
    assert store.reserve(db, clock.now(), send_id=sid, person=V, day="2026-10-16", kind="single", refs=[ref])
    run.tick(db, clock, ports)
    assert ports.sent == [] and _row(db, ref)["stage"] == "digest"


def test_a_transport_failure_retries_then_tells_the_owner():
    from flows.model import StepError
    db, clock, ports = rig()
    ref = _card(ports)
    _arm(db, clock, ref, owner="7")
    run.tick(db, clock, ports)
    ports.mail_error = StepError("mail:connect smtp smtp.example:587 — refused", retryable=True)
    for _ in range(3):
        clock.advance(10 * 60)
        run.tick(db, clock, ports)
    assert ports.sent == [] and _row(db, ref)["outcome"] == "failed"
    assert ports.failures == [("7", ref)], "delivery_failed: the owner sees it"
    assert [s["status"] for s in store.sends(db, V)] == ["failed"] * 3
    assert store.sent_today(db, V, "2026-10-16") == 0, "a failed send spends no budget"


def test_no_address_ends_quietly():
    db, clock, ports = rig()
    ports.address = ""
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [] and _row(db, ref)["outcome"] == "no_address"


def test_email_off_keeps_the_inbox_only():
    db, clock, ports = rig()
    ports.policy = {"channels": {"email": False}}
    ref = _card(ports)
    _arm(db, clock, ref)
    run.tick(db, clock, ports)
    clock.advance(10 * 60)
    run.tick(db, clock, ports)
    assert len(ports.in_apps) == 1 and ports.sent == [] and _row(db, ref)["outcome"] == "email_off"
