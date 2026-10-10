"""THE MEETING SHARE'S MAIL LEG — `meeting.shared` → `meeting_share` → one mail.

A meeting's owner pressed **Invite** in the Share dialog with an address and asked for the mail;
meeting-api minted a grant restricted to that address and handed the fact over. This file is the
mail, and nothing else — who may share, and what the grant admits, are meeting-api's and are tested
there (`test_meeting_share_access.py`).

  1. The flow exists, on that fact, with one step that reaches no domain.
  2. The mail is what the template renders; the LINK is the port's argument, never in the body, and
     it is composed HERE from this deployment's own VEXA_UI_URL — the producer sends a token, not a
     URL, and no template writes one.
  3. A bundled workspace invite rides the same link; an absent one leaves no empty parameter.
  4. No address or no token refuses, typed and terminal, sends nothing, and never echoes the token.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_defs.production as production  # noqa: E402
import flows_steps.mailtext as mailtext  # noqa: E402
import flows_steps.notify as notify_mod  # noqa: E402
import flows_steps.policies as policies  # noqa: E402
from flows import Done, Reaction, Registry, StepCtx, StepError  # noqa: E402

from test_link_loop import FakeChannel, _StubDB  # noqa: E402
from test_workspace_invite_mail import _ws  # noqa: E402

UI = "https://app.example.test"
REFS = {"uid": "7", "meeting_id": "42", "email": "jsmith@example.com", "grant_id": "g1",
        "title": "Weekly sync", "inviter": "anna@bank.test"}
#: What the step's own mint returns — the recipient's link is minted at SEND time (R1801-5).
MINTED = "42.minted-at-send-time"


def _ctx(refs):
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={}, clock_now=1_700_000_000.0, scratch={})


def _mint(minted):
    def mint(uid, meeting_id, email, expires_in_sec=0, *, requires_grant=""):
        minted.append((uid, meeting_id, email))
        assert requires_grant == REFS["grant_id"], "the send-time mint names the invite it carries out"
        return MINTED
    return mint


@pytest.fixture()
def rig(monkeypatch):
    monkeypatch.setenv("VEXA_UI_URL", UI)
    minted = []
    monkeypatch.setattr(production.mt, "mint_transcript_share", _mint(minted))
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    read = _ws()
    monkeypatch.setattr(mailtext, "ws_file", read)
    monkeypatch.setattr(policies, "ws_file", read)
    yield reg, ch
    notify_mod.use(None)


def test_the_flow_is_registered_on_meeting_shared_with_one_domainless_step(rig):
    reg, _ = rig
    flows = reg.by_event[production.SHARED.name]
    assert [f.name for f in flows] == ["meeting_share"]
    assert flows[0].steps == ("mail_meeting_share",)
    assert reg.needs("mail_meeting_share") == frozenset({"meetings"})


def test_the_mail_is_the_template_and_the_link_is_composed_here(rig):
    reg, ch = rig
    out = reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert isinstance(out, Done) and out.result["to"] == "jsmith@example.com"
    msg = ch.sent[0]
    assert msg["subject"] == "anna@bank.test shared Weekly sync with you"
    assert "{{" not in msg["subject"] + msg["body"]
    assert "http" not in msg["body"] and MINTED not in msg["body"], "the link is never in prose"
    link = urlparse(msg["link"])
    assert f"{link.scheme}://{link.netloc}" == UI
    assert parse_qs(link.query) == {"tshare": [MINTED]}


def test_the_link_is_minted_at_send_time_for_exactly_this_address_as_the_owner(rig, monkeypatch):
    """R1801-5: the fact carries no credential; the step mints the recipient's own restricted
    grant, as the owner, for this one address — so the event store never holds a working token."""
    reg, ch = rig
    minted = []
    monkeypatch.setattr(production.mt, "mint_transcript_share", _mint(minted))
    reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert minted == [("7", "42", "jsmith@example.com")]
    assert "token" not in REFS


def test_an_owner_title_is_one_bounded_plain_line(rig):
    """R1801-2: whatever the producer sent, the subject is one line, bounded."""
    reg, ch = rig
    reg.steps["mail_meeting_share"](_ctx({**REFS, "title": "Board\r\nBcc: all@example.test " + "x" * 500}))
    subj = ch.sent[0]["subject"]
    assert "\r" not in subj and "\n" not in subj and len(subj) < 300


def test_a_failed_mint_sends_nothing(rig, monkeypatch):
    reg, ch = rig
    def boom(*a, **k):
        raise production.mt.ShareMintError(meeting_id="42", identity="jsmith@example.com", status=404,
                                           detail="Meeting 42 not found", retryable=False)
    monkeypatch.setattr(production.mt, "mint_transcript_share", boom)
    with pytest.raises(StepError) as e:
        reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert not e.value.retryable and ch.sent == []


def test_missing_words_fall_back_to_plain_ones(rig):
    reg, ch = rig
    reg.steps["mail_meeting_share"](_ctx({k: v for k, v in REFS.items() if k not in ("title", "inviter")}))
    assert ch.sent[0]["subject"] == "Someone shared a meeting with you"


@pytest.mark.parametrize("missing", ["email", "uid", "meeting_id"])
def test_no_address_owner_or_meeting_refuses_and_sends_nothing(rig, missing):
    reg, ch = rig
    with pytest.raises(StepError) as e:
        reg.steps["mail_meeting_share"](_ctx({**REFS, missing: ""}))
    assert not e.value.retryable
    assert ch.sent == []


def test_the_mail_goes_out_on_a_deployment_with_no_agent_domain(monkeypatch):
    """The step reaches no domain, and neither may its wording: with no agent-api there is no
    `_global` to read, which is an empty company layer — the baked template and "this
    organisation" — not a failed send. Found live on a stack with flows and meetings but no agent
    door configured for flows: the step retried on `AgentDomainAbsent` and the mail never left."""
    # The class `mailtext` itself catches: other suites reload `flows_steps.common`, so a fresh
    # import here could name a different class object than the one the module under test holds.
    def absent(*_a, **_k):
        raise mailtext.AgentDomainAbsent("no agent domain here")
    monkeypatch.setenv("VEXA_UI_URL", UI)
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    monkeypatch.setattr(mailtext, "ws_file", absent)
    monkeypatch.setattr(policies, "ws_file", absent)
    monkeypatch.setattr(production.mt, "mint_transcript_share", _mint([]))
    try:
        out = reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    finally:
        notify_mod.use(None)
    assert isinstance(out, Done) and ch.sent[0]["subject"] == "anna@bank.test shared Weekly sync with you"
    assert mailtext.COMPANY_UNSET in ch.sent[0]["body"]


def test_a_broken_agent_door_is_still_an_error_not_an_absence(monkeypatch):
    def broken(*_a, **_k):
        raise ConnectionError("agent-api refused")
    monkeypatch.setattr(mailtext, "ws_file", broken)
    with pytest.raises(ConnectionError):
        mailtext.render("meeting-share", "7", {"inviter": "a", "title": "b"})



def test_an_invite_withdrawn_between_publish_and_send_sends_nothing(rig, monkeypatch):
    """R1801-12: the owner revoked the invite (or removed the person) after pressing Invite. The
    send-time mint is refused by meeting-api (409), so no link exists and nothing is mailed — and
    the reaction finishes saying why rather than retrying."""
    reg, ch = rig

    def withdrawn(uid, meeting_id, email, expires_in_sec=0, *, requires_grant=""):
        raise production.mt.ShareMintError(meeting_id=meeting_id, identity=email, status=409,
                                           detail="the invite was withdrawn", retryable=False)
    monkeypatch.setattr(production.mt, "mint_transcript_share", withdrawn)
    out = reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert isinstance(out, Done) and out.result["sent"] is False
    assert ch.sent == []
