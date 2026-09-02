"""DELIVERY: the report goes out ONCE, to EVERYONE, and we know when it did not.

Seven findings of the 2026-09-02 line-vs-main review sit under one sentence, and each breaks a
different clause of it:

  ONCE      R-B01  the attendee fan-out can outlive its 90 s lease, which has no renewal; the
                   reclaiming worker restarts from an empty scratch, so a 20-person room is mailed
                   twice, with two share tokens each.
  EVERYONE  R-B20  the desk drop builds its room from `email_attendees`' `drops`, which is empty
                   whenever the MAIL was switched off or every attendee is cross-domain — a guard
                   on the mail door closing the desk door.
            R-B23  a per-attendee send failure loses that person the mail permanently: the address
                   enters neither `sent` nor `drops`, and nothing ever retries it.
            R-B07  a backslash in the meeting title raises inside `re.sub`'s REPLACEMENT, so the
                   whole fan-out for that room dies and retries into the same error forever.
  WE KNOW   R-B03  claimed `MAX_ATTEMPTS` is unreachable for a step mixing `Wait` and `StepError`.
                   IT IS NOT — the two tests under that heading are the evidence, and they PASS on
                   the code the review read. A `Wait` is +1 (claim) then -1 (the Wait branch), so
                   it is net zero; only an error-ending claim keeps its increment, and `attempt`
                   therefore climbs by exactly one per `StepError`. Observed at `_retry_or_fail`
                   for a step polling three times per dispatch: 1, 2, 3, 4, 5, 6 — backoff 5 s,
                   30 s, 120 s, 600 s, 600 s, 600 s — then `failed`. The tests stay as pins.
            R-B26  nothing ever wrote `effect_receipt.state = 'failed'`, so a permanently failed
                   `email_minutes` renders as `in_flight` and the timeline module's own promise —
                   "the one thing an agent must not do is talk about a report it never delivered"
                   — is not kept.
            R-B21  the note path is stamped at three moments with a wall-clock fallback, so the
                   mail's path and the desk's path differ by minutes and the Minutes tab reads
                   "No page here yet". This is F58 re-opening on a second route.

No network, no sleeps: the engine tests run on the sqlite rig, the step tests call the step
directly with the refs its flow would hand it.
"""
from __future__ import annotations

import json

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import flows_steps.meeting as mt_mod
import flows_steps.notify as notify_mod
import pytest
from flows import (Done, EventType, FakeClock, Reaction, Registry, SqliteDB, StepCtx, StepError,
                   Wait, admit, escalate, reclaim, tick)
from flows.loop import BACKOFF_S, LEASE_S, MAX_ATTEMPTS

from test_link_loop import FakeChannel, FakeScaffolds, _StubDB


# ── the rig ──────────────────────────────────────────────────────────────────────────────────
class _Flow:
    """The governing Flow, reduced to the one thing a step reads off it."""

    def __init__(self, **params):
        self._p = params

    def param(self, key, default=None):
        return self._p.get(key, default)


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None, flow=None,
         attempt: int = 1) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", attempt, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {}, clock_now=1_700_000_000.0,
                   scratch=scratch if scratch is not None else {}, flow=flow)


REFS = {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
        "start": 1_700_003_600.0,          # 2023-11-14 23:13:20 UTC
        "participants": ["anna@bank.test", "ben@bank.test", "cara@bank.test", "out@other.test"]}
REPORT = "## Decided\n- ship it\n"
PRIOR = {"process_meeting": {"report": REPORT, "group": "", "room_read": []}}


def _mail_rig(monkeypatch, *, fail_for=(), title=None):
    """`email_attendees` with agent-api, admin-api, the share mint and SMTP all replaced."""
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    sent_to: list[str] = []

    class _Ch:
        name = "fake"

        def send(self, to, subject, body, *, link=None, in_reply_to=None):
            sent_to.append(to)
            if to in fail_for:
                raise OSError("SMTP 421 — try again later")
            return ch.send(to, subject, body, link=link, in_reply_to=in_reply_to)

    notify_mod.use(_Ch())
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    monkeypatch.setattr(production, "ws_file",
                        lambda uid, path, slug=None: "# Acme Bank" if path == "README.md" else None)
    monkeypatch.setattr(mailtext, "ws_file", lambda uid, path, slug=None: None)
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: "" if key == "timezone" else True)
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, mid, native=None: {"id": 97})
    monkeypatch.setattr(production.mt, "mint_transcript_share",
                        lambda uid, m, email, expires_in_sec=30 * 86400: f"97.tok-{email}")
    return reg, ch, sent_to


def teardown_function():
    notify_mod.use(None)


# ══ ONCE ════════════════════════════════════════════════════════════════════════════════════
# R-B01 · the fan-out renews its own lease and persists what it has done, per person.
def test_the_engine_gives_a_step_a_checkpoint_that_renews_the_lease_and_saves_scratch():
    """The whole defect in one assertion: a step that runs LONGER THAN THE LEASE must be able to
    say so. Without this, `reclaim` hands the reaction to a second worker while the first is still
    mailing people, and the second starts from an EMPTY scratch — every attendee already mailed is
    mailed again, with a second share token."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()
    seen: list[tuple[float, dict]] = []

    @reg.step
    def slow(ctx: StepCtx):
        for i in range(3):
            clock._t += 60.0                       # 180 s total, against LEASE_S = 90
            ctx.scratch.setdefault("done", []).append(i)
            ctx.checkpoint()
            row = db.execute("SELECT lease_until, scratch FROM reaction")[0]
            seen.append((row[0], row[1]))
        return Done({"ok": True})

    reg.flow(name="slow_flow", version=1, on=EventType("t.slow"), steps=[slow])
    admit(db, reg, clock, source_event_id="s1", event_type="t.slow", subject_refs={})
    assert tick(db, reg, clock) is True

    # the lease was always in the future — the reclaimer never had a window
    assert [lease for lease, _ in seen] == pytest.approx(
        [1_000_060.0 + LEASE_S, 1_000_120.0 + LEASE_S, 1_000_180.0 + LEASE_S])
    # ...and each person's progress was DURABLE before the next one was attempted
    assert [json.loads(scratch) for _, scratch in seen] == [
        {"done": [0]}, {"done": [0, 1]}, {"done": [0, 1, 2]}]


def test_the_attendee_fan_out_checkpoints_once_per_person(monkeypatch):
    """Not "the mechanism exists" but "the fan-out uses it" — the review's finding is about this
    loop, whose per-person cost is a share mint plus a scaffold mint plus an SMTP round trip."""
    reg, ch, _ = _mail_rig(monkeypatch)
    ctx = _ctx(dict(REFS), PRIOR)
    beats: list[int] = []
    ctx.checkpoint = lambda: beats.append(1)

    out = reg.steps["email_attendees"](ctx)
    assert isinstance(out, Done) and out.result["sent"] == 2      # ben + cara, out@ is cross-domain
    assert len(beats) == 2


# ══ EVERYONE ════════════════════════════════════════════════════════════════════════════════
# R-B20 · the drop room is the INVITE ROSTER; `drops` supplies the link and nothing else.
class _Store:
    def __init__(self):
        self.files: dict[tuple[str, str], str] = {}
        self.writes: list[tuple[str, str]] = []

    def uid_of(self, email):
        return "uid-" + email.split("@")[0]

    def init(self, uid):
        return None

    def write(self, uid, path, content):
        self.writes.append((uid, path))
        self.files[(uid, path)] = content

    def read(self, uid, path, slug=None):
        return None if slug == "_global" else self.files.get((uid, path))

    def dropped_to(self) -> set[str]:
        return {uid for uid, path in self.writes if path.endswith(".md") and "index" not in path}


def _drop_rig(monkeypatch, store):
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production, "ensure_platform_user", store.uid_of)
    monkeypatch.setattr(production, "ws_file", store.read)
    monkeypatch.setattr(production.ag, "workspace_init", store.init)
    monkeypatch.setattr(production.ag, "workspace_write", store.write)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())
    return reg


def test_the_desk_drop_reaches_the_whole_room_even_when_the_mail_went_to_nobody(monkeypatch):
    """`email_attendees` returns `drops: []` when the follow-up is switched off or when every
    attendee is outside the organiser's domain. Decision 22's "the drop lands on it regardless"
    then held for the organiser alone, while `room_order` had already READ those same desks."""
    store = _Store()
    reg = _drop_rig(monkeypatch, store)
    prior = dict(PRIOR, email_attendees={"sent": 0, "followup": "off", "to": [], "drops": [],
                                         "skipped": "opted out"})
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))

    assert isinstance(out, Done)
    assert store.dropped_to() == {"uid-anna", "uid-ben", "uid-cara", "uid-out"}
    assert out.result["dropped"] == 4


def test_drops_supply_the_link_and_only_the_link(monkeypatch):
    """A person who WAS mailed carries their own share link into their copy; a person who was not
    still gets the artefact, with no link rather than somebody else's."""
    store = _Store()
    reg = _drop_rig(monkeypatch, store)
    prior = dict(PRIOR, email_attendees={
        "sent": 1, "meeting_id": 97, "to": ["ben@bank.test"],
        "drops": [{"to": "ben@bank.test", "link": "http://ui/?s=ben"}]})
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), prior))

    ben = store.files[("uid-ben", [p for _, p in store.writes if "index" not in p][0])]
    out = store.files[("uid-out", [p for _, p in store.writes if "index" not in p][0])]
    assert "Open the meeting: http://ui/?s=ben" in ben
    assert "Open the meeting:" not in out          # no link is not somebody else's link
    assert REPORT.strip() in out                   # ...but the artefact is all there


# R-B23 · a send failure is retried, and when the retries are spent it is said out loud.
def test_one_unreachable_address_is_retried_rather_than_silently_dropped(monkeypatch):
    """Today the address enters neither `sent` nor `drops` and the step returns `Done`: one
    transient SMTP 421 permanently removes a person from a meeting they attended."""
    reg, ch, sent_to = _mail_rig(monkeypatch, fail_for={"cara@bank.test"})
    scratch: dict = {}
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, scratch=scratch))

    assert e.value.retryable is True
    assert "cara@bank.test" in str(e.value)
    # the reachable attendee was mailed FIRST and is remembered, so the retry does not repeat it
    assert scratch["sent"] == ["ben@bank.test"]


def test_the_retry_does_not_re_mail_anybody_and_gives_up_loudly(monkeypatch):
    """The ceiling is the point: a permanently bad address must not hold the room's desk drop
    hostage forever. When the attempts are spent the step completes and names who was lost."""
    reg, ch, sent_to = _mail_rig(monkeypatch, fail_for={"cara@bank.test"})
    scratch = {"sent": ["ben@bank.test"], "drops": [{"to": "ben@bank.test", "link": "x"}]}
    out = reg.steps["email_attendees"](
        _ctx(dict(REFS), PRIOR, scratch=scratch, attempt=production.ATTENDEE_MAIL_ATTEMPTS))

    assert isinstance(out, Done)
    assert sent_to == ["cara@bank.test"]                       # ben was not mailed twice
    assert out.result["sent"] == 1
    assert any("cara@bank.test" in f for f in out.result["failed"])


# R-B07 · a backslash in the title must not kill the fan-out.
def test_a_backslash_in_a_value_does_not_raise_inside_the_replacement(monkeypatch):
    """`re.sub`'s REPLACEMENT is escape-processed, so a value containing `\\1`, `\\g` or a trailing
    backslash raises or corrupts — and `values` carries the ICS `SUMMARY`, which a person types."""
    monkeypatch.setattr(mailtext, "ws_file", lambda uid, path, slug=None: None)
    monkeypatch.setattr(mailtext, "company_name", lambda uid: "Acme Bank")
    monkeypatch.setattr(mailtext, "mailbox_address", lambda: "vexa@acme.test")
    monkeypatch.setitem(mailtext.DEFAULTS, "_backslash_probe",
                        "subject: {{meeting}}\n---\nabout {{meeting}}")
    title = r"Q3 planning \1 (backup C:\temp) \\"

    subject, body = mailtext.render("_backslash_probe", "7", {"meeting": title})
    assert subject == title
    assert body == f"about {title}"


def test_a_backslash_in_a_meeting_title_still_mails_the_whole_room(monkeypatch):
    """The step-level version of the same defect: `email_attendees` raised out of its `try` and the
    whole fan-out retried into the same error forever."""
    reg, ch, sent_to = _mail_rig(monkeypatch)
    refs = dict(REFS, title=r"Q3 planning \1 (backup C:\temp)")
    out = reg.steps["email_attendees"](_ctx(refs, PRIOR))

    assert isinstance(out, Done) and out.result["sent"] == 2
    assert refs["title"] in ch.sent[0]["subject"] or refs["title"] in ch.sent[0]["body"]


# ══ WE KNOW ═════════════════════════════════════════════════════════════════════════════════
# R-B03 · a step that polls AND fails still reaches the ceiling.
def test_a_step_that_waits_and_errors_by_turns_still_reaches_the_dead_letter():
    """R-B03, REFUSED — and this is the refusal, written as a pin rather than as a paragraph.

    The review read the two statements (`claim` does `attempt + 1`, the `Wait` branch does
    `attempt - 1`) and concluded `r.attempt` is "always 1 at `_retry_or_fail`". It is not: a poll
    is +1 then -1, which is NET ZERO, while an error-ending claim keeps its increment. `attempt`
    therefore climbs by exactly one per `StepError` no matter how many polls are interleaved, the
    backoff walks the whole `BACKOFF_S` ladder, and the sixth failure is terminal.

    `feedback_turn` is the step the finding names, and it cannot loop either for a second reason:
    `dispatched` and `t0` live in scratch, which survives a `StepError`, so the retries after the
    first "agent silent for 10min" re-dispatch nothing and raise immediately.

    Kept as a test because the property is worth pinning even though it already holds — the
    counter is shared between two branches, which is the shape that eventually does break."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()
    calls = {"n": 0}

    # THREE polls per dispatch, then the failure — the real shape. `feedback_turn` polls the agent
    # every 10 minutes and raises only when the collect itself fails, so with one decrement per
    # poll `attempt` was back at 1 by the time any error reached `_retry_or_fail`.
    @reg.step
    def poll_then_fail(ctx: StepCtx):
        calls["n"] += 1
        if calls["n"] % 4:
            return Wait(seconds=600)
        raise StepError("the agent is not answering")

    reg.flow(name="sick", version=1, on=EventType("t.sick"), steps=[poll_then_fail])
    admit(db, reg, clock, source_event_id="s1", event_type="t.sick", subject_refs={})

    for _ in range(600):
        reclaim(db, clock)
        if tick(db, reg, clock):
            continue
        nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                         "WHERE status IN ('admitted','retrying')")[0][0]
        if nxt is None:
            break
        clock._t = max(clock._t, nxt)
    else:
        raise AssertionError("the reaction never reached a terminal state — MAX_ATTEMPTS is "
                             "unreachable for a step that mixes Wait and StepError")

    status, reason = db.execute("SELECT status, reason FROM reaction")[0]
    assert status == "failed"
    assert "the agent is not answering" in reason
    # a Wait still costs nothing: exactly MAX_ATTEMPTS failures, not fewer
    assert calls["n"] == MAX_ATTEMPTS * 4          # three polls per attempt, six attempts


def test_a_wait_still_burns_no_attempt():
    """The other half of the same rule — the reason the decrement existed at all. A step that only
    polls must be able to poll indefinitely."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()
    calls = {"n": 0}

    @reg.step
    def only_polls(ctx: StepCtx):
        calls["n"] += 1
        if calls["n"] < 20:
            return Wait(seconds=10)
        return Done({"ok": True})

    reg.flow(name="patient", version=1, on=EventType("t.wait"), steps=[only_polls])
    admit(db, reg, clock, source_event_id="s1", event_type="t.wait", subject_refs={})
    for _ in range(60):
        if not tick(db, reg, clock):
            nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                             "WHERE status IN ('admitted','retrying')")[0][0]
            if nxt is None:
                break
            clock._t = max(clock._t, nxt)
    assert db.execute("SELECT status FROM reaction")[0][0] == "done"
    assert calls["n"] == 20


# R-B26 · the terminal branch writes the receipt it promised.
def test_a_permanently_failed_step_marks_its_own_receipt_failed():
    """`flows_timeline` turns `state = 'failed'` into a `reaction.failed` event and says the one
    thing an agent must not do is talk about a report it never delivered. Nothing wrote that state,
    so a permanently failed `email_minutes` rendered as `in_flight` forever."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def never_works(ctx: StepCtx):
        raise StepError("admin-api refused the mint", retryable=False)

    reg.flow(name="doomed", version=1, on=EventType("t.doom"), steps=[never_works])
    admit(db, reg, clock, source_event_id="s1", event_type="t.doom", subject_refs={})
    tick(db, reg, clock)

    assert db.execute("SELECT status FROM reaction")[0][0] == "failed"
    state, result = db.execute("SELECT state, result FROM effect_receipt")[0]
    assert state == "failed"
    assert "admin-api refused the mint" in (result or "")


def test_a_confirmed_receipt_is_never_re_marked_failed():
    """The guard on the fix: an effect that HAPPENED stays confirmed whatever the reaction does
    afterwards, or a retry of a later step would rewrite the mail's own history."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def works(ctx: StepCtx):
        return Done({"message_id": "<m@x>"})

    @reg.step
    def dies(ctx: StepCtx):
        raise StepError("nope", retryable=False)

    reg.flow(name="half", version=1, on=EventType("t.half"), steps=[works, dies])
    admit(db, reg, clock, source_event_id="s1", event_type="t.half", subject_refs={})
    for _ in range(4):
        tick(db, reg, clock)

    states = dict(db.execute("SELECT step, state FROM effect_receipt"))
    assert states == {"works": "confirmed", "dies": "failed"}


# R-B21 · the meeting's stamp is computed ONCE per reaction.
def test_the_note_path_is_stamped_once_even_when_the_row_lookup_recovers(monkeypatch):
    """`_meeting_stamp` falls back to the wall clock when there is no `refs.start` and the row
    lookup fails — the documented case for a `meeting.completed` admitted through `POST /events`.
    The path is then computed at THREE moments (the scaffold's mint, the drop, the frontmatter),
    and the mail's path and the desk's path differ: the Minutes tab resolves to a file nothing
    wrote and shows "No page here yet" (F58, on a second route)."""
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    answers = [None, 1_700_090_000.0]      # agent-api was down, then it came back

    def flaky_start(uid, mid, native=None):
        return answers.pop(0) if answers else 1_700_090_000.0

    monkeypatch.setattr(mt_mod, "meeting_start", flaky_start)
    ctx = _ctx({"uid": "7", "title": "Pilot sync", "meeting_id": 97}, scratch={})

    first = production._note_path(ctx, "7", "Pilot sync")
    second = production._note_path(ctx, "7", "Pilot sync")
    assert first == second, "the mail's path and the desk's path must be one string"
