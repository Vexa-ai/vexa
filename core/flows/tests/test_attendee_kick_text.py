"""The WORDS `process_meeting` kicks the agent turn off with — the one AGENT-SURFACE property the
attendee mail's shape rests on.

These two tests are the siblings of `test_attendee_mail_shape.py` and were split out of it. That
file is about the mail a recipient opens, and every property in it holds in a deployment with no
agent domain at all. These two are about the prompt handed to `ag.dispatch_turn`, which is agent
surface: in the no-agents product (decision 40.6) `process_meeting` never dispatches a turn and so
never composes a kick, and there is nothing here for that product to assert. Splitting them out is
what lets the mail-shape file — eighteen tests of the product's own behaviour — collect and pass
where `core/agent` is absent, instead of being excluded whole for the sake of these two.

Two properties, one test each:

  1. THE KICK ASKS FOR ONE SHARED REPORT. `mail_outbox/attendees-<id>.md` and its `## <address>`
     sections are deleted, so a kick that still asked for them would buy sixty paragraphs nobody
     sends. The report IS the reply.
  2. A GROUP MEETING ADDS THE GROUP DESK, and adds only that — mounted read/write and actively
     maintained, with everything about the single-meeting kick unchanged.

No network, no clock, no DB: the step is called directly with the refs its flow would hand it.
`REFS` and `_ctx` come from the mail-shape module so there is one definition of the room these two
files describe, not two that can drift.
"""
from __future__ import annotations

import flows_defs.production as production
from flows import Registry

from test_attendee_mail_shape import REFS, _ctx
from test_link_loop import _StubDB


# ── the personalisation machinery is GONE, not merely unused ─────────────────────────────────
def test_the_kick_asks_for_one_shared_report_and_no_per_person_file(monkeypatch):
    """`mail_outbox/attendees-<id>.md` and its `## <address>` sections are deleted. A kick that
    still asked for them would produce a file nothing reads — and an agent that spent a turn
    writing sixty paragraphs nobody sends."""
    reg = Registry()
    production.build(reg, _StubDB())
    kicks = []
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, session, prompt, room=None: kicks.append(prompt) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.mt, "room_order",
                        lambda uid, mid, participants, names, cap=12: [])
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 97})
    reg.steps["process_meeting"](_ctx(dict(REFS, native="abc")))

    k = kicks[0]
    assert "mail_outbox/attendees-" not in k
    assert "## _decision" not in k
    assert "## <address>" not in k
    assert "THE REPORT IS SHARED, AND IT IS YOUR REPLY" in k
    # the attribution rule the shared report has to carry, now that the turn can read desks
    assert ("MEETING-RELEVANT FACTS ONLY, ATTRIBUTED — a person's desk informs the report, it is "
            "never quoted into it.") in k
    # and the clause that supersedes the behavior-domain kick's own desk writes (decision 22)
    assert "WRITE NO FILES FOR THIS REPORT" in k
    assert "Your REPLY is the artefact" in k
    # no group in these refs, so not one word about maintaining one
    assert "MAINTAIN" not in k


def test_a_group_meeting_asks_the_turn_to_MAINTAIN_the_group_desk(monkeypatch):
    """The group case is a pure addition (founder decision 22): everything above unchanged, plus
    the group desk mounted read/write and actively maintained — its people, decisions, open items
    and README — rather than an artefact appended to it. A meeting with no group gets none of it,
    which the test above pins from the other side."""
    reg = Registry()
    production.build(reg, _StubDB())
    kicks = []
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, session, prompt, room=None: kicks.append(prompt) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.mt, "room_order",
                        lambda uid, mid, participants, names, cap=12: [])
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 97})
    reg.steps["process_meeting"](_ctx(dict(REFS, native="abc", group="platform-sync")))

    k = kicks[0]
    assert "THIS MEETING BELONGS TO THE GROUP #platform-sync" in k
    assert "MAINTAIN" in k and "READ/WRITE" in k
    for page in ("its PEOPLE", "its DECISIONS", "its OPEN ITEMS", "its README"):
        assert page in k
    assert "Maintaining is not appending" in k
    # ...and it is still the one desk it writes to
    assert "WRITE NO FILES FOR THIS REPORT" in k
    assert "never copy one person's desk into the group's" in k
