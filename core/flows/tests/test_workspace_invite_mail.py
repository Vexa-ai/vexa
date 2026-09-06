"""THE MEMBERSHIP INVITE'S MAIL LEG — Vexa-ai/vexa#1632.

*"this add member should just ask chat to do that with mcp, asking their emails etc."* (founder,
2026-09-06, on the front page answering `invite role must be one of ('contributor',)`.) Membership
is a conversation: the agent asks for the address and the role, confirms in one sentence, mints the
invite — and when the address is EXTERNAL to this instance, flows carries it out as mail. This file
is that last clause, and nothing else. Who may invite, what a role means and what gets committed
are the producer's, and they are tested on the producer's side.

Five properties:

  1. THE FLOW EXISTS AND IS THE ONLY ONE ON THIS FACT. A carrier has exactly one producing domain;
     a fact with two consumers that both mail is how one invite mails twice.
  2. THE MAIL IS WHAT THE TEMPLATE RENDERS, and the LINK IS NOT IN IT. `notify.compose` appends the
     one call to action; a step that pasted the url into prose would still "work" and would put a
     bare URL mid-paragraph in a stranger's first mail from us.
  3. NO ADDRESS OR NO LINK REFUSES, TYPED AND TERMINAL, and sends nothing. A mail with no link asks
     somebody to do nothing, and the refs are frozen at admission, so retrying asks the same
     unanswerable question of the same row forever.
  4. AN ADMIN'S `_global/mail/workspace-invite.md` WINS. The wording is a file edit, not a rebuild —
     the whole reason `mailtext` exists.
  5. ONE INVITE, ONE MAIL. `source_event_id` is keyed to the invite, so a redelivery is a no-op at
     the intake; this drives the real engine over a real sqlite double to prove it rather than
     asserting it about the id string.

Offline like the rest of the suite: no network, no clock, no SMTP. The workspace reader is a fake
in both namespaces that hold one (`mailtext` and `policies` each bind `ws_file` at import), so the
mail is deterministic and the agent door is never knocked on.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent_half  # noqa: E402
import flows_defs.production as production  # noqa: E402
import flows_steps.mailtext as mailtext  # noqa: E402
import flows_steps.notify as notify_mod  # noqa: E402
import flows_steps.policies as policies  # noqa: E402
from flows import Done, FakeClock, Reaction, Registry, StepCtx, StepError, admit, status, tick  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

from test_link_loop import FakeChannel, _StubDB  # noqa: E402

pytestmark = agent_half.required

#: The fact agent-api publishes, refs and all. `uid` is the INVITER's subject — the person this
#: deployment can look a setting up for — and `email` is the invitee's, who by construction has no
#: account here. Names are the repository's stock examples, never a customer's.
REFS = {
    "uid": "7",
    "email": "jsmith@example.com",
    "workspace": "pilot",
    "workspace_name": "Pilot",
    "role": "contributor",
    "role_sentence": "A contributor reads this desk and writes to it.",
    "inviter": "anna@bank.test",
    "link": "https://app.example.test/invite/tok-abc",
    "expires_at": 1_700_600_000,
}

#: `invite-<workspace>-<invite_id>` — the producer's scheme, keyed to the INVITE.
SOURCE_ID = "invite-pilot-inv_001"


def _ctx(refs: dict) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={}, clock_now=1_700_000_000.0,
                   scratch={})


def _ws(readme="# Acme Bank\n\nthe org handbook", mail=None):
    """A `_global` reader that knows the two files this mail is composed from: the company's README
    (`{{company}}`) and any `mail/<name>.md` override. Everything else answers None, which is what
    `policies.read` needs to resolve to its defaults — and therefore what makes `{{visibility}}`
    the founder's own sentence rather than whatever a live deployment happens to hold."""
    mail = mail or {}

    def read(uid, path, slug=None):
        if slug == "_global":
            if path == "README.md":
                return readme
            if path.startswith("mail/"):
                return mail.get(path[len("mail/"):])
        return None
    return read


def _rig(monkeypatch, *, readme="# Acme Bank\n\nthe org handbook", override=None, db=None):
    """The registry, the recorded channel, and a workspace reader in BOTH namespaces that bind one.

    `mailtext` and `policies` each do `from .common import ws_file`, so patching one leaves the
    other reaching the real agent door — which in this process is `127.0.0.1:1` and would make the
    visibility sentence depend on a connection being refused fast enough."""
    reg = Registry()
    production.build(reg, db if db is not None else _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    read = _ws(readme, {"workspace-invite.md": override} if override is not None else {})
    monkeypatch.setattr(mailtext, "ws_file", read)
    monkeypatch.setattr(policies, "ws_file", read)
    return reg, ch


def teardown_function():
    notify_mod.use(None)


# ── 1 · the flow is registered, on that fact, with that one step ─────────────────────────────
def test_the_flow_is_registered_on_workspace_invited_and_is_the_only_one(monkeypatch):
    """A carrier has exactly ONE producing domain and this fact has exactly one consumer. Two
    flows on it would each mail the invitee, and the census could not tell you which."""
    reg, _ = _rig(monkeypatch)
    flows = reg.by_event[production.WORKSPACE_INVITED.name]
    assert [f.name for f in flows] == ["workspace_invite"]
    assert flows[0].steps == ("mail_workspace_invite",)


def test_the_step_reaches_no_domain_and_says_so_by_declaring_nothing(monkeypatch):
    """It renders a template and posts one notification. `needs=("agent",)` here would be a lie
    that the engine acts on: it would answer `agent:not_present` and skip a mail the mailbox could
    have carried. What is agent-shaped is the PRODUCER, and that is expressed by which module
    registers the flow, not by what the step declares."""
    reg, _ = _rig(monkeypatch)
    assert reg.needs("mail_workspace_invite") == frozenset()


def test_the_step_runs_to_done_and_names_what_it_sent(monkeypatch):
    reg, ch = _rig(monkeypatch)
    out = reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))

    assert isinstance(out, Done)
    assert out.result["to"] == "jsmith@example.com"
    assert out.result["workspace"] == "pilot"
    # The channel's own handle, carried on the receipt as `provider_ref` — what an operator joins
    # a delivery question back to the mail that was actually sent.
    assert out.result["message_id"] == "<fake-1@test>"
    assert out.provider_ref == out.result["message_id"]
    assert len(ch.sent) == 1


# ── 2 · the mail is the template, and the link is the port's argument ────────────────────────
def test_the_recipient_subject_and_body_are_what_the_template_renders(monkeypatch):
    reg, ch = _rig(monkeypatch)
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))

    msg = ch.sent[0]
    assert msg["to"] == "jsmith@example.com"
    assert msg["subject"] == "anna@bank.test invited you to Pilot"
    assert msg["body"] == "\n".join([
        f"I am Vexa, the meeting assistant at Acme Bank. {mailtext.SERVICE_SENTENCE}",
        "",
        "anna@bank.test invited you to Pilot as a contributor. "
        "A contributor reads this desk and writes to it.",
        "",
        mailtext.VISIBILITY_SENTENCE,
        "",
        "The link below is yours alone — it signs you in, so please do not forward it.",
    ])
    assert "{{" not in msg["body"] and "{{" not in msg["subject"]


def test_the_link_travels_as_the_ports_argument_and_never_inside_the_body(monkeypatch):
    """The property this file exists for most. A step that pasted the url into its own prose would
    pass every other assertion here and would put a bare link mid-paragraph in the first mail this
    person ever gets from us — `notify.compose` is what makes it the last line and its own
    paragraph, and it can only do that if the step hands it over separately."""
    reg, ch = _rig(monkeypatch)
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))

    msg = ch.sent[0]
    assert msg["link"] == REFS["link"]
    assert REFS["link"] not in msg["body"]
    assert "http" not in msg["body"]
    assert notify_mod.compose(msg["body"], msg["link"]) == msg["body"] + "\n\n" + msg["link"] + "\n"


def test_the_subject_header_never_travels_in_the_body(monkeypatch):
    reg, ch = _rig(monkeypatch)
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))
    body = ch.sent[0]["body"]
    assert not body.lstrip().lower().startswith("subject:")
    assert "\n---\n" not in body


def test_a_template_with_no_subject_line_falls_back_and_says_so(monkeypatch, caplog):
    """An empty subject reads as spam. The warning is the point: only somebody editing the live
    file can get here, and they should be able to find out that they did."""
    reg, ch = _rig(monkeypatch, override="No header here, just {{workspace_name}}.")
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))

    assert ch.sent[0]["subject"] == "You have been invited to Pilot"
    assert "no `subject:` line" in "\n".join(r.getMessage() for r in caplog.records)


def test_the_display_name_falls_back_to_the_slug_rather_than_leaving_a_hole(monkeypatch):
    """agent-api sends the slug when a workspace has no display name; this is the belt for a
    producer that forgets. A fallback rather than a token left standing, because a subject line
    with `{{workspace_name}}` in it is not deliverable prose."""
    reg, ch = _rig(monkeypatch)
    refs = dict(REFS)
    del refs["workspace_name"]
    reg.steps["mail_workspace_invite"](_ctx(refs))
    assert ch.sent[0]["subject"] == "anna@bank.test invited you to pilot"


def test_a_ref_the_producer_dropped_is_left_standing_rather_than_blanked(monkeypatch):
    """`mailtext.render`'s own rule: a visible `{{role}}` in a test inbox is a bug report and a
    silently empty sentence is not. Asserted here because the step chooses which values it passes,
    and passing `""` for a missing one would defeat it."""
    reg, ch = _rig(monkeypatch)
    refs = dict(REFS)
    del refs["role"]
    reg.steps["mail_workspace_invite"](_ctx(refs))
    assert "as a {{role}}." in ch.sent[0]["body"]


# ── 3 · no address or no link refuses, typed and terminal ────────────────────────────────────
@pytest.mark.parametrize("missing", ["email", "link"])
def test_a_missing_address_or_link_refuses_and_sends_nothing(monkeypatch, missing):
    reg, ch = _rig(monkeypatch)
    refs = dict(REFS)
    del refs[missing]

    with pytest.raises(StepError) as e:
        reg.steps["mail_workspace_invite"](_ctx(refs))
    assert e.value.retryable is False, (
        "the refs are frozen at admission — a retry asks the same unanswerable question of the "
        "same row, every backoff, forever")
    assert "pilot" in str(e.value), "the refusal must name the workspace an operator would look up"
    assert ch.sent == [], "a refusal that already mailed is not a refusal"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_present_but_empty_ref_is_the_same_refusal(monkeypatch, blank):
    """Whitespace is not an address. Spelled out because `ctx.refs.get("email")` is truthy for
    `"  "`, and a mail to a blank recipient fails at the transport with a stack trace instead of
    at the door with a sentence."""
    reg, ch = _rig(monkeypatch)
    with pytest.raises(StepError) as e:
        reg.steps["mail_workspace_invite"](_ctx(dict(REFS, email=blank)))
    assert e.value.retryable is False
    assert ch.sent == []


# ── 4 · the admin's file wins over the baked default ─────────────────────────────────────────
def test_an_admin_override_wins_over_the_baked_default(monkeypatch):
    """THE POINT OF THE WHOLE DIRECTORY: the founder's rewrite of a stranger's first mail is a file
    edit, not a review, a rebuild and a deploy."""
    reg, ch = _rig(monkeypatch,
                   override="subject: Admin subject\n---\nAdmin wording for {{workspace_name}}.")
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))

    assert ch.sent[0]["subject"] == "Admin subject"
    assert ch.sent[0]["body"] == "Admin wording for Pilot."
    assert "Admin wording" not in mailtext.DEFAULTS["workspace-invite"]


def test_with_no_override_the_baked_default_ships(monkeypatch):
    """A fresh deployment mails correctly before anybody has edited anything."""
    reg, ch = _rig(monkeypatch)
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))
    assert ch.sent[0]["body"].startswith("I am Vexa, the meeting assistant at Acme Bank.")


def test_an_emptied_override_falls_back_rather_than_mailing_a_blank(monkeypatch):
    """A file the admin cleared by accident is not a mail with no introduction — `"   \\n\\n"` is
    truthy in Python, which is why `mailtext.render` tests the stripped value."""
    reg, ch = _rig(monkeypatch, override="   \n\n")
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))
    assert ch.sent[0]["body"].startswith("I am Vexa, the meeting assistant at Acme Bank.")
    assert ch.sent[0]["subject"] == "anna@bank.test invited you to Pilot"


def test_the_stranger_facing_sentences_are_in_the_mail(monkeypatch):
    """This may be the first mail this person ever gets from this deployment, so it carries the
    same two things `attendee-head` carries: whose Vexa this is, and who can see what. The
    visibility sentence is a TOKEN here rather than a literal, so it stays derived from the rules
    this deployment actually runs under instead of becoming a fourth copy to keep in step."""
    reg, ch = _rig(monkeypatch)
    reg.steps["mail_workspace_invite"](_ctx(dict(REFS)))
    body = ch.sent[0]["body"]
    assert mailtext.SERVICE_SENTENCE in body
    assert mailtext.VISIBILITY_SENTENCE in body
    assert "Acme Bank" in body


# ── 5 · one invite, one mail ─────────────────────────────────────────────────────────────────
def _drain(db, reg, clock, budget=100):
    for _ in range(budget):
        if tick(db, reg, clock):
            continue
        nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                         "WHERE status IN ('admitted','retrying')")[0][0]
        if nxt is None:
            return
        clock._t = max(clock._t, nxt)


def test_a_redelivery_of_the_same_invite_mails_exactly_once(monkeypatch):
    """THE GUARANTEE THAT MATTERS TO THE PERSON RECEIVING IT. `source_event_id` is
    `invite-<workspace>-<invite_id>` — keyed to the INVITE — so the intake's `(source_event_id,
    flow)` dedup makes a redelivery a no-op. Driven through the real engine rather than asserted
    about the id string: what is being checked is that a second `admit` produces no second mail,
    and only the engine can answer that."""
    db, clock = SqliteDB(), FakeClock(1_700_000_000.0)
    reg, ch = _rig(monkeypatch, db=db)

    assert admit(db, reg, clock, source_event_id=SOURCE_ID,
                 event_type=production.WORKSPACE_INVITED.name, subject_refs=dict(REFS)) == 1
    assert admit(db, reg, clock, source_event_id=SOURCE_ID,
                 event_type=production.WORKSPACE_INVITED.name, subject_refs=dict(REFS)) == 0
    _drain(db, reg, clock)

    assert [m["to"] for m in ch.sent] == ["jsmith@example.com"]
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    assert status(db, rid)["status"] == "done"


def test_a_second_invite_to_the_same_person_is_a_second_mail(monkeypatch):
    """The other half, and the reason the carrier is `once_per_occurrence` and holds no stamp: a
    re-invite after an expiry, or the same address at a different role, is a second FACT. Deduping
    it away would leave the inviter told an invitation went out and the invitee with nothing."""
    db, clock = SqliteDB(), FakeClock(1_700_000_000.0)
    reg, ch = _rig(monkeypatch, db=db)

    admit(db, reg, clock, source_event_id="invite-pilot-inv_001",
          event_type=production.WORKSPACE_INVITED.name, subject_refs=dict(REFS))
    admit(db, reg, clock, source_event_id="invite-pilot-inv_002",
          event_type=production.WORKSPACE_INVITED.name,
          subject_refs=dict(REFS, role="reader", link="https://app.example.test/invite/tok-def"))
    _drain(db, reg, clock)

    assert len(ch.sent) == 2
    assert {m["link"] for m in ch.sent} == {"https://app.example.test/invite/tok-abc",
                                            "https://app.example.test/invite/tok-def"}


def test_a_fact_with_no_link_ends_terminal_and_mails_nobody(monkeypatch):
    """The refusal, through the engine rather than the step: not retryable means the reaction ends
    `failed` on its first attempt instead of knocking every backoff forever."""
    db, clock = SqliteDB(), FakeClock(1_700_000_000.0)
    reg, ch = _rig(monkeypatch, db=db)
    refs = dict(REFS)
    del refs["link"]

    admit(db, reg, clock, source_event_id=SOURCE_ID,
          event_type=production.WORKSPACE_INVITED.name, subject_refs=refs)
    _drain(db, reg, clock)

    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    st = status(db, rid)
    assert st["status"] == "failed"
    assert "asks somebody to do nothing" in (st["reason"] or "")
    assert ch.sent == []


# ── 6 · the invite goes to the INVITEE, and never to the inviter (Vexa-ai/vexa#1648) ─────────
def test_the_invite_is_addressed_to_the_invitee_and_never_to_the_inviter(monkeypatch):
    """The failure this rules out was suspected on 2026-09-06, when a founder reported an invite as
    having reached his own mailbox instead of the person's. It had not — the mail sink showed the
    invitee in both the `To` header and the SMTP envelope, and the real defect was one surface
    further on, in the MEETING WRITE-UP. But the failure mode is a real one and nothing pinned it:
    the refs carry an address AND a uid, `uid` IS THE INVITER, and a step that mailed `uid` would
    mail the sender their own invitation (`publish.invite_refs` says exactly that in prose).

    So: the recipient is `email` and it tracks `email`, while `uid` and `inviter` move under it
    without ever becoming the address."""
    reg, ch = _rig(monkeypatch)
    refs = dict(REFS, email="marvin@example.test", uid="176", inviter="admin@example.test")
    reg.steps["mail_workspace_invite"](_ctx(refs))

    assert [m["to"] for m in ch.sent] == ["marvin@example.test"]
    # the inviter appears in the SUBJECT (it is their invitation) and nowhere in the addressing
    assert "admin@example.test" in ch.sent[0]["subject"]
    assert ch.sent[0]["to"] != refs["inviter"] and ch.sent[0]["to"] != refs["uid"]
