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
REFS = {"uid": "7", "meeting_id": "42", "email": "jsmith@example.com", "token": "42.s3cr3t-token",
        "title": "Weekly sync", "inviter": "anna@bank.test"}


def _ctx(refs):
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={}, clock_now=1_700_000_000.0, scratch={})


@pytest.fixture()
def rig(monkeypatch):
    monkeypatch.setenv("VEXA_UI_URL", UI)
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
    assert reg.needs("mail_meeting_share") == frozenset()


def test_the_mail_is_the_template_and_the_link_is_composed_here(rig):
    reg, ch = rig
    out = reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert isinstance(out, Done) and out.result["to"] == "jsmith@example.com"
    msg = ch.sent[0]
    assert msg["subject"] == "anna@bank.test shared Weekly sync with you"
    assert "{{" not in msg["subject"] + msg["body"]
    assert "http" not in msg["body"] and REFS["token"] not in msg["body"], "the link is never in prose"
    link = urlparse(msg["link"])
    assert f"{link.scheme}://{link.netloc}" == UI
    assert parse_qs(link.query) == {"tshare": [REFS["token"]]}


def test_a_bundled_workspace_invite_rides_the_same_link(rig):
    reg, ch = rig
    reg.steps["mail_meeting_share"](_ctx({**REFS, "workspace_invite": "W" * 43}))
    assert parse_qs(urlparse(ch.sent[0]["link"]).query) == {"tshare": [REFS["token"]], "invite": ["W" * 43]}


def test_missing_words_fall_back_to_plain_ones(rig):
    reg, ch = rig
    reg.steps["mail_meeting_share"](_ctx({k: v for k, v in REFS.items() if k not in ("title", "inviter")}))
    assert ch.sent[0]["subject"] == "Someone shared a meeting with you"


@pytest.mark.parametrize("missing", ["email", "token"])
def test_no_address_or_no_token_refuses_and_sends_nothing(rig, missing):
    reg, ch = rig
    with pytest.raises(StepError) as e:
        reg.steps["mail_meeting_share"](_ctx({**REFS, missing: ""}))
    assert not e.value.retryable
    assert REFS["token"] not in str(e.value)
    assert ch.sent == []
