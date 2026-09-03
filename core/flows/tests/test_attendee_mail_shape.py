"""The attendee mail's SHAPE: one template HEAD + ONE shared report, the same words to everybody.

Four elements, in order, and nothing else: head → the shared report → one gap line → one button.
The gap line and the button belong to `notify.compose`, so every assertion here reads the `link`
off the recorded call rather than hunting for a url in prose.

Four properties this file exists to hold:

  1. THERE IS ONE MAIL READER, AND IT IS `mailtext`. The live text a send reads is
     `_global/mail/<name>.md` — admin-writable, git-backed, mounted into every worker — falling
     back to the identical baked default in `flows_steps/mailtext.py`. This step used to run a
     SECOND reader of its own against the repo path `deploy/dogfood/mail/`, which is not what a
     send reads: an admin's live edit was ignored, and because that reader did not parse the
     `subject:` / `---` header the body it mailed began with the literal line `subject: … ---`.
  2. THE SUBJECT COMES OUT OF THE SAME RENDER as the body. It used to be a Python f-string, so
     editing the template could not change it.
  3. ONE REPORT, THE SAME MAIL TO EVERYONE (founder, 2026-09-02). No per-person section, no
     `## _decision`, no silent policy, no room-size cap — and therefore nothing that can hand two
     people in one room different words. Personalisation happens in the chat after the click.
  4. THE SOURCE FILE AND THE BAKED DEFAULT ARE THE SAME BYTES. `deploy/dogfood/mail/README.md`
     says they are, so a drift gate is the only thing that makes that true rather than intended.

No network, no clock, no DB: the step is called directly with the refs its flow would hand it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import flows_steps.notify as notify_mod
import pytest
from flows import Done, Reaction, Registry, StepCtx

from test_link_loop import FakeChannel, FakeScaffolds, _StubDB, _params


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    """Every production touch mints a scaffold before it sends; this stands in for agent-api."""
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


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
NOTE = "## Decided\n- ship it\n"
PRIOR = {"process_meeting": {"report": NOTE, "group": "", "room_read": []}}
THE_DATE = "14 November 2023"

HEAD_OVERRIDE = (
    "subject: {{meeting}} — what it means for you\n"
    "---\n"
    "I'm Vexa, the meeting assistant at {{company}}. I sit in meetings you're invited to; "
    "afterwards you get what came out of them and what they leave on your plate.\n"
    "\n"
    "{{organizer}} had me in {{meeting}} on {{date}}.\n")


def _ws(readme="# Acme Bank\n\nthe org handbook", mail=None):
    """A workspace reader that knows the three things this step reads: the ORG's `_global` README,
    the ORG's `_global/mail/<name>.md` overrides, and the organiser's meeting note."""
    mail = mail or {}

    def read(uid, path, slug=None):
        if slug == "_global":
            if path == "README.md":
                return readme
            if path.startswith("mail/"):
                return mail.get(path[len("mail/"):])
            return None
        # NOTHING ELSE IS READ. The report used to be re-read out of the organiser's desk; the run
        # writes into no desk now, so a step reaching for one here is the defect, not the fixture.
        raise AssertionError(f"email_attendees read a desk file: {path!r}")
    return read


def _rig(monkeypatch, *, readme="# Acme Bank\n\nthe org handbook", head=HEAD_OVERRIDE, mail=None):
    """`head` is the admin's `_global/mail/attendee-head.md`; None means they wrote none, so the
    baked default ships. Both `production` and `mailtext` are patched — `mailtext` binds `ws_file`
    into its own module namespace at import, and patching only one of them would silently test a
    reader nobody uses."""
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    files = mail if mail is not None else {}   # NOT copied: a test edits it mid-run
    if head is not None:
        files["attendee-head.md"] = head
    read = _ws(readme, files)
    monkeypatch.setattr(production, "ws_file", read)
    monkeypatch.setattr(mailtext, "ws_file", read)
    # no timezone -> UTC; every mail preference at its default, which is ON
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: "" if key == "timezone" else True)
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, mid, native=None: {"id": 97})
    monkeypatch.setattr(production.mt, "mint_transcript_share",
                        lambda uid, m, email, expires_in_sec=30 * 86400: f"97.tok-{email}")
    return reg, ch


def teardown_function():
    notify_mod.use(None)


# ── 1 · one reader, and it is the one a send actually uses ───────────────────────────────────
def test_the_head_comes_from_the_global_override_and_every_token_is_filled(monkeypatch):
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    assert isinstance(out, Done) and out.result["sent"] == 2
    body = ch.sent[0]["body"]
    assert body.startswith("I'm Vexa, the meeting assistant at Acme Bank.")   # {{company}}
    assert "anna@bank.test had me in Pilot sync on " + THE_DATE in body       # the other three
    assert "{{" not in body                                                   # nothing unfilled


def test_an_admin_override_wins_over_the_baked_default(monkeypatch):
    """THE POINT OF THE WHOLE DIRECTORY. The previous reader resolved `deploy/dogfood/mail/` in the
    REPO, so an admin's edit to the live `_global` copy changed nothing and they had no way to
    find that out."""
    reg, ch = _rig(monkeypatch, head="subject: Admin subject\n---\nAdmin wording for {{company}}.")
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    assert ch.sent[0]["subject"] == "Admin subject"
    assert ch.sent[0]["body"].startswith("Admin wording for Acme Bank.")
    # ...and the baked default, which is what would have shipped, says something else entirely
    assert "Admin wording" not in mailtext.DEFAULTS["attendee-head"]


def test_with_no_override_the_baked_default_ships(monkeypatch):
    """A fresh deployment mails correctly before anybody has edited anything."""
    reg, ch = _rig(monkeypatch, head=None)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    assert ch.sent[0]["subject"] == "Pilot sync — what it means for you"
    assert ch.sent[0]["body"].startswith("I am Vexa, the meeting assistant at Acme Bank.")
    assert "anna@bank.test had me in Pilot sync on " + THE_DATE in ch.sent[0]["body"]
    assert "{{" not in ch.sent[0]["body"]


def test_the_override_is_re_read_on_every_send_so_an_edit_needs_no_restart(monkeypatch):
    live = {"attendee-head.md": "First wording for {{company}}."}
    reg, ch = _rig(monkeypatch, head=None, mail=live)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith("First wording for Acme Bank.")

    live["attendee-head.md"] = "Second wording for {{company}}."   # the admin edits between runs
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))          # no reload, no restart
    assert ch.sent[-1]["body"].startswith("Second wording for Acme Bank.")


def test_an_emptied_override_falls_back_rather_than_mailing_a_blank(monkeypatch):
    """A file the admin cleared by accident is not a mail with no introduction. `"   \\n\\n"` is
    truthy in Python, so `or` alone did not catch this."""
    reg, ch = _rig(monkeypatch, head="   \n\n")
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith("I am Vexa, the meeting assistant at Acme Bank.")
    assert ch.sent[0]["subject"] == "Pilot sync — what it means for you"


# ── 2 · the subject is rendered, and its header never travels ────────────────────────────────
def test_the_subject_comes_from_the_template_not_from_a_python_f_string(monkeypatch):
    reg, ch = _rig(monkeypatch, head="subject: Notes on {{meeting}} ({{date}})\n---\nHEAD.")
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert {m["subject"] for m in ch.sent} == {f"Notes on Pilot sync ({THE_DATE})"}


@pytest.mark.parametrize("head", [
    HEAD_OVERRIDE,                                   # an override with a header
    None,                                            # the baked default
    "subject: S\n---\nBody one.",                    # the minimal header
])
def test_the_subject_header_never_appears_in_a_body(monkeypatch, head):
    """THE DEFECT THIS REPLACES. The previous reader did not know the `subject:` / `---` header
    existed, so it mailed it: every attendee's introduction opened with two lines of machinery."""
    reg, ch = _rig(monkeypatch, head=head)
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent, "the fan-out sent nothing, so this asserts nothing"
    for m in ch.sent:
        assert not m["body"].lstrip().lower().startswith("subject:")
        assert "\n---\n" not in m["body"]
        assert m["subject"], "an empty subject line reads as spam"


def test_a_template_with_no_subject_line_falls_back_to_the_meeting_title(monkeypatch, caplog):
    reg, ch = _rig(monkeypatch, head="No header here, just {{company}}.")
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["subject"] == "Pilot sync"
    assert ch.sent[0]["body"] == "No header here, just Acme Bank.\n\n## Decided\n- ship it"
    assert "no `subject:` line" in "\n".join(r.getMessage() for r in caplog.records)


# ── 3 · one report, the same mail to everyone ────────────────────────────────────────────────
def test_the_mail_is_exactly_head_blankline_report_then_the_button(monkeypatch, scaffolds):
    reg, ch = _rig(monkeypatch, head="HEAD for {{company}}.")
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    msg = next(m for m in ch.sent if m["to"] == "ben@bank.test")
    assert msg["body"] == "HEAD for Acme Bank.\n\n## Decided\n- ship it"
    # the gap line and the button are the PORT's, not the step's
    assert notify_mod.compose(msg["body"], msg["link"]) == msg["body"] + "\n\n" + msg["link"] + "\n"
    # THE BUTTON IS AN ID — the preset moved into the record (PRD §5.5), and so did the capability
    # (R-A08: a bearer token in a query string leaks into every log the link passes through).
    assert set(_params(msg["link"])) == {"s"}
    rec = scaffolds.for_("ben@bank.test")
    assert rec["share_token"] == "97.tok-ben@bank.test"
    assert (rec["kind"], rec["opening"], rec["meeting"]) == \
        ("invite-offer", "minutes-review-invite", "97")
    # the preamble the head replaced is gone — including the mailbox line's double splice
    assert "You were in Pilot sync" not in msg["body"]
    assert "Forward its calendar invite" not in msg["body"]
    assert "Open it and ask anything about the meeting" not in msg["body"]


def test_everybody_in_the_room_gets_byte_identical_words(monkeypatch, scaffolds):
    """The founder's simplification, as a test: two attendees, one report, and the ONLY thing that
    may differ between them is the share token on their own record.

    Since R-A08 the mails are byte-identical INCLUDING the button, because the capability left the
    URL — which makes the claim stronger than it was, not weaker: there is now nothing per-person in
    the mail at all."""
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    assert out.result["sent"] == 2 and out.result["followup"] == "on"
    assert len({m["body"] for m in ch.sent}) == 1
    assert len({m["subject"] for m in ch.sent}) == 1
    assert len({m["link"] for m in ch.sent}) == 2            # one id each, and only the id differs
    assert all(set(_params(m["link"])) == {"s"} for m in ch.sent)
    assert {scaffolds.for_(m["to"])["share_token"] for m in ch.sent} == {
        "97.tok-ben@bank.test", "97.tok-cara@bank.test"}


def test_a_big_room_changes_nothing_about_what_anyone_receives(monkeypatch):
    """There is no room-size cap any more, because there is nothing left for it to cap: a report
    written once costs the same to send to four people as to forty."""
    reg, ch = _rig(monkeypatch, head="HEAD.")
    refs = dict(REFS, participants=["anna@bank.test"] + [f"p{i}@bank.test" for i in range(9)])
    out = reg.steps["email_attendees"](_ctx(refs, PRIOR))
    assert out.result["sent"] == 9
    assert {m["body"] for m in ch.sent} == {"HEAD.\n\n## Decided\n- ship it"}


def test_the_organiser_and_the_attendees_get_the_same_report(monkeypatch):
    """`email_minutes` and `email_attendees` are two sends of ONE artefact. The heads differ — a
    returning person is not introduced to Vexa again, per `deploy/dogfood/mail/README.md` — but
    the report inside both is the same `_readable(note)` string."""
    reg, ch = _rig(monkeypatch, head="HEAD.")
    reg.steps["email_minutes"](_ctx(dict(REFS), PRIOR))
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))

    by_to = {m["to"]: m["body"] for m in ch.sent}
    assert set(by_to) == {"anna@bank.test", "ben@bank.test", "cara@bank.test"}
    report = "## Decided\n- ship it"
    assert report in by_to["anna@bank.test"] and report in by_to["ben@bank.test"]


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


def test_the_removed_params_no_longer_exist(monkeypatch):
    """`attendee_silent_policy` and `attendee_personal_max` are gone rather than left inert: a
    param a deployment can still set, that silently does nothing, is worse than one that is not
    there."""
    src = Path(production.__file__).with_suffix(".py").read_text()
    body = src.split("REMOVED WITH THE AXIS", 1)[1].split('"""', 1)[1]   # past the explanation
    assert "attendee_silent_policy" not in body
    assert "attendee_personal_max" not in body


def test_a_per_meeting_opt_out_still_turns_the_fan_out_off(monkeypatch):
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS, share=False), PRIOR))
    assert ch.sent == [] and out.result["sent"] == 0 and out.result["followup"] == "off"


def test_the_param_kill_switch_still_turns_the_fan_out_off(monkeypatch):
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_Flow(attendee_followup="off")))
    assert ch.sent == [] and out.result["sent"] == 0 and out.result["followup"] == "off"


def test_an_unset_param_is_ON(monkeypatch):
    """The default IS the coefficient: a fan-out that ships off is a loop nobody sees. Spelled out
    because `str(None)` is the string `"none"`, which the off-list contains."""
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR, flow=_Flow()))
    assert out.result["sent"] == 2 and out.result["followup"] == "on"


# ── {{company}} — mailtext's own rule, not a second one ──────────────────────────────────────
@pytest.mark.parametrize("readme,expected", [
    ("# Acme Bank\n\nthe org handbook", "Acme Bank"),          # the heading the setup gate demands
    ("\n\n#  Acme Bank  \nrest", "Acme Bank"),                 # leading blanks are skipped
    ("Acme Bank\n", mailtext.COMPANY_UNSET),                   # no heading is not a company name
    (None, mailtext.COMPANY_UNSET),                            # nor is an unreadable README
])
def test_company_follows_mailtexts_rule(monkeypatch, readme, expected):
    """`mailtext.company_name` takes the FIRST HEADING of `_global/README.md` and nothing else.
    The step used to carry its own looser reader that stripped hashes off any first line and
    degraded to "your organisation" — two answers to one question, and `_global/README.md` is
    written by the setup gate, which requires the heading. The fallback string is deliberately
    unpretty: reaching a recipient means the gate let a mail out before the company layer
    existed, which is a bug, not a wording choice."""
    reg, ch = _rig(monkeypatch, readme=readme, head="At {{company}}.")
    reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    assert ch.sent[0]["body"].startswith(f"At {expected}.")


# ── the source of truth and the baked default do not drift ───────────────────────────────────
def test_the_baked_defaults_match_the_files_in_deploy_dogfood_mail():
    """`deploy/dogfood/mail/README.md`: those files are the SOURCE, the baked defaults are the
    same content, "edited in both, or the source lies". They HAD drifted — the baked
    `attendee-head` substituted `{{title}}`, `{{when}}` and `{{attendees}}`, none of which the
    step fills, while the file substitutes `{{company}} {{organizer}} {{meeting}} {{date}}`. A
    deployment with no override would have mailed a stranger standing braces. Nothing read both,
    so nothing noticed; this reads both."""
    root = Path(mailtext.__file__).resolve().parents[4]      # <repo>/core/flows/src/flows_steps
    mail_dir = root / "deploy" / "dogfood" / "mail"
    if not mail_dir.is_dir():                                # the image is not a checkout
        pytest.skip(f"no source directory at {mail_dir}")
    for name, baked in mailtext.DEFAULTS.items():
        f = mail_dir / f"{name}.md"
        assert f.is_file(), f"{f} is missing — the baked default has no source to be edited in"
        assert f.read_text().strip() == baked.strip(), (
            f"{f} and mailtext.DEFAULTS[{name!r}] have drifted apart")


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
    reg.steps["process_meeting"](_ctx(dict(REFS, native="abc", group="dna-tsc")))

    k = kicks[0]
    assert "THIS MEETING BELONGS TO THE GROUP #dna-tsc" in k
    assert "MAINTAIN" in k and "READ/WRITE" in k
    for page in ("its PEOPLE", "its DECISIONS", "its OPEN ITEMS", "its README"):
        assert page in k
    assert "Maintaining is not appending" in k
    # ...and it is still the one desk it writes to
    assert "WRITE NO FILES FOR THIS REPORT" in k
    assert "never copy one person's desk into the group's" in k
