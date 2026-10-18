"""IN-APP ECHOES OF MEETING FACTS (#1783) — flows tells the person's bell, agent-api writes it.

The founder's collaboration path: a meeting shared with you, a bot set to join your calendar
meeting, the bot going live in it — each also lands in the app as a notification with one action,
Open. flows produces these facts; agent-api is the notification store's one writer, so flows only
names the fact on agent-api's internal tier (`POST /internal/notifications`). A publish, never a
dependency: no agent domain, or a refusal, and the mail still goes and the bot still joins.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_defs.production as production  # noqa: E402
import flows_steps.common as common  # noqa: E402
from flows import Done, Reaction, Registry, StepCtx, Wait  # noqa: E402

from test_meeting_share_mail import MINTED, REFS, UI, _ctx, rig  # noqa: E402,F401
from test_link_loop import _StubDB  # noqa: E402

AGENT = "http://agent-api.test:8100"


@pytest.fixture()
def wire(monkeypatch):
    calls = []
    real = common.http

    def http(method, url, headers, body=None, timeout=20):
        if url.startswith(AGENT):
            calls.append((method, url, headers, body))
            return 200, {"delivered": 1}
        return real(method, url, headers, body, timeout)

    monkeypatch.setattr(common, "AGENT_API", AGENT)
    monkeypatch.setattr(common, "http", http)
    monkeypatch.setenv("INTERNAL_API_SECRET", "s3cret-internal-tier-value-0001")
    return calls


def test_a_shared_meeting_reaches_the_recipients_bell_with_the_same_link_as_the_mail(rig, wire):
    reg, ch = rig
    out = reg.steps["mail_meeting_share"](_ctx(dict(REFS)))
    assert isinstance(out, Done) and ch.sent
    [(method, url, headers, body)] = wire
    assert (method, url) == ("POST", f"{AGENT}/internal/notifications")
    assert headers["X-Internal-Secret"] == "s3cret-internal-tier-value-0001"
    assert body["kind"] == "meeting_shared" and body["email"] == "jsmith@example.com"
    assert body["meeting_id"] == "42" and body["title"] == "Weekly sync" and body["actor"] == "7"
    # Open goes where the mail's button goes — a path in the app, never another origin
    assert body["url"] == f"/?tshare={MINTED}" and UI not in body["url"]


def test_no_agent_domain_still_mails_and_tells_no_bell(rig, wire, monkeypatch):
    reg, ch = rig
    monkeypatch.setattr(common, "AGENT_API", "")
    assert isinstance(reg.steps["mail_meeting_share"](_ctx(dict(REFS))), Done) and ch.sent
    assert wire == []


def test_a_refused_echo_never_fails_the_mail(rig, monkeypatch):
    reg, ch = rig
    monkeypatch.setattr(common, "AGENT_API", AGENT)
    monkeypatch.setattr(common, "http", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert isinstance(reg.steps["mail_meeting_share"](_ctx(dict(REFS))), Done) and ch.sent


def _live_ctx(scratch):
    refs = {"uid": "7", "meeting_id": "43", "native": "abc-defg-hij", "platform": "google_meet",
            "title": "Design review"}
    r = Reaction("rid", "live-43", "meeting.started", refs, "live_meeting", 1, "attend_live", "running",
                 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:attend_live", prior={}, clock_now=1_700_000_000.0,
                   scratch=scratch)


def test_the_bot_going_live_tells_the_owner_once(wire, monkeypatch):
    reg = Registry()
    production.build(reg, _StubDB())
    scratch: dict = {}
    assert isinstance(reg.steps["attend_live"](_live_ctx(scratch)), Wait)
    assert isinstance(reg.steps["attend_live"](_live_ctx(scratch)), Wait)
    [(_, _, _, body)] = wire
    assert body == {"kind": "bot_joined", "to": "7", "meeting_id": "43", "title": "Design review"}


def test_a_calendar_invite_tells_the_owner_the_bot_will_join(wire):
    reg = Registry()
    production.build(reg, _StubDB())
    refs = {"ics_uid": "evt-1@cal", "title": "Board prep", "start": 1_700_000_600.0}
    r = Reaction("rid", "inv-1", "invite.received", refs, "invite_intake", 3, "emit_prep", "running",
                 1, 0.0, None, None, None)
    emitted = []
    ctx = StepCtx(reaction=r, effect_key="rid:emit_prep", prior={"ensure_user": {"uid": "7"}},
                  clock_now=1_700_000_000.0, scratch={})
    ctx.emit = lambda *a, **k: emitted.append(a)
    assert isinstance(reg.steps["emit_prep"](ctx), Done) and emitted
    [(_, _, _, body)] = wire
    assert body == {"kind": "bot_scheduled", "to": "7", "meeting_id": "evt-1@cal", "title": "Board prep",
                    "object_kind": "none"}
