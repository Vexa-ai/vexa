"""Base path on the fixture corpus: organiser artifact, org-domain chat invites, outsider silence."""
from pathlib import Path

import pytest

from vexa_mailroom.base_path import plan_base_path
from vexa_mailroom.invite import parse_invite

import os
# The corpus lives in the operator's workspace, not this repo; point MAILROOM_ICS_DIR at it.
ICS = Path(os.environ.get("MAILROOM_ICS_DIR", str(Path.home() / "dev/biz/fixtures/mailroom/ics")))

ORG = "example.com"
BOT = "mk-dev@dev.vexa.ai"
SUMMARY = "Decisions: ship Tuesday. Owner: Priya."

pytestmark = pytest.mark.skipif(not ICS.is_dir(), reason="fixture corpus not present")


def _mail(name: str) -> bytes:
    ics = (ICS / name).read_bytes()
    return (
        b"MIME-Version: 1.0\r\nMessage-ID: <t@test>\r\nFrom: a@example.com\r\n"
        b"To: " + BOT.encode() + b"\r\nSubject: invite\r\n"
        b"Content-Type: text/calendar; method=REQUEST\r\n\r\n" + ics
    )


def _plan(name: str, **kw):
    parsed = parse_invite(_mail(name))
    assert parsed.ok, parsed.rejection
    return plan_base_path(parsed, org_domain=ORG, assistant=BOT,
                          transcript_summary=SUMMARY, **kw)


def test_create_single_fans_out_to_org_only():
    r = _plan("gcal-create-single.ics")
    kinds = {(p.kind, p.to) for p in r.sends}
    assert ("artifact", "ana.silva@example.com") in kinds
    invites = {t for k, t in kinds if k == "chat_invite"}
    assert invites == {"priya.raman@example.com", "tomas.oliveira@example.com",
                       "anna.weber@example.com"}
    assert all(p.to != BOT for p in r.sends)


def test_outsider_gets_nothing_and_is_logged():
    r = _plan("gcal-create-single-outsider.ics")
    assert all(p.to != "jordan.lee@partner-firm.example" for p in r.sends)
    assert any(e["decision"] == "suppress" and e["to"] == "jordan.lee@partner-firm.example"
               and e["reason"] == "outside org domain" for e in r.log)


def test_hold_for_creator_mails_organiser_alone():
    r = _plan("gcal-create-single.ics", verdict="hold_for_creator")
    assert [p.kind for p in r.sends] == ["artifact"]
    assert sum(1 for e in r.log if e["decision"] == "suppress") >= 3


def test_suppress_mails_nobody_loudly():
    r = _plan("gcal-create-single.ics", verdict="suppress")
    assert r.sends == () and r.log


def test_rejected_invite_plans_nothing():
    parsed = parse_invite(_mail("neg-tzless-dtstart.ics"))
    assert not parsed.ok
    r = plan_base_path(parsed, org_domain=ORG, assistant=BOT, transcript_summary=SUMMARY)
    assert r.sends == ()
    assert "rejected invite" in r.log[0]["reason"]


def test_artifact_carries_assign_affordance():
    r = _plan("gcal-create-single.ics")
    art = next(p for p in r.sends if p.kind == "artifact")
    assert "/assign?uid=" in art.body and SUMMARY in art.body
