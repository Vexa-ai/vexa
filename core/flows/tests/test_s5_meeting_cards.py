"""S5 / E2 (PRD 0.13.4 §8.3): a call's follow-ups become cards, on a RECORDED transcript fixture.

The fixture (`fixtures/s5-call-valeriia/`) is a synthetic 3-minute call between the meeting owner
and Valeriia and the write-up the post-meeting turn produced for it. The founder's check: two
commitment cards appear — one to each person who committed — while the unowned follow-up and the
open item become questions to the owner, every card is offered as the room's workspace principal
with origin `meeting:<id>`, and the page cites the cards instead of restating them.
"""
from __future__ import annotations

import json
from pathlib import Path

import flows_defs.production as production

FIX = Path(__file__).parent / "fixtures" / "s5-call-valeriia"
CALL = json.loads((FIX / "transcript.json").read_text())
REPORT = (FIX / "report.md").read_text()
PEOPLE = [("3", "founder@vexa.test", "Dmitry"), ("17", "valeriia@unicorn.test", "Valeriia")]


def test_the_plan_is_two_commitments_and_two_questions():
    plan = production.plan_meeting_cards(REPORT, PEOPLE, "3", CALL["day"])
    commitments = [c for c in plan if c["kind"] == "commitment"]
    questions = [c for c in plan if c["kind"] == "question"]
    assert [(c["next_actor"], c["title"], c["due"]) for c in commitments] == [
        ("17", "Send the venue contract by Friday", "2026-10-23"),   # the meeting was Sat 17 Oct
        ("3", "Send the sponsor deck tomorrow", "2026-10-18")]
    assert all(c["requester"] == "3" for c in plan)
    assert [(c["next_actor"], c["title"]) for c in questions] == [
        ("3", "Who owns this? Book the catering for about thirty people"),
        ("3", "Do we stream the meetup?")]


def test_every_commitment_is_grounded_in_the_recorded_call():
    """A card goes to someone who actually spoke the commitment in the transcript."""
    said = {}
    for seg in CALL["segments"]:
        said.setdefault(seg["speaker"], []).append(seg["text"].lower())
    names = {uid: name for uid, _a, name in PEOPLE}
    for c in production.plan_meeting_cards(REPORT, PEOPLE, "3", CALL["day"]):
        if c["kind"] != "commitment":
            continue
        words = [w for w in c["title"].lower().split() if len(w) > 4][:3]
        assert any(all(w in line for w in words) for line in said[names[c["next_actor"]]]), c


def test_cards_are_offered_as_the_room_principal_and_the_page_cites_them(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm.test")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://identity.test")
    monkeypatch.setattr(production._common, "require_internal_secret", lambda: "s", raising=False)
    import flows_steps.common as common
    monkeypatch.setattr(common, "require_internal_secret", lambda: "s")
    calls = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append((method, url, body))
        if url.endswith("/internal/principals/workspace"):
            return 200, {"id": 900, "kind": "workspace", "workspace_id": body["workspace_id"]}
        if url.endswith("/internal/cards/offer"):
            return 200, {"cards": [{"id": f"c{n}", "title": c["title"]} for n, c in enumerate(body["cards"])]}
        return 404, {}
    monkeypatch.setattr(common, "http", fake_http)
    cited, cards = production.offer_meeting_cards(REPORT, PEOPLE, "3", CALL["workspace"], CALL["meeting_id"],
                                                  CALL["day"], "Venue and sponsor deck")
    (_, _, principal_body), (_, _, offer) = calls
    assert principal_body == {"workspace_id": "ai-tinkerers", "bills_to": 3}
    assert offer["principal"] == "900" and offer["workspace"] == "ai-tinkerers" and offer["on_behalf_of"] == "3"
    assert offer["origin"] == "meeting:153" and offer["session"] == "meet-153"
    assert [c["kind"] for c in offer["cards"]].count("commitment") == 2
    assert all(c["about"] == "meeting:153" for c in offer["cards"])
    assert "- [[Valeriia]] — send the venue contract by Friday [[card:c0]]" in cited
    assert "- [[Dmitry]] — send the sponsor deck tomorrow [[card:c1]]" in cited
    assert "- Do we stream the meetup? [[card:c3]]" in cited
    assert len(cards) == 4
    # a re-run on the cited page offers nothing again
    assert production.plan_meeting_cards(cited, PEOPLE, "3", CALL["day"]) == []


def test_no_room_or_no_principal_offers_nothing_and_never_as_the_owner(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm.test")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://identity.test")
    import flows_steps.common as common
    monkeypatch.setattr(common, "require_internal_secret", lambda: "s")
    seen = []
    monkeypatch.setattr(common, "http", lambda m, u, h, b=None, timeout=20: seen.append(u) or (503, {}))
    assert production.offer_meeting_cards(REPORT, PEOPLE, "3", "", 153, CALL["day"]) == (REPORT, [])
    assert seen == []                                           # no room: no call at all
    assert production.offer_meeting_cards(REPORT, PEOPLE, "3", "ai-tinkerers", 153, CALL["day"]) == (REPORT, [])
    assert not any(u.endswith("/internal/cards/offer") for u in seen)   # no principal: no offer


def test_without_a_crm_nothing_is_offered(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_CRM_API_URL", raising=False)
    assert production.offer_meeting_cards(REPORT, PEOPLE, "3", "ai-tinkerers", 153, CALL["day"]) == (REPORT, [])


def test_due_dates_are_only_the_stated_ones():
    assert production.due_from("by 2026-11-02", "2026-10-17") == "2026-11-02"
    assert production.due_from("send it today", "2026-10-17") == "2026-10-17"
    assert production.due_from("on Monday", "2026-10-17") == "2026-10-19"     # 17 Oct 2026 is a Saturday
    assert production.due_from("by Saturday", "2026-10-17") == "2026-10-24"   # the NEXT one, never the same day
    assert production.due_from("soon", "2026-10-17") is None


def test_the_drop_writes_a_page_that_cites_the_cards_on_every_desk(monkeypatch):
    """Through the real step: every desk's copy of the page carries the same `[[card:…]]` refs."""
    from test_attendee_drop import PRIOR, REFS, Store, _ctx, _rig
    import flows_steps.common as common
    store = Store()
    reg = _rig(monkeypatch, store)
    monkeypatch.setenv("VEXA_FLOWS_CRM_API_URL", "http://crm.test")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://identity.test")
    monkeypatch.setattr(common, "require_internal_secret", lambda: "s")
    offers = []

    def fake_http(method, url, headers, body=None, timeout=20):
        if url.endswith("/internal/principals/workspace"):
            return 200, {"id": 901}
        if url.endswith("/internal/cards/offer"):
            offers.append(body)
            return 200, {"cards": [{"id": f"k{n}"} for n, _ in enumerate(body["cards"])]}
        return 404, {}
    monkeypatch.setattr(common, "http", fake_http)
    refs = {**REFS, "workspace": "pilot"}
    scratch: dict = {}
    out = reg.steps["drop_to_attendees"](_ctx(refs, PRIOR, scratch=scratch))
    assert out.result["cards"] == [{"id": "k0", "kind": "commitment", "next_actor": "uid-ben"}]
    for who in ("anna@bank.test", "ben@bank.test", "cara@bank.test"):
        assert "- Ben — the migration doc [[card:k0]]" in store.of(who)
    reg.steps["drop_to_attendees"](_ctx(refs, PRIOR, scratch=scratch))       # a retry inside the run
    assert len(offers) == 1
