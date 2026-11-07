"""THE POST-MEETING TURN IS THE FIRST AGENT THAT SEES A JOB (Vexa-ai/vexa#1614).

Founder, 2026-09-06, on the empty chat: *"that is a short list that is updated by other agents when
they see something as JTBD, can have up to 10 items"*. The report the meeting produced names, under
`Committed`, what each person took on — so `drop_to_attendees`, which is already walking every desk
in the room with that report in hand, files one short-list item per commitment it named for them.

Plain code, no model: this step is deliberately LLM-free (decision 22 addendum), and a turn per
person per meeting to decide what to put on a chip is exactly the per-person cost that refuses.

Four properties:

  1. THE COMMITMENT REACHES THE PERSON IT NAMES — and nobody else's desk.
  2. THE WORDS ARE THE REPORT'S. A chip that paraphrases would disagree with the page behind it.
  3. THE OWNER POSITION IS WHAT IS READ. A bullet that merely MENTIONS somebody is not their job:
     a chip telling you to do somebody else's work is worse than no chip at all.
  4. A FAILED FILING NEVER COSTS THE DROP. The meeting's record is the step's real work.
"""
from __future__ import annotations

import flows_defs.production as production
import pytest
from flows import Done, Reaction, Registry, StepCtx

from test_attendee_drop import PRIOR, REFS, Store, _rig  # noqa: F401 — the drop's own rig, reused


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    from test_link_loop import FakeScaffolds
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())


def _ctx(refs: dict, prior: dict | None = None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {}, clock_now=1_700_000_000.0,
                   scratch={}, flow=None)


class Filed:
    """Every proposal the step tried to file, as `(uid, source, act, label, by)`."""

    def __init__(self, fail=False):
        self.rows: list[tuple] = []
        self.fail = fail

    def __call__(self, uid, *, source, act, source_label="", by=""):
        if self.fail:
            raise RuntimeError("agent-api said no")
        self.rows.append((uid, source, act, source_label, by))
        return {"id": "x", "added": True}

    def acts_for(self, email: str) -> list:
        uid = "uid-" + email.split("@")[0]
        return [r[2] for r in self.rows if r[0] == uid]


def _run(monkeypatch, report: str, *, names: dict | None = None, filed: Filed | None = None):
    store = Store()
    reg = _rig(monkeypatch, store)
    filed = filed or Filed()
    monkeypatch.setattr(production.ag, "propose", filed)
    refs = dict(REFS)
    if names is not None:
        refs["participant_names"] = names
    out = reg.steps["drop_to_attendees"](
        _ctx(refs, dict(PRIOR, process_meeting={"report": report, "group": ""})))
    return out, filed, store


# ── the pure read, on its own ────────────────────────────────────────────────────────────────────

REPORT = ("## Decided\n- ship it on the 21st\n\n"
          "## Committed\n"
          "- [[Ben]] — the migration doc, by Friday\n"
          "- Cara: the pricing note\n"
          "- Anna — brief the board\n\n"
          "---\n\n"
          "- Ben should also read the contract\n")


def test_the_committed_section_ends_at_the_action_points():
    assert production.committed_bullets(REPORT) == [
        "[[Ben]] — the migration doc, by Friday", "Cara: the pricing note", "Anna — brief the board"]


def test_a_commitment_reaches_the_person_it_names_in_the_reports_own_words():
    assert production.commitments_for(REPORT, "ben@bank.test") == ["The migration doc, by Friday"]
    assert production.commitments_for(REPORT, "cara@bank.test") == ["The pricing note"]
    assert production.commitments_for(REPORT, "anna@bank.test") == ["Brief the board"]


def test_a_bullet_that_merely_mentions_somebody_is_not_their_job():
    """`Ben should also read the contract` is below the `---`, and even inside the section a bullet
    owned by Cara that names Ben is Cara's. The asymmetry is deliberate: a missed commitment costs
    a chip nobody notices, a wrong one tells you to do somebody else's work."""
    report = "## Committed\n- Cara — send Ben the migration doc\n"
    assert production.commitments_for(report, "cara@bank.test") == ["Send Ben the migration doc"]
    assert production.commitments_for(report, "ben@bank.test") == []


def test_the_display_name_from_the_invite_is_matched_too():
    report = "## Committed\n- Ben Smith — the migration doc\n"
    assert production.commitments_for(report, "b.smith@bank.test", "Ben Smith") \
        == ["The migration doc"]


def test_a_report_with_no_committed_section_proposes_nothing():
    assert production.commitments_for("## Decided\n- ship it\n", "ben@bank.test") == []


def test_one_meeting_can_never_fill_the_ten_slot_list():
    report = "## Committed\n" + "".join(f"- Ben — job {n}\n" for n in range(9))
    assert len(production.commitments_for(report, "ben@bank.test")) \
        == production.COMMITMENTS_PER_MEETING


# ── the step ─────────────────────────────────────────────────────────────────────────────────────

def test_the_drop_files_each_persons_commitment_onto_their_own_list(monkeypatch):
    out, filed, _ = _run(monkeypatch, REPORT,
                         names={"ben@bank.test": "Ben Smith", "cara@bank.test": "Cara Lopez"})
    assert isinstance(out, Done) and out.result["proposed"] == 3
    assert filed.acts_for("ben@bank.test") == ["The migration doc, by Friday"]
    assert filed.acts_for("cara@bank.test") == ["The pricing note"]
    assert filed.acts_for("anna@bank.test") == ["Brief the board"]


def test_the_item_names_the_meeting_it_came_from_and_who_saw_it(monkeypatch):
    _, filed, _ = _run(monkeypatch, REPORT)
    ben = [r for r in filed.rows if r[0] == "uid-ben"][0]
    assert ben[1] == "meeting:97"          # the SOURCE is the meeting — the store dedups on it
    assert ben[3] == "Pilot sync"          # ...and the chip can say where it came from
    assert ben[4] == "post-meeting"        # one writer per item


def test_a_report_that_commits_nobody_to_anything_files_nothing(monkeypatch):
    out, filed, _ = _run(monkeypatch, "## Decided\n- ship it on the 21st\n")
    assert isinstance(out, Done) and out.result["proposed"] == 0
    assert filed.rows == []


def test_a_filing_that_fails_never_costs_anybody_the_meetings_record(monkeypatch):
    """`ag.propose` swallows its own failures, and the step does not depend on that: even when the
    door raises, every desk still has the meeting and the drop is still a drop."""
    out, filed, store = _run(monkeypatch, REPORT, filed=Filed(fail=True))
    assert isinstance(out, Done)
    assert out.result["dropped"] == 3 and out.result["failed"] == []
    assert out.result["proposed"] == 0 and filed.rows == []
    assert store.of("ben@bank.test") is not None
