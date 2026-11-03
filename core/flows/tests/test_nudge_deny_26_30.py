"""DENY TESTS 26 and 30 (PRD 0.13.4 §5.2), and the M4 fixed-template rule they stand on.

26  A nudge email body holds only template fields: no message text, no private fields.
30  Once a card is seen, its pending email delivery is suppressed.

Both are FLOORS (D38 `nudge_body = template_only`): no workspace or person policy changes them, so the
tests set the most permissive policy there is and still expect the refusal.
"""
from __future__ import annotations

import pytest

from flows import Registry, admit, tick
from flows_defs import nudges
from flows_delivery import run, store, templates
from nudge_rig import rig

V = "126"
SECRET_MESSAGE = "the acquisition price is 4.2M, do not forward"
PRIVATE_FIELD = "+351 912 345 678"
LOUD = {"budget": 12, "idle_min": 5, "digest": "hourly", "quiet": {"on": False},
        "channels": {"email": True}}


def _card(ports, cid="c1", **kw):
    ports.cards[cid] = {"open": True, "next_actor": V, "title": "Sign: Checklist v2",
                        "from_name": "Ana Ortiz", "workspace_name": "Product planning", **kw}
    return f"card:{cid}"


def _to_email(db, clock, ports, ref):
    store.arm(db, clock.now(), person=V, ref=ref, kind="card", at=clock.now(), link=f"/decide/{ref[5:]}")
    run.tick(db, clock, ports)
    clock.advance(5 * 60)
    run.tick(db, clock, ports)


# ── 26: template fields only ────────────────────────────────────────────────────────────────────

def test_26_the_email_body_is_the_template_and_nothing_else():
    db, clock, ports = rig()
    ports.policy = LOUD
    # crm answers more than the template asks for — a private field and the message the card came from
    ref = _card(ports, phone=PRIVATE_FIELD, body=SECRET_MESSAGE, description=SECRET_MESSAGE)
    _to_email(db, clock, ports, ref)
    [(to, subject, body, link)] = ports.sent
    assert SECRET_MESSAGE not in body + subject and PRIVATE_FIELD not in body + subject
    expected = ("Ana Ortiz is waiting on you: “Sign: Checklist v2”\nProduct planning\n\n"
                "Open to decide: https://app.vexa.test/decide/c1\n\n"
                "You get at most 12 of these a day. Change it in Settings → Notifications.\n")
    assert body == expected, "byte for byte the fixed template"


def test_26_a_producer_that_sends_content_is_refused_and_arms_nothing():
    db, clock, ports = rig()
    reg = Registry()
    nudges.build(reg, db)
    admit(db, reg, clock, source_event_id="leak-1", event_type="nudge.requested",
          subject_refs={"person": V, "kind": "mention", "ref": "mention:m1", "who": "Maya",
                        "room_name": "#onboarding", "text": SECRET_MESSAGE})
    while tick(db, reg, clock):
        pass
    assert store.get(db, V, "mention:m1") is None
    [(status, reason)] = db.execute("SELECT status, reason FROM reaction")
    assert status == "failed" and "template fields only" in (reason or "")


@pytest.mark.parametrize("key", sorted(templates.FREE_TEXT_KEYS))
def test_26_the_renderer_has_no_door_for_free_text(key):
    with pytest.raises(templates.FreeTextRefused):
        templates.render_single({"kind": "card", "title": "x", key: SECRET_MESSAGE, "link": "/decide/1"},
                                ui="https://app.vexa.test", budget=6)


def test_26_a_title_cannot_smuggle_a_second_link_or_extra_lines():
    subject, body, url = templates.render_single(
        {"kind": "card", "title": "Approve https://evil.example/pay now\nPS: wire it",
         "who": "Leo\r\nBcc: x@evil", "link": "/decide/c9"}, ui="https://app.vexa.test", budget=6)
    assert "evil.example" not in body and "[link]" in body
    assert "\nPS:" not in body and "\nBcc:" not in body, "one line per field, never a new header or line"
    assert body.count("https://") == 1 and url == "https://app.vexa.test/decide/c9"
    assert "\n" not in subject and "\r" not in body


def test_26_the_link_is_always_this_deployments_terminal():
    for bad in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)", ""):
        _, body, url = templates.render_single({"kind": "card", "title": "t", "link": bad},
                                               ui="https://app.vexa.test", budget=6)
        assert url.startswith("https://app.vexa.test/") and "evil" not in body


def test_26_the_digest_carries_titles_and_names_only():
    subject, body = templates.render_digest(
        [{"kind": "card", "title": "Sign: Checklist v2", "who": "Ana Ortiz", "workspace": "Product planning",
          "link": "/decide/c1"},
         {"kind": "call", "who": "Sara Kim", "room": "#onboarding", "workspace": "Product planning",
          "link": "/r/chat/s1"},
         {"kind": "card", "title": "Approve: ship the newsletter CTA", "who": "Leo Brandt",
          "workspace": "Growth", "link": "/decide/c2"}], ui="https://app.vexa.test")
    assert subject == "3 things wait on you"
    assert body.index("Product planning") < body.index("Growth"), "grouped by workspace"
    assert body.count("Open to decide: https://app.vexa.test/") == 3


# ── 30: seen suppresses the pending email ───────────────────────────────────────────────────────

def test_30_once_a_card_is_seen_its_pending_email_is_suppressed():
    db, clock, ports = rig()
    ports.policy = LOUD
    ref = _card(ports)
    store.arm(db, clock.now(), person=V, ref=ref, kind="card", at=clock.now(), link="/decide/c1")
    run.tick(db, clock, ports)
    assert store.get(db, V, ref)["stage"] == "notified", "an email is pending"
    ports.cards["c1"]["seen"] = True            # rendered in view somewhere (crm's seen relation)
    clock.advance(5 * 60)
    run.tick(db, clock, ports)
    assert ports.sent == [] and store.get(db, V, ref)["outcome"] == "seen"
    assert store.sends(db, V) == [], "nothing was even reserved"


def test_30_seen_also_suppresses_a_pending_digest_line():
    db, clock, ports = rig()
    ports.policy = LOUD
    a, b = _card(ports, "c1"), _card(ports, "c2", title="Approve: ship the CTA")
    for r in (a, b):
        store.arm(db, clock.now(), person=V, ref=r, kind="card", at=clock.now(), link=f"/decide/{r[5:]}")
    run.tick(db, clock, ports)
    clock.advance(5 * 60)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 2
    ports.cards["c1"]["seen"] = True
    clock.advance(3600)
    run.tick(db, clock, ports)
    assert len(ports.sent) == 3 and "/decide/c2" in ports.sent[2][2] and "/decide/c1" not in ports.sent[2][2]
