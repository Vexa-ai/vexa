"""THE DROP: one meeting entity into each attendee's OWN workspace, written by plain code.

`drop_to_attendees` is the step that makes the follow-up mail land somewhere durable. It runs
after `email_attendees`, over the people that step ACTUALLY mailed, and for each of them it
ensures a platform user and a workspace, writes ONE pointer entity, and adds ONE line to their
meeting index. No agent turn, no LLM.

Five properties this file exists to hold:

  1. THE ENTITY IS A POINTER, NEVER A COPY. It carries the meeting's title, date and organiser and
     says where the canonical note lives — the organiser's workspace path plus the
     `?meeting=<row>` link with this person's own share token. One meeting, one note.
  2. EVERYBODY GETS THE SAME ENTITY (founder, 2026-09-02). No personal line. The only thing that
     differs between two attendees' files is the token inside their own link.
  3. THE INDEX IS CREATED, THEN APPENDED — once. A re-run adds no second line.
  4. IDEMPOTENT ACROSS RUNS, not merely within one. Scratch skips people already done inside a
     run, and every write is a content-compare, which is what survives a worker restart that
     loses scratch entirely.
  5. ONE FAILURE NEVER COSTS THE OTHERS THEIRS. The step only fails when EVERY drop failed.

No network: `ensure_platform_user`, `workspace_init`, `workspace_write` and `ws_file` are replaced
by a fake workspace store that records exactly what would have been written.
"""
from __future__ import annotations

import flows_defs.production as production
import pytest
from flows import Done, Reaction, Registry, StepCtx, StepError

from test_link_loop import _StubDB


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None, flow=None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {}, clock_now=1_700_000_000.0,
                   scratch=scratch if scratch is not None else {}, flow=flow)


REFS = {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
        "start": 1_700_003_600.0,
        "participants": ["anna@bank.test", "ben@bank.test", "cara@bank.test"]}
DAY = "2023-11-14"
ENTITY = f"kg/entities/meeting/{DAY}-pilot-sync.md"
INDEX = "kg/entities/meeting/index.md"
NOTE = "## Decided\n- ship it\n- and the whole note nobody may copy\n"

PRIOR = {
    "process_meeting": {"note_path": f"kg/entities/meeting/{DAY}-abc.md", "summary": "s",
                        "sha": "abc123"},
    "email_attendees": {"sent": 2, "meeting_id": 97, "to": ["ben@bank.test", "cara@bank.test"],
                        "drops": [
                            {"to": "ben@bank.test",
                             "link": "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-ben"},
                            {"to": "cara@bank.test",
                             "link": "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-cara"}]},
}


class Store:
    """Every attendee's workspace, as a dict, plus a log of every effect the step caused.

    `writes` is the ledger the idempotence tests read: a step that rewrites an identical file
    still produces a commit in somebody's history, so "did it write" is the question, not "what
    does the file say now"."""

    def __init__(self, fail_for=None):
        self.files: dict[tuple[str, str], str] = {}
        self.writes: list[tuple[str, str]] = []
        self.inits: list[str] = []
        self.users: list[str] = []
        self.fail_for = set(fail_for or ())

    def uid_of(self, email):
        self.users.append(email)
        if email in self.fail_for:
            raise RuntimeError(f"admin-api said no for {email}")
        return "uid-" + email.split("@")[0]

    def init(self, uid):
        self.inits.append(uid)

    def write(self, uid, path, content):
        self.writes.append((uid, path))
        self.files[(uid, path)] = content

    def read(self, uid, path, slug=None):
        if slug == "_global":
            return None
        if (uid, path) in self.files:
            return self.files[(uid, path)]
        return NOTE if path.startswith("kg/entities/meeting/") and path.endswith("-abc.md") else None

    def of(self, email, path=ENTITY):
        return self.files.get(("uid-" + email.split("@")[0], path))


def _rig(monkeypatch, store):
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production, "ensure_platform_user", store.uid_of)
    monkeypatch.setattr(production, "ws_file", store.read)
    monkeypatch.setattr(production.ag, "workspace_init", store.init)
    monkeypatch.setattr(production.ag, "workspace_write", store.write)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")   # no timezone -> UTC
    return reg


# ── 1 · the entity's content ─────────────────────────────────────────────────────────────────
def test_the_entity_carries_title_date_organiser_and_a_pointer(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert isinstance(out, Done) and out.result["dropped"] == 2
    doc = store.of("ben@bank.test")
    assert doc is not None, f"nothing written; wrote {store.writes}"
    # a real entity, in the shape kg/templates/meeting.md defines
    assert doc.startswith("---\ntype: meeting\n")
    assert f"id: {DAY}-pilot-sync" in doc
    assert 'title: "Pilot sync"' in doc
    assert f"date: {DAY}" in doc
    assert 'organizer: "anna@bank.test"' in doc
    assert "# Pilot sync" in doc
    assert "14 November 2023 — anna@bank.test had Vexa in the room." in doc
    # the POINTER: the organiser's path AND this person's own link
    assert f"It lives in anna@bank.test's workspace at `kg/entities/meeting/{DAY}-abc.md`." in doc
    assert "Open the meeting: http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-ben" in doc


def test_the_full_note_is_never_copied_into_anybodys_workspace(monkeypatch):
    """One meeting has one note. A copy in every attendee's workspace is five versions of one
    truth the moment the organiser corrects theirs — and the pointer is what makes that
    unnecessary."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    for email in ("ben@bank.test", "cara@bank.test"):
        doc = store.of(email)
        assert "and the whole note nobody may copy" not in doc
        assert "## Decided" not in doc
    # and nothing was READ out of anybody's workspace except the two files the step authors
    assert {p for _uid, p in store.writes} == {ENTITY, INDEX}


def test_everybody_gets_the_same_entity_apart_from_their_own_token(monkeypatch):
    """No personal line: the two files differ in exactly one place, and it is the share token,
    which must differ because a forwarded link may grant its new reader nothing."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    ben = store.of("ben@bank.test").replace("t-ben", "TOKEN")
    cara = store.of("cara@bank.test").replace("t-cara", "TOKEN")
    assert ben.replace("ben@bank.test", "X") == cara.replace("cara@bank.test", "X")


@pytest.mark.parametrize("title,expect", [
    ("../../etc/passwd", "kg/entities/meeting/2023-11-14-etc-passwd.md"),
    ("A/B test: round 2!", "kg/entities/meeting/2023-11-14-a-b-test-round-2.md"),
    ("   ", "kg/entities/meeting/2023-11-14-meeting.md"),
    ("x" * 200, "kg/entities/meeting/2023-11-14-" + "x" * 60 + ".md"),
])
def test_the_title_is_slugified_safely(monkeypatch, title, expect):
    """A title comes off a calendar invite anybody in the room can edit, so the character class is
    an allow-list: no `/`, no `..`, bounded length, never empty."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS, title=title), PRIOR))
    assert expect in {p for _uid, p in store.writes}


def test_a_title_with_yaml_in_it_cannot_break_the_frontmatter(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS, title='Q3: "roadmap" [draft]'), PRIOR))
    doc = store.of("ben@bank.test", "kg/entities/meeting/2023-11-14-q3-roadmap-draft.md")
    assert 'title: "Q3: \\"roadmap\\" [draft]"' in doc


# ── 2 · the person and their workspace are ensured, and nothing else is built ────────────────
def test_each_attendee_gets_a_platform_user_and_a_workspace_and_no_more(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert store.users == ["ben@bank.test", "cara@bank.test"]
    assert store.inits == ["uid-ben", "uid-cara"]        # POST /api/workspace/init as THEM
    # no chat, no session, no scaffolding: two files each and nothing else
    assert sorted(store.writes) == [
        ("uid-ben", ENTITY), ("uid-ben", INDEX),
        ("uid-cara", ENTITY), ("uid-cara", INDEX)]


def test_nobody_is_dropped_on_who_was_not_mailed(monkeypatch):
    """The step's input is who the mail ACTUALLY reached, not who was in the room."""
    store = Store()
    reg = _rig(monkeypatch, store)
    prior = dict(PRIOR, email_attendees=dict(PRIOR["email_attendees"],
                                             drops=[PRIOR["email_attendees"]["drops"][0]]))
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))
    assert out.result["to"] == ["ben@bank.test"]
    assert store.of("cara@bank.test") is None


def test_a_fan_out_that_mailed_nobody_drops_nobody(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    prior = dict(PRIOR, email_attendees={"sent": 0, "drops": []})
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))
    assert out.result["dropped"] == 0 and store.writes == []
    assert "nothing to drop" in out.result["skipped"]


# ── 3 · the index ────────────────────────────────────────────────────────────────────────────
def test_the_index_is_created_when_it_is_absent(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert idx.startswith("# meeting\n")
    assert f"- [Pilot sync]({DAY}-pilot-sync.md) — {DAY}\n" in idx


def test_the_index_is_appended_to_when_it_exists(monkeypatch):
    """...and the seed's own "no entities yet" placeholder is replaced, not left standing above
    the first real row: it is the index saying it is empty, and it stops being true here."""
    store = Store()
    store.files[("uid-ben", INDEX)] = (
        "# meeting\n\nMeetings — one file per meeting at `meeting/<yyyy-mm-dd-slug>.md`.\n\n"
        "_No entities yet — meetings file themselves here as they happen._\n")
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert "_No entities yet" not in idx
    assert idx.endswith(f"- [Pilot sync]({DAY}-pilot-sync.md) — {DAY}\n")
    assert "Meetings — one file per meeting" in idx        # the human preamble survives


def test_an_existing_index_row_is_kept_and_the_new_one_added_below(monkeypatch):
    store = Store()
    store.files[("uid-ben", INDEX)] = "# meeting\n\n- [Earlier](2023-11-01-earlier.md) — 2023-11-01\n"
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert "- [Earlier](2023-11-01-earlier.md) — 2023-11-01" in idx
    assert idx.endswith(f"- [Pilot sync]({DAY}-pilot-sync.md) — {DAY}\n")


# ── 4 · idempotence ──────────────────────────────────────────────────────────────────────────
def test_a_second_run_writes_nothing_even_with_a_fresh_scratch(monkeypatch):
    """THE ACROSS-RUN HALF. A worker restart loses scratch, so the content-compare is what stops a
    second entity, a second index line and a second commit."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    first = list(store.writes)
    store.writes.clear()

    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch={}))   # scratch gone
    assert store.writes == [], f"a re-run wrote again: {store.writes}"
    assert out.result["dropped"] == 2
    assert len(first) == 4
    # one entity, one index line — not two
    assert store.of("ben@bank.test", INDEX).count("pilot-sync.md") == 1


def test_scratch_skips_people_already_done_inside_one_run(monkeypatch):
    """THE WITHIN-RUN HALF. A `StepError` upstream re-runs the whole step; ben is not re-touched,
    so he is not even looked up in the admin API again."""
    store = Store()
    reg = _rig(monkeypatch, store)
    scratch = {"dropped": ["ben@bank.test"]}
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))

    assert store.users == ["cara@bank.test"]
    assert store.of("ben@bank.test") is None
    assert sorted(scratch["dropped"]) == ["ben@bank.test", "cara@bank.test"]


# ── 5 · failure policy ───────────────────────────────────────────────────────────────────────
def test_one_attendees_failure_does_not_cost_the_others_theirs(monkeypatch):
    store = Store(fail_for={"ben@bank.test"})
    reg = _rig(monkeypatch, store)
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert out.result["dropped"] == 1 and out.result["to"] == ["cara@bank.test"]
    assert store.of("cara@bank.test") is not None
    assert len(out.result["failed"]) == 1
    assert out.result["failed"][0].startswith("ben@bank.test: RuntimeError")


def test_the_step_fails_loudly_only_when_every_drop_failed(monkeypatch):
    store = Store(fail_for={"ben@bank.test", "cara@bank.test"})
    reg = _rig(monkeypatch, store)
    with pytest.raises(StepError) as e:
        reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert "every attendee drop failed" in str(e.value)
    assert "ben@bank.test" in str(e.value) and "cara@bank.test" in str(e.value)
    assert e.value.retryable is True          # every write above is safe to repeat


def test_a_retry_after_a_partial_failure_finishes_the_rest(monkeypatch):
    """The partial state is a fact an operator can act on, and the retry is cheap: the person who
    succeeded is skipped by scratch, the one who failed is attempted again."""
    store = Store(fail_for={"ben@bank.test"})
    reg = _rig(monkeypatch, store)
    scratch: dict = {}
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))
    store.fail_for.clear()                    # whatever was wrong is fixed
    store.writes.clear()

    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))
    assert out.result["dropped"] == 2 and out.result["failed"] == []
    assert sorted(store.writes) == [("uid-ben", ENTITY), ("uid-ben", INDEX)]   # only ben's


# ── the step is in the flow, after the mail ──────────────────────────────────────────────────
def test_drop_to_attendees_runs_after_email_attendees_in_post_meeting():
    reg = Registry()
    production.build(reg, _StubDB())
    steps = list(reg.flows[("post_meeting", 1)].steps)
    assert steps == ["require_workspace", "process_meeting", "email_minutes",
                     "email_attendees", "drop_to_attendees"]
