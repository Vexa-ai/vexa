"""The link loop: a flow-sent notification carries a COMPOSED terminal deeplink.

Two properties, and they are different. (1) The recipes no longer name a transport — every send
goes through the notify PORT, so a fake channel installed here sees all of them and no SMTP
connection is attempted. (2) The two meeting mails carry exactly one link each, built from
VEXA_UI_URL, naming the preset and the meeting: `?ask=minutes-review&meeting=<row-id>` after,
`?ask=prep&meeting=<ref>` before.

No network, no clock, no DB — the steps are called directly with the refs their flow would have
handed them, which is also the only way to exercise prepare_meeting without a real invite."""
from __future__ import annotations

import importlib
import os
from urllib.parse import parse_qs, urlparse

import flows_defs.production as production
import flows_steps.common as common
import flows_steps.notify as notify_mod
from flows import Done, Reaction, Registry, StepCtx


class FakeChannel:
    """Records instead of sending. `link` arrives SEPARATELY from `body` — the port's whole
    point — so the assertions can read the call to action without parsing prose."""

    name = "fake"

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, *, link=None, in_reply_to=None):
        self.sent.append({"to": to, "subject": subject, "body": body,
                          "link": link, "in_reply_to": in_reply_to})
        return f"<fake-{len(self.sent)}@test>"


class _StubDB:
    """production.build() only ever calls execute(); nothing here asserts on storage."""

    def execute(self, *_a, **_k):
        return []


def _rig():
    reg = Registry()
    production.build(reg, _StubDB())
    ch = FakeChannel()
    notify_mod.use(ch)
    return reg, ch


def _ctx(refs: dict, prior: dict | None = None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {},
                   clock_now=1_700_000_000.0, scratch={})


def _params(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


def teardown_function():
    notify_mod.use(None)                      # never leak a fake channel into a later test


# ── the port ─────────────────────────────────────────────────────────────────────────────────
def test_compose_puts_the_link_last_and_alone():
    assert notify_mod.compose("Body.", "https://x/y") == "Body.\n\nhttps://x/y\n"
    assert notify_mod.compose("Body.", None) == "Body.\n"
    # a body that already carries the link is not given it twice
    assert notify_mod.compose("Body https://x/y", "https://x/y") == "Body https://x/y\n"


def test_channel_is_env_selected_and_refuses_what_it_cannot_do():
    notify_mod.use(None)
    os.environ["VEXA_NOTIFY_CHANNEL"] = "teams"
    try:
        notify_mod.notify("a@b.test", "s", "b")
    except ValueError as e:
        assert "teams" in str(e)
    else:
        raise AssertionError("an unimplemented channel must refuse, never fall back to smtp")
    finally:
        os.environ.pop("VEXA_NOTIFY_CHANNEL", None)
        notify_mod.use(None)
    assert notify_mod.channel().name == "smtp"          # the default is still the real one


def test_ui_url_comes_from_the_environment():
    os.environ["VEXA_UI_URL"] = "https://app.example.test/"
    try:
        reloaded = importlib.reload(common)
        assert reloaded.UI_URL == "https://app.example.test"          # trailing slash normalised
        assert reloaded.ui_link(ask="prep", meeting=7) == \
            "https://app.example.test/?ask=prep&meeting=7"
        assert reloaded.ui_link(ask="prep", meeting="") == "https://app.example.test/?ask=prep"
    finally:
        os.environ.pop("VEXA_UI_URL", None)
        importlib.reload(common)
        importlib.reload(production)


# ── the two meeting mails ────────────────────────────────────────────────────────────────────
def test_email_minutes_carries_the_composed_review_link(monkeypatch):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "ws_file", lambda uid, path: "## Decided\n- ship it\n")
    out = reg.steps["email_minutes"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 41},
        {"process_meeting": {"note_path": "kg/entities/meeting/x.md", "summary": "s", "sha": "abc123def"}}))
    assert isinstance(out, Done)
    assert len(ch.sent) == 1
    msg = ch.sent[0]
    assert msg["to"] == "anna@bank.test"
    assert _params(msg["link"]) == {"ask": "minutes-review", "meeting": "41"}
    assert "## Decided" in msg["body"]                   # the note still travels VERBATIM
    assert msg["link"] not in msg["body"]                # the channel appends it, not the step


def test_email_minutes_still_obeys_the_person_switch(monkeypatch):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: False)
    out = reg.steps["email_minutes"](_ctx({"uid": "7", "organizer": "a@b.test", "title": "T",
                                           "meeting_id": 41}))
    assert isinstance(out, Done) and out.result.get("skipped")
    assert ch.sent == []


def test_prepare_meeting_sends_five_plain_lines_and_the_prep_link(monkeypatch):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    out = reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync",
         "meeting_id": 41, "start": 1_700_003_600.0}))
    assert isinstance(out, Done)
    msg = ch.sent[0]
    assert _params(msg["link"]) == {"ask": "prep", "meeting": "41"}
    assert msg["subject"] == "Prepare: Pilot sync"
    assert "Pilot sync" in msg["body"]
    # ≤5 lines INCLUDING the link the channel appends — a prepare mail is read in one glance
    assert len(notify_mod.compose(msg["body"], msg["link"]).strip().splitlines()) <= 5


def test_prepare_meeting_obeys_mail_prep(monkeypatch):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: False if key == "mail_prep" else "")
    out = reg.steps["prepare_meeting"](_ctx({"uid": "7", "organizer": "a@b.test", "title": "T",
                                             "meeting_id": 41, "start": 1_700_003_600.0}))
    assert isinstance(out, Done) and out.result.get("skipped")
    assert ch.sent == []


def test_prepare_meeting_resolves_the_row_id_from_the_url(monkeypatch):
    """No meeting_id in refs — the step asks the platform which row this url is, because the
    terminal deeplink names a ROW, and an invite carries only the meeting url."""
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    monkeypatch.setattr(production.mt, "meeting_ref", lambda uid, url: "41")
    reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "a@b.test", "title": "T", "start": 1_700_003_600.0,
         "url": "https://meet.google.com/abc-defg-hij"}))
    assert _params(ch.sent[0]["link"]) == {"ask": "prep", "meeting": "41"}


# ── the recipes no longer name a transport ───────────────────────────────────────────────────
def test_the_prep_fact_is_emitted_inside_invite_intake():
    """meeting.upcoming has no producer of its own on this deployment: the invite IS the
    meeting-created event, so invite_intake emits the fact before it parks on await_start."""
    reg, _ch = _rig()
    assert reg.get("invite_intake", 1).steps.index("emit_prep") < \
        reg.get("invite_intake", 1).steps.index("await_start")
    assert reg.get("meeting_prep", 1).on.name == "meeting.upcoming"
    emitted = []
    ctx = _ctx({"ics_uid": "u-1", "organizer": "a@b.test"},
               {"ensure_user": {"uid": "7"}})
    ctx.emit = lambda et, sid, refs: emitted.append((et, sid, refs)) or 1
    reg.steps["emit_prep"](ctx)
    assert emitted[0][0] == "meeting.upcoming"
    assert emitted[0][2]["uid"] == "7"


def test_no_recipe_calls_the_mail_transport_by_name():
    """The point of the port: `mx.send(` must not appear in flows_defs. emailx survives there
    only for register_thread (DB bookkeeping) and send_rsvp_accept (iMIP, a calendar protocol
    reply — not a notification to a person)."""
    from pathlib import Path
    src = Path(production.__file__).read_text()
    assert "mx.send(" not in src
    assert "notify(" in src
