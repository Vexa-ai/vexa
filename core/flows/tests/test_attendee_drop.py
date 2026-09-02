"""THE DROP: the meeting's artefact onto every desk in the room, written by plain code.

Founder decision 22, 2026-09-02. `process_meeting` writes into NO desk — not the organiser's
either — and the note's canonical home is the meeting row and its transcript store. So this step
is where a meeting lands on a person: it copies the one artefact, byte-for-byte, to everybody who
was in the room, organiser included, nobody special. No agent turn, no LLM.

Six properties this file exists to hold:

  1. THE ENTITY IS THE ARTEFACT, not a pointer to somebody else's copy of it. There is no longer a
     copy elsewhere to point at.
  2. THE SAME BYTES FOR EVERYONE except the last line — the `?meeting=` link, which carries that
     person's own share token because a forwarded link must grant its new reader nothing.
  3. THE ORGANISER IS IN THE ROOM. Their copy is the same entity, with the token-free link
     `email_minutes` already built.
  4. THE INDEX is created, then appended — once. A re-run adds no second line.
  5. IDEMPOTENT ACROSS RUNS, not merely within one.
  6. ONE FAILURE NEVER COSTS THE OTHERS THEIRS. The step fails only when EVERY drop failed.

No network: `ensure_platform_user`, `workspace_init`, `workspace_write` and `ws_file` are replaced
by a fake desk store that records exactly what would have been written.
"""
from __future__ import annotations

import flows_defs.production as production
import pytest
from flows import Done, Reaction, Registry, StepCtx, StepError

from test_link_loop import FakeScaffolds, _StubDB


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    """Every production touch mints a scaffold before it sends; this stands in for agent-api. The
    organiser's own drop composes one too when their minutes mail was off — a preference about MAIL
    is not a preference about the link on their own desk."""
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None, flow=None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {}, clock_now=1_700_000_000.0,
                   scratch=scratch if scratch is not None else {}, flow=flow)


REFS = {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
        "start": 1_700_003_600.0,
        "participants": ["anna@bank.test", "ben@bank.test", "cara@bank.test"]}
DAY = "2023-11-14"
# THE FILENAME CARRIES THE MEETING'S TIME, not only its day (F58). `_meeting_stamp` renders
# `%Y-%m-%d-%H%M` precisely "so two occurrences on ONE day are still two files", and the drop was
# slicing that back to `%Y-%m-%d` before building the name — which silently restored the collision
# the stamp exists to prevent: a recurring meeting keeps one title across occurrences, so the
# afternoon's record overwrote the morning's on every desk. `DAY` still appears INSIDE the file
# (frontmatter `date:`, the index line's trailing date), where a person reads it and no two files
# have to differ. 23:13 UTC is REFS["start"] = 1_700_003_600.
STAMP = f"{DAY}-2313"
ENTITY = f"kg/entities/meeting/{STAMP}-pilot-sync.md"
INDEX = "kg/entities/meeting/index.md"
REPORT = "## Decided\n- ship it on the 21st\n\n## Committed\n- Ben — the migration doc"

PRIOR = {
    "process_meeting": {"report": REPORT, "group": "", "room_read": []},
    "email_minutes": {"message_id": "<m@x>", "link": "http://ui/?ask=minutes-review&meeting=97"},
    "email_attendees": {"sent": 2, "meeting_id": 97, "to": ["ben@bank.test", "cara@bank.test"],
                        "drops": [
                            {"to": "ben@bank.test",
                             "link": "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-ben"},
                            {"to": "cara@bank.test",
                             "link": "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-cara"}]},
}
EVERYONE = ["anna@bank.test", "ben@bank.test", "cara@bank.test"]


class Store:
    """Every person's desk, as a dict, plus a log of every effect the step caused.

    `writes` is the ledger the idempotence tests read: a step that rewrites an identical file still
    produces a commit in somebody's history, so "did it write" is the question, not "what does the
    file say now"."""

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
        # The step may read EXACTLY the two paths it authors, and only so a re-run writes nothing.
        # Anything else is the defect this fake exists to catch.
        if slug == "_global":
            return None
        if not (path == INDEX or path.startswith("kg/entities/meeting/")):
            raise AssertionError(f"the drop read a desk file it does not author: {path!r}")
        return self.files.get((uid, path))

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


# ── 1 · the entity IS the artefact ───────────────────────────────────────────────────────────
def test_the_entity_carries_the_report_itself_not_a_pointer_to_it(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert isinstance(out, Done) and out.result["dropped"] == 3
    doc = store.of("ben@bank.test")
    assert doc is not None, f"nothing written; wrote {store.writes}"
    # a real entity, in the shape kg/templates/meeting.md defines
    assert doc.startswith("---\ntype: meeting\n")
    assert f"id: {STAMP}-pilot-sync" in doc
    assert 'title: "Pilot sync"' in doc
    assert f"date: {DAY}" in doc
    assert 'organizer: "anna@bank.test"' in doc
    assert '"anna@bank.test", "ben@bank.test", "cara@bank.test"' in doc   # the meeting's roster
    assert "# Pilot sync" in doc
    assert "14 November 2023 — anna@bank.test had Vexa in the room." in doc
    # THE REPORT, in full, in their own desk
    assert REPORT in doc
    # ...and no sentence sending them somewhere else for it
    assert "pointer" not in doc
    assert "It lives in" not in doc
    assert "Open the meeting: http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-ben" in doc


def test_the_report_is_rendered_readably_not_as_a_raw_workspace_note(monkeypatch):
    """`_readable` is what strips frontmatter and flattens wikilinks. A drop that skipped it would
    put `type: meeting / id: ...` in the middle of the entity's own body."""
    store = Store()
    reg = _rig(monkeypatch, store)
    raw = "---\ntype: meeting\nid: x\n---\n\n## Decided\n- [[Ben]] ships it\n"
    reg.steps["drop_to_attendees"](
        _ctx(dict(REFS), dict(PRIOR, process_meeting={"report": raw, "group": ""})))

    doc = store.of("ben@bank.test")
    assert doc.count("type: meeting") == 1          # the entity's own frontmatter, not the note's
    assert "[[Ben]]" not in doc and "Ben ships it" in doc


# ── 2 · the same bytes for everyone, except their own link ──────────────────────────────────
def test_every_desk_gets_byte_identical_content_apart_from_the_link(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    docs = [store.of(a) for a in EVERYONE]
    assert all(d is not None for d in docs)
    heads = {d.rsplit("Open the meeting:", 1)[0] for d in docs}
    assert len(heads) == 1, "the entity differs between people above the link"
    tails = {d.rsplit("Open the meeting:", 1)[1].strip() for d in docs}
    assert tails == {"http://ui/?ask=minutes-review&meeting=97",
                     "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-ben",
                     "http://ui/?ask=minutes-review-invite&meeting=97&tshare=t-cara"}


def test_no_copy_is_personalised_to_its_owner(monkeypatch):
    """The frontmatter says who was in the MEETING, not who this copy is for. So each address
    appears the SAME number of times in everybody's copy — the roster mentions ben exactly as often
    in anna's entity as in his own, which is what makes the bytes equal in the first place."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    for named in EVERYONE:
        counts = {store.of(owner).rsplit("Open the meeting:", 1)[0].count(named)
                  for owner in EVERYONE}
        assert len(counts) == 1, f"{named} appears a different number of times in different copies"


# ── 3 · the organiser is in the room ─────────────────────────────────────────────────────────
def test_the_organiser_gets_the_same_entity_with_their_own_token_free_link(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert out.result["to"][0] == "anna@bank.test"      # first, because the room starts with them
    doc = store.of("anna@bank.test")
    assert REPORT in doc
    assert "tshare" not in doc                          # the meeting is theirs; no capability
    assert "Open the meeting: http://ui/?ask=minutes-review&meeting=97" in doc


def test_the_organiser_is_dropped_on_even_when_their_minutes_mail_was_off(monkeypatch, scaffolds):
    """`mail_minutes` is a preference about MAIL. It is not a preference about what lands on their
    own desk, and reading it as one would silently deny the organiser their own meeting.

    With no `email_minutes` link to reuse, this step MINTS the organiser's scaffold itself — so the
    link on their own desk is a record like everybody else's, not a hand-built deeplink."""
    store = Store()
    reg = _rig(monkeypatch, store)
    prior = dict(PRIOR, email_minutes={"skipped": "mail_minutes is off for this person"})
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))

    assert "anna@bank.test" in out.result["to"]
    rec = scaffolds.for_("anna@bank.test")
    assert (rec["kind"], rec["opening"], rec["meeting"]) == ("post-meeting", "minutes-review", "97")
    assert rec["share_token"] is None                   # the meeting is theirs; no capability
    assert f"Open the meeting: https://app.example.test/?s=sc{len(scaffolds.minted)}" in \
        store.of("anna@bank.test")


def test_the_organiser_is_dropped_on_even_when_the_fan_out_mailed_nobody(monkeypatch):
    """A meeting with no inside-domain attendee still happened to its organiser."""
    store = Store()
    reg = _rig(monkeypatch, store)
    prior = dict(PRIOR, email_attendees={"sent": 0, "drops": [], "skipped": "opted out"})
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))

    assert out.result["to"] == ["anna@bank.test"]
    assert store.of("ben@bank.test") is None


def test_no_report_means_no_drop_at_all(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    prior = dict(PRIOR, process_meeting={"report": "", "group": ""})
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))
    assert out.result["dropped"] == 0 and store.writes == []
    assert "no report" in out.result["skipped"]


# ── the person and their desk are ensured, and nothing else is built ────────────────────────
def test_each_person_gets_a_platform_user_and_a_desk_and_no_more(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert store.users == EVERYONE
    assert store.inits == ["uid-anna", "uid-ben", "uid-cara"]   # POST /api/workspace/init as THEM
    # no chat, no session, no scaffolding: two files each and nothing else
    assert sorted(store.writes) == sorted(
        [(f"uid-{n}", p) for n in ("anna", "ben", "cara") for p in (ENTITY, INDEX)])


@pytest.mark.parametrize("title,expect", [
    ("../../etc/passwd", "kg/entities/meeting/2023-11-14-2313-etc-passwd.md"),
    ("A/B test: round 2!", "kg/entities/meeting/2023-11-14-2313-a-b-test-round-2.md"),
    ("   ", "kg/entities/meeting/2023-11-14-2313-meeting.md"),
    ("x" * 200, "kg/entities/meeting/2023-11-14-2313-" + "x" * 60 + ".md"),
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
    doc = store.of("ben@bank.test", "kg/entities/meeting/2023-11-14-2313-q3-roadmap-draft.md")
    assert 'title: "Q3: \\"roadmap\\" [draft]"' in doc


# ── 4 · the index ────────────────────────────────────────────────────────────────────────────
def test_the_index_is_created_when_it_is_absent(monkeypatch):
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert idx.startswith("# meeting\n")
    assert f"- [Pilot sync]({STAMP}-pilot-sync.md) — {DAY}\n" in idx


def test_the_index_is_appended_to_when_it_exists(monkeypatch):
    """...and the seed's own "no entities yet" placeholder is replaced, not left standing above the
    first real row: it is the index saying it is empty, and it stops being true here."""
    store = Store()
    store.files[("uid-ben", INDEX)] = (
        "# meeting\n\nMeetings — one file per meeting at `meeting/<yyyy-mm-dd-slug>.md`.\n\n"
        "_No entities yet — meetings file themselves here as they happen._\n")
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert "_No entities yet" not in idx
    assert idx.endswith(f"- [Pilot sync]({STAMP}-pilot-sync.md) — {DAY}\n")
    assert "Meetings — one file per meeting" in idx        # the human preamble survives


def test_an_existing_index_row_is_kept_and_the_new_one_added_below(monkeypatch):
    store = Store()
    store.files[("uid-ben", INDEX)] = "# meeting\n\n- [Earlier](2023-11-01-earlier.md) — 2023-11-01\n"
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    idx = store.of("ben@bank.test", INDEX)
    assert "- [Earlier](2023-11-01-earlier.md) — 2023-11-01" in idx
    assert idx.endswith(f"- [Pilot sync]({STAMP}-pilot-sync.md) — {DAY}\n")


# ── 5 · idempotence ──────────────────────────────────────────────────────────────────────────
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
    assert out.result["dropped"] == 3
    assert len(first) == 6
    assert store.of("ben@bank.test", INDEX).count("pilot-sync.md") == 1


def test_scratch_skips_people_already_done_inside_one_run(monkeypatch):
    """THE WITHIN-RUN HALF. A `StepError` upstream re-runs the whole step; ben is not re-touched,
    so he is not even looked up in the admin API again."""
    store = Store()
    reg = _rig(monkeypatch, store)
    scratch = {"dropped": ["anna@bank.test", "ben@bank.test"]}
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))

    assert store.users == ["cara@bank.test"]
    assert store.of("ben@bank.test") is None
    assert sorted(scratch["dropped"]) == EVERYONE


# ── 6 · failure policy ───────────────────────────────────────────────────────────────────────
def test_one_persons_failure_does_not_cost_the_others_theirs(monkeypatch):
    store = Store(fail_for={"ben@bank.test"})
    reg = _rig(monkeypatch, store)
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    assert out.result["dropped"] == 2
    assert out.result["to"] == ["anna@bank.test", "cara@bank.test"]
    assert store.of("cara@bank.test") is not None
    assert len(out.result["failed"]) == 1
    assert out.result["failed"][0].startswith("ben@bank.test: RuntimeError")


def test_the_step_fails_loudly_only_when_every_drop_failed(monkeypatch):
    store = Store(fail_for=set(EVERYONE))
    reg = _rig(monkeypatch, store)
    with pytest.raises(StepError) as e:
        reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert "every desk drop failed" in str(e.value)
    for a in EVERYONE:
        assert a in str(e.value)
    assert e.value.retryable is True          # every write above is safe to repeat


def test_a_retry_after_a_partial_failure_finishes_the_rest(monkeypatch):
    """The partial state is a fact an operator can act on, and the retry is cheap: the people who
    succeeded are skipped by scratch, the one who failed is attempted again."""
    store = Store(fail_for={"ben@bank.test"})
    reg = _rig(monkeypatch, store)
    scratch: dict = {}
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))
    store.fail_for.clear()                    # whatever was wrong is fixed
    store.writes.clear()

    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))
    assert out.result["dropped"] == 3 and out.result["failed"] == []
    assert sorted(store.writes) == [("uid-ben", ENTITY), ("uid-ben", INDEX)]   # only ben's


# ── the step is in the flow, after the mail ──────────────────────────────────────────────────
def test_drop_to_attendees_runs_after_email_attendees_in_post_meeting():
    reg = Registry()
    production.build(reg, _StubDB())
    steps = list(reg.flows[("post_meeting", 1)].steps)
    assert steps == ["require_workspace", "process_meeting", "email_minutes",
                     "email_attendees", "drop_to_attendees"]


# ── the economics bound: the drop is ENTITY-FREE ─────────────────────────────────────────────
def test_the_drop_writes_the_report_and_its_index_line_AND_NOTHING_ELSE(monkeypatch):
    """Founder economics rule (PRD decision 22 addendum): *a desk nobody talks to is a flat pile of
    reports — complete, and free.*

    Wiring that pile into people, companies and decisions costs a model call per person per
    meeting, and for somebody who never opens the product it buys nothing. So the drop writes TWO
    paths and stops. The wiring happens later, in the person's own chat, when they engage, and it
    is PROPOSED by the agent rather than run on their behalf.

    This test is the boundary. It fails if anybody ever adds a person entity, a company entity, a
    decision entity, or a README rewrite to this step."""
    store = Store()
    reg = _rig(monkeypatch, store)
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))

    written = {p for _uid, p in store.writes}
    assert written == {ENTITY, INDEX}, f"the drop wrote something else: {written - {ENTITY, INDEX}}"
    for path in written:
        assert path.startswith("kg/entities/meeting/")
    assert not any("README" in p for p in written)
    assert not any("/person/" in p or "/company/" in p or "/decision/" in p for p in written)


def test_the_drop_never_runs_an_agent_turn(monkeypatch):
    """Plain code, no LLM. A dispatch here would be a model call per person per meeting — the exact
    cost the rule above exists to refuse — and it would also make the step non-deterministic on a
    retry, which is what the idempotence tests depend on."""
    store = Store()
    reg = _rig(monkeypatch, store)

    def no_turns(*a, **k):
        raise AssertionError("drop_to_attendees dispatched an agent turn")
    monkeypatch.setattr(production.ag, "dispatch_turn", no_turns)
    monkeypatch.setattr(production.ag, "collect_reply", no_turns)
    monkeypatch.setattr(production.ag, "collect_outbox", no_turns)

    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert out.result["dropped"] == 3
