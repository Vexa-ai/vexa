"""The attendee mail's SHAPE: a template HEAD the founder edits + the agent's one-run section.

Four elements, in order, and nothing else: head → the person's section → one gap line → one
button. The gap line and the button belong to `notify.compose`, so every assertion here reads the
`link` off the recorded call rather than hunting for a url in prose.

Three properties this file exists to hold:

  1. THE HEAD IS A FILE, READ AT SEND TIME. The founder edits `deploy/dogfood/mail/attendee-head.md`
     between runs; a template read at import would need a restart to land, so this reads per send.
  2. A FALLBACK IS LOUD. When that file is missing the inline default still ships — but it is
     logged with the exact path, and the step's result carries the same string. A founder editing
     a file nobody reads must be able to see that without reading a log.
  3. SILENCE IS KNOWABLE. `process_meeting` no longer asks the agent to substitute the decision
     for people the meeting held nothing for, so a MISSING `## <address>` section now means
     exactly one thing, and `attendee_silent_policy` / `attendee_personal_max` decide what those
     people receive.

No network, no clock, no DB: the step is called directly with the refs its flow would hand it.
"""
from __future__ import annotations

import logging

import flows_defs.production as production
import flows_steps.notify as notify_mod
import pytest
from flows import Done, Reaction, Registry, StepCtx

from test_link_loop import FakeChannel, _StubDB, _params


class _Flow:
    """The governing Flow, reduced to the one thing the steps read off it."""

    def __init__(self, **params):
        self._p = params

    def param(self, key, default=None):
        return self._p.get(key, default)


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None, flow=None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {}, clock_now=1_700_000_000.0,
                   scratch=scratch if scratch is not None else {}, flow=flow)


# 1700003600 = 2023-11-14 23:13:20 UTC. `start` in refs + no timezone setting keeps `_meeting_stamp`
# — and therefore {{date}} — off the network and off the server's clock.
REFS = {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
        "start": 1_700_003_600.0,
        "participants": ["anna@bank.test", "ben@bank.test", "cara@bank.test", "out@other.test"]}
PRIOR = {"process_meeting": {"note_path": "kg/x.md", "summary": "s", "sha": "abc123"}}
NOTE = "## Decided\n- ship it\n"
THE_DATE = "14 November 2023"


def _ws(outbox=None, readme="# Acme Bank\n\nthe org handbook"):
    """A workspace reader that knows the difference between the ORG's `_global` README, the
    meeting note, and the per-attendee outbox — the three files this step reads."""
    def read(uid, path, slug=None):
        if slug == "_global":
            return readme
        if path.startswith("mail_outbox/"):
            return outbox
        return NOTE
    return read


def _rig(monkeypatch, *, outbox=None, readme="# Acme Bank\n\nthe org handbook", mail_dir=None):
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    monkeypatch.setattr(production, "ws_file", _ws(outbox, readme))
    monkeypatch.setattr(production, "setting", lambda uid, key: "")      # no timezone → UTC
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, mid, native=None: {"id": 97})
    monkeypatch.setattr(production.mt, "mint_transcript_share",
                        lambda uid, m, email, expires_in_sec=30 * 86400: f"97.tok-{email}")
    if mail_dir is not None:
        monkeypatch.setattr(production, "_mail_dir", lambda: mail_dir)
    return reg, ch


def teardown_function():
    notify_mod.use(None)


# ── 1 · the head is a file, read at send time ────────────────────────────────────────────────
HEAD_FILE = ("I'm Vexa, the meeting assistant at {{company}}. I sit in meetings you're invited "
             "to; afterwards you get what came out of them and what they leave on your plate. "
             "{{organizer}} had me in {{meeting}} on {{date}}.")


def test_the_head_is_read_from_the_file_and_all_four_tokens_are_substituted(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text(HEAD_FILE + "\n")
    reg, ch = _rig(monkeypatch, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    assert isinstance(out, Done) and out.result["sent"] == 2
    assert out.result["head"] == str(tmp_path / "attendee-head.md")
    body = ch.sent[0]["body"]
    assert body.startswith("I'm Vexa, the meeting assistant at Acme Bank.")   # {{company}}
    assert "anna@bank.test had me in Pilot sync on " + THE_DATE in body       # the other three
    assert "{{" not in body                                                   # nothing left unfilled


def test_the_file_is_re_read_on_every_send_so_an_edit_needs_no_restart(monkeypatch, tmp_path):
    f = tmp_path / "attendee-head.md"
    f.write_text("First wording for {{company}}.")
    reg, ch = _rig(monkeypatch, mail_dir=tmp_path)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith("First wording for Acme Bank.")

    f.write_text("Second wording for {{company}}.")          # the founder edits between runs
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))    # no reload, no restart
    assert ch.sent[-1]["body"].startswith("Second wording for Acme Bank.")


def test_a_missing_head_file_falls_back_to_the_inline_default_AND_SAYS_SO(monkeypatch, tmp_path,
                                                                          caplog):
    """The documented fallback. A SILENT fallback is the defect this asserts against: the founder
    has to be able to tell that the file he is editing is not the one being read."""
    reg, ch = _rig(monkeypatch, mail_dir=tmp_path)               # tmp_path holds no template
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    body = ch.sent[0]["body"]
    assert body.startswith("I'm Vexa, the meeting assistant at Acme Bank.")   # it still ships
    # ...and the path it looked for is named, in the log AND in the receipt
    looked_for = str(tmp_path / "attendee-head.md")
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert looked_for in warning
    assert "INLINE DEFAULT" in warning
    assert out.result["head"] == f"inline default (no readable file at {looked_for})"


def test_an_empty_head_file_is_a_fallback_too(monkeypatch, tmp_path, caplog):
    """A file the founder emptied by accident is not a mail with no introduction."""
    (tmp_path / "attendee-head.md").write_text("   \n\n")
    reg, ch = _rig(monkeypatch, mail_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert out.result["head"].startswith("inline default")
    assert ch.sent[0]["body"].startswith("I'm Vexa, the meeting assistant at Acme Bank.")


def test_a_flow_param_still_outranks_the_file(monkeypatch, tmp_path):
    """The `prompts` override idiom `prompt_for` already uses — a deployment can replace the head
    without a file at all."""
    (tmp_path / "attendee-head.md").write_text(HEAD_FILE)
    reg, ch = _rig(monkeypatch, mail_dir=tmp_path)
    flow = _Flow(prompts={"attendee-head.md": "Param wording for {{company}}."})
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=flow))
    assert ch.sent[0]["body"].startswith("Param wording for Acme Bank.")
    assert out.result["head"] == "flow param prompts[attendee-head.md]"


def test_the_same_reader_serves_minutes_head_md(monkeypatch, tmp_path):
    """`email_minutes` is deliberately NOT wired here — but the reader it would use is this one,
    unchanged, and this pins that it resolves against the same founder-edited directory."""
    (tmp_path / "minutes-head.md").write_text("Minutes head for {{company}}.")
    monkeypatch.setattr(production, "_mail_dir", lambda: tmp_path)
    text, source = production.mail_template(None, "minutes-head.md", "inline")
    assert text == "Minutes head for {{company}}."
    assert source == str(tmp_path / "minutes-head.md")


# ── {{company}} — the first line of the _global README ───────────────────────────────────────
@pytest.mark.parametrize("readme,expected", [
    ("# Acme Bank\n\nthe org handbook", "Acme Bank"),      # a heading loses its hashes
    ("Acme Bank\n", "Acme Bank"),                          # ...and a plain first line survives
    ("\n\n##   Acme Bank  \nrest", "Acme Bank"),           # leading blanks are skipped
])
def test_company_is_the_first_line_of_the_global_readme(monkeypatch, tmp_path, readme, expected):
    (tmp_path / "attendee-head.md").write_text("At {{company}}.")
    reg, ch = _rig(monkeypatch, readme=readme, mail_dir=tmp_path)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith(f"At {expected}.")


def test_an_unreadable_global_readme_degrades_honestly(monkeypatch, tmp_path):
    """Non-empty and true of every reader — the head never claims a name we do not have."""
    (tmp_path / "attendee-head.md").write_text("At {{company}}.")
    reg, ch = _rig(monkeypatch, readme=None, mail_dir=tmp_path)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith("At your organisation.")


# ── the whole shape: head → section → gap → button ───────────────────────────────────────────
def test_the_mail_is_exactly_head_blankline_section_then_the_button(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD for {{company}}.")
    outbox = "## ben@bank.test\nYou own the migration doc by Friday.\n"
    reg, ch = _rig(monkeypatch, outbox=outbox, mail_dir=tmp_path)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_Flow(attendee_followup="personal")))

    msg = next(m for m in ch.sent if m["to"] == "ben@bank.test")
    assert msg["body"] == "HEAD for Acme Bank.\n\nYou own the migration doc by Friday."
    # the gap line and the button are the PORT's, not the step's
    assert notify_mod.compose(msg["body"], msg["link"]) == msg["body"] + "\n\n" + msg["link"] + "\n"
    assert _params(msg["link"]) == {"ask": "minutes-review-invite", "meeting": "97",
                                    "tshare": "97.tok-ben@bank.test"}
    # the preamble the head replaced is gone — including the mailbox line's double splice
    assert "You were in Pilot sync" not in msg["body"]
    assert "Forward its calendar invite" not in msg["body"]
    assert "Open it and ask anything about the meeting" not in msg["body"]


# ── 2 · attendee_silent_policy ───────────────────────────────────────────────────────────────
OUTBOX = ("## _decision\nWe ship the pilot on the 21st.\n\n"
          "## ben@bank.test\nYou own the migration doc by Friday.\n")


def _personal(**params):
    return _Flow(attendee_followup="personal", **params)


def test_silent_policy_decision_is_the_default_and_sends_the_decision(monkeypatch, tmp_path):
    """cara spoke to nobody and was assigned nothing — she gets the meeting's single decision,
    and we KNOW that is what she got, which the old contract made impossible."""
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_personal()))

    assert out.result["silent_policy"] == "decision"              # the DEFAULT, unset
    assert out.result["sent"] == 2 and out.result["skipped_silent"] == []
    bodies = {m["to"]: m["body"] for m in ch.sent}
    assert bodies["ben@bank.test"] == "HEAD.\n\nYou own the migration doc by Friday."
    assert bodies["cara@bank.test"] == "HEAD.\n\nWe ship the pilot on the 21st."
    assert "## _decision" not in bodies["cara@bank.test"]          # the marker never travels


def test_silent_policy_none_sends_them_nothing_and_counts_them(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(dict(REFS), PRIOR, flow=_personal(attendee_silent_policy="none")))

    assert [m["to"] for m in ch.sent] == ["ben@bank.test"]
    assert out.result["sent"] == 1
    assert out.result["skipped_silent"] == ["cara@bank.test"]      # counted, not pretended-mailed
    assert "cara@bank.test" not in out.result["to"]


def test_silent_policy_none_with_nobody_left_sends_no_mail_at_all(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox="## _decision\nWe ship.\n", mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(dict(REFS), PRIOR, flow=_personal(attendee_silent_policy="none")))
    assert ch.sent == [] and out.result["sent"] == 0
    assert out.result["skipped_silent"] == ["ben@bank.test", "cara@bank.test"]
    assert "silent" in out.result["skipped"]


def test_a_nonsense_silent_policy_is_the_default_not_an_error(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(dict(REFS), PRIOR, flow=_personal(attendee_silent_policy="NONSENSE")))
    assert out.result["silent_policy"] == "decision" and out.result["sent"] == 2


# ── 2 · attendee_personal_max ────────────────────────────────────────────────────────────────
def _big_refs(n: int) -> dict:
    """A room of exactly n inside-domain attendees (the organiser is extra and never counted)."""
    return dict(REFS, participants=["anna@bank.test"] + [f"p{i}@bank.test" for i in range(n)])


def test_at_the_boundary_a_silent_person_still_gets_the_decision(monkeypatch, tmp_path):
    """Room size == attendee_personal_max: personal sections are still the rule, so silence is
    still governed by attendee_silent_policy."""
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(_big_refs(3), PRIOR, flow=_personal(attendee_personal_max=3)))
    assert out.result["personal_max"] == 3 and out.result["sent"] == 3
    assert {m["body"] for m in ch.sent} == {"HEAD.\n\nWe ship the pilot on the 21st."}


def test_at_the_boundary_the_none_policy_still_skips(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(_big_refs(3), PRIOR,
             flow=_personal(attendee_personal_max=3, attendee_silent_policy="none")))
    assert ch.sent == [] and out.result["skipped_silent"] == [f"p{i}@bank.test" for i in range(3)]


def test_one_above_the_boundary_the_silent_get_the_SHARED_note(monkeypatch, tmp_path):
    """Above the cap the policy does not apply: nobody is skipped and nobody gets the decision —
    everybody without a section gets the shared note, including under policy `none`."""
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](
        _ctx(_big_refs(4), PRIOR,
             flow=_personal(attendee_personal_max=3, attendee_silent_policy="none")))
    assert out.result["sent"] == 4 and out.result["skipped_silent"] == []
    assert {m["body"] for m in ch.sent} == {"HEAD.\n\n## Decided\n- ship it"}


def test_the_kick_asks_only_for_speakers_once_the_room_is_big(monkeypatch):
    """The other half of the cap: the ONE agent turn is told not to write 21 personal sections."""
    reg = Registry()
    production.build(reg, _StubDB())
    kicks = []
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, session, prompt: kicks.append(prompt) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.ag, "commit_shas", lambda uid: [])

    reg.steps["process_meeting"](_ctx(dict(_big_refs(4), native="abc"), flow=_personal(
        attendee_personal_max=3)))
    assert "SPOKE or were NAMED" in kicks[0]

    kicks.clear()
    reg.steps["process_meeting"](_ctx(dict(_big_refs(3), native="abc"), flow=_personal(
        attendee_personal_max=3)))
    assert "SPOKE or were NAMED" not in kicks[0]
    assert "actually held something" in kicks[0]


def test_the_kick_forbids_substituting_the_decision_and_demands_one_decision_section(monkeypatch):
    """The contract change that makes silence knowable at all. The old kick said 'if the meeting
    held nothing for that person, write the meeting's single decision instead' — which is exactly
    what this now forbids, because it made a silent person indistinguishable from a served one."""
    reg = Registry()
    production.build(reg, _StubDB())
    kicks = []
    monkeypatch.setattr(production.ag, "dispatch_turn",
                        lambda uid, session, prompt: kicks.append(prompt) or 0)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.ag, "commit_shas", lambda uid: [])
    reg.steps["process_meeting"](_ctx(dict(REFS, native="abc"), flow=_personal()))

    k = kicks[0]
    assert "## _decision" in k
    assert "WRITE NO SECTION for an address the meeting held nothing for" in k
    assert "do not substitute the decision" in k
    # 3 · meeting-centric, from the transcript — person-centric work happens on the click
    assert "MEETING-CENTRIC, FROM THE TRANSCRIPT" in k
    assert "when they click the link in the mail, not here" in k


# ── the agent omits `## _decision` ───────────────────────────────────────────────────────────
def test_a_missing_decision_section_falls_back_to_the_shared_note_and_says_so(monkeypatch,
                                                                              tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox="## ben@bank.test\nYou own the doc.\n", mail_dir=tmp_path)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_personal()))

    assert out.result["decision_missing"] is True                 # the receipt SAYS it degraded
    bodies = {m["to"]: m["body"] for m in ch.sent}
    assert bodies["cara@bank.test"] == "HEAD.\n\n## Decided\n- ship it"
    assert bodies["ben@bank.test"] == "HEAD.\n\nYou own the doc."


def test_decision_missing_is_false_when_the_section_is_there(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, _ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_personal()))
    assert out.result["decision_missing"] is False


def test_the_decision_key_is_never_handed_to_a_person_as_their_section(monkeypatch, tmp_path):
    """`_decision` cannot collide with an address (`_attendees` only yields strings with '@'), and
    it is popped so it can never be looked up as somebody's block either."""
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_personal()))
    assert all("_decision" not in m["body"] for m in ch.sent)
    assert all(m["to"] != "_decision" for m in ch.sent)


# ── shared mode is untouched ─────────────────────────────────────────────────────────────────
def test_shared_mode_still_sends_the_note_to_everybody(monkeypatch, tmp_path):
    (tmp_path / "attendee-head.md").write_text("HEAD.")
    reg, ch = _rig(monkeypatch, outbox=OUTBOX, mail_dir=tmp_path)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))       # default mode = shared
    assert out.result["mode"] == "shared" and out.result["sent"] == 2
    assert {m["body"] for m in ch.sent} == {"HEAD.\n\n## Decided\n- ship it"}
    assert out.result["skipped_silent"] == []
