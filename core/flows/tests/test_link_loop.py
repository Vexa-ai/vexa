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
import pytest
from flows import Done, Reaction, Registry, StepCtx, StepError


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


class FakeScaffolds:
    """agent-api's `POST /internal/scaffolds`, recorded — the record a step mints, and the url it
    gets back.

    THE URL IT RETURNS IS AN ID AND NOTHING ELSE, which is what the real route returns and what
    every assertion below now reads. Before the scaffold, a test could read the preset name and the
    meeting off the query string, because they were IN the link — and so could anyone who received
    the mail, which is why a link may not carry prompt text and why both renderers behind it were
    free to compose their own halves. The preset now lives in the RECORD, so the tests assert on
    the record."""

    def __init__(self, fail=None):
        self.minted: list[dict] = []
        self._fail = fail          # (address) -> Exception | None

    def __call__(self, kind, recipient, *, opening, meeting_id=None, refs=None, workspaces=None,
                 tabs=None, focus=None, share_token=None, provenance=None):
        if self._fail is not None:
            err = self._fail(recipient)
            if err is not None:
                raise err
        self.minted.append({"kind": kind, "who": recipient, "opening": opening,
                            "meeting": None if meeting_id is None else str(meeting_id),
                            "refs": refs or {}, "share_token": share_token,
                            "provenance": provenance or {}})
        # THE LINK IS AN ID (R-A08). The share the step minted is stored ON the record and its
        # recipient redeems it against this id; agent-api stopped composing `&tshare=` into the url,
        # so a fake that still did would be this suite pinning a contract that no longer exists.
        return f"https://app.example.test/?s=sc{len(self.minted)}"

    def for_(self, address: str) -> dict:
        return next(m for m in self.minted if m["who"] == address)


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


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None) -> StepCtx:
    # `scratch` is passed in when a test replays a step the way the engine does: the real scratch is
    # persisted after every step, so a retry sees what the failed attempt already recorded.
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {},
                   clock_now=1_700_000_000.0, scratch=scratch if scratch is not None else {})


def _params(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    """Every production touch mints a scaffold before it sends; this stands in for agent-api."""
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


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
def test_email_minutes_carries_the_composed_review_link(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "ws_file", lambda uid, path: "## Decided\n- ship it\n")
    out = reg.steps["email_minutes"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 41},
        {"process_meeting": {"report": "## Decided\n- ship it", "group": ""}}))
    assert isinstance(out, Done)
    assert len(ch.sent) == 1
    msg = ch.sent[0]
    assert msg["to"] == "anna@bank.test"
    # THE LINK IS AN ID. The preset and the meeting live in the RECORD, not in the query string.
    assert set(_params(msg["link"])) == {"s"}
    rec = scaffolds.for_("anna@bank.test")
    assert (rec["kind"], rec["opening"], rec["meeting"]) == ("post-meeting", "minutes-review", "41")
    assert rec["share_token"] is None                     # the organiser owns this meeting
    assert rec["provenance"]["minted_by"] == "7"          # who can read the row to resolve the phase
    assert "## Decided" in msg["body"]                   # the note still travels VERBATIM
    assert msg["link"] not in msg["body"]                # the channel appends it, not the step


def test_email_minutes_still_obeys_the_person_switch(monkeypatch):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: False)
    out = reg.steps["email_minutes"](_ctx({"uid": "7", "organizer": "a@b.test", "title": "T",
                                           "meeting_id": 41}))
    assert isinstance(out, Done) and out.result.get("skipped")
    assert ch.sent == []


def test_prepare_meeting_sends_five_plain_lines_and_the_prep_link(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    out = reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync",
         "meeting_id": 41, "start": 1_700_003_600.0}))
    assert isinstance(out, Done)
    msg = ch.sent[0]
    assert set(_params(msg["link"])) == {"s"}
    rec = scaffolds.for_("anna@bank.test")
    assert (rec["kind"], rec["opening"], rec["meeting"]) == ("prep", "prep", "41")
    # THE FACTS THE INVITE ALREADY KNEW ride the record — the missing half of the prepare opening
    # that named a meeting by its Zoom id and then said it held nothing.
    assert rec["refs"]["title"] == "Pilot sync" and rec["refs"]["when"] == 1_700_003_600.0
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


def test_prepare_meeting_resolves_the_row_id_from_the_url(monkeypatch, scaffolds):
    """No meeting_id in refs — the step asks the platform which row this url is, because the
    terminal deeplink names a ROW, and an invite carries only the meeting url."""
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    monkeypatch.setattr(production.mt, "meeting_ref", lambda uid, url: "41")
    reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "a@b.test", "title": "T", "start": 1_700_003_600.0,
         "url": "https://meet.google.com/abc-defg-hij"}))
    assert set(_params(ch.sent[0]["link"])) == {"s"}
    assert scaffolds.for_("a@b.test")["meeting"] == "41"


# ── the recipes no longer name a transport ───────────────────────────────────────────────────
def test_the_prep_fact_is_emitted_inside_invite_intake():
    """meeting.upcoming has no producer of its own on this deployment: the invite IS the
    meeting-created event, so invite_intake emits the fact before it parks on await_start."""
    reg, _ch = _rig()
    assert reg.get("invite_intake", 2).steps.index("emit_prep") < \
        reg.get("invite_intake", 2).steps.index("await_start")
    # DECISION 29: three touches and no others — RSVP accept, the ack mail, the prepare mail.
    steps = reg.get("invite_intake", 2).steps
    assert "spawn_onboardings" not in steps
    assert ("onboard_person", 1) not in reg.flows and ("onboard_group", 1) not in reg.flows
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


# ── the attendee gate: a touch that cannot work is not sent ──────────────────────────────────
# 2026-09-02, meeting 97 ("DNA TSC — 3 August"). The row was planned from an invite whose url
# matched no platform, so it landed platform='unknown' with an empty native; the mint was
# addressed by that pair, answered 404, and `mint_transcript_share` returned None rather than
# raising — so the `except Exception` guarding it never fired. The mail went to every attendee
# with no `tshare` token, and every one of them clicked into a chat that answered "no meeting
# with id 97 on my side". The gate below is the fix: no capability, no mail.
ATTENDEE_REFS = {
    "uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
    "participants": ["anna@bank.test", "ben@bank.test", "cara@bank.test", "out@other.test"],
}
ATTENDEE_PRIOR = {"process_meeting": {"report": "## Decided\n- ship it", "group": ""}}


def _attendee_rig(monkeypatch, *, mint, row=None):
    reg, ch = _rig()
    monkeypatch.setattr(production, "ws_file", lambda uid, path: "## Decided\n- ship it\n")
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, mid, native=None: row if row is not None
                        else {"id": 97, "platform": "unknown", "native_meeting_id": ""})
    monkeypatch.setattr(production.mt, "mint_transcript_share", mint)
    return reg, ch


def test_a_minted_share_travels_in_the_link(monkeypatch, scaffolds):
    """The happy path, asserted on the artifact the attendee actually receives: one token per
    attendee, restricted to them, and carried ON THE RECORD the button's id names (R-A08)."""
    minted = []

    def mint(uid, meeting_id, email, expires_in_sec=30 * 86400):
        minted.append((uid, meeting_id, email))
        return f"97.secret-for-{email}"

    reg, ch = _attendee_rig(monkeypatch, mint=mint)
    out = reg.steps["email_attendees"](_ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR))

    assert isinstance(out, Done) and out.result["sent"] == 2      # ben + cara; out@other.test is outside
    # minted BY ROW ID (97), not by the (platform, native) pair the row cannot supply
    assert [m[1] for m in minted] == [97, 97]
    assert [m[2] for m in minted] == ["ben@bank.test", "cara@bank.test"]
    for msg in ch.sent:
        assert set(_params(msg["link"])) == {"s"}                 # an id, and nothing that is a credential
        rec = scaffolds.for_(msg["to"])
        assert rec["meeting"] == "97"
        assert rec["share_token"] == f"97.secret-for-{msg['to']}"  # this attendee's OWN capability
        # the attendee mail carries the SECOND ASK, so the record says which kind of touch it is
        assert (rec["kind"], rec["opening"]) == ("invite-offer", "minutes-review-invite")


def test_the_fan_out_is_HELD_when_a_share_cannot_be_minted(monkeypatch):
    """The regression. A 404 from the mint must stop the send, not degrade the link."""
    def mint(uid, meeting_id, email, expires_in_sec=30 * 86400):
        raise production.mt.ShareMintError(
            meeting_id=meeting_id, identity=email, status=404,
            detail="Meeting not found for unknown/96088138284", retryable=False)

    reg, ch = _attendee_rig(monkeypatch, mint=mint)
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](_ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR))

    assert ch.sent == []                                  # NOTHING went out
    reason = str(e.value)
    assert "404" in reason                                # the status
    assert "96088138284" in reason                        # the response detail, not just its class
    assert "97" in reason                                 # which meeting
    assert "ben@bank.test" in reason                      # which identity it tried to mint by
    assert "Mailed: nobody" in reason                     # who was mailed
    assert "cara@bank.test" in reason                     # ...and who was not
    assert e.value.retryable is False                     # a 404 does not fix itself


def test_a_PARTIAL_fan_out_reports_who_was_mailed_and_does_not_resend(monkeypatch):
    """The first attendee mints and is mailed; the second cannot. The step still fails — but it
    names both halves, and the durable scratch means a retry does not mail the first one twice."""
    def mint(uid, meeting_id, email, expires_in_sec=30 * 86400):
        if email == "ben@bank.test":
            return "97.ok"
        raise production.mt.ShareMintError(meeting_id=meeting_id, identity=email, status=403,
                                           detail="Invalid API key", retryable=False)

    reg, ch = _attendee_rig(monkeypatch, mint=mint)
    ctx = _ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR)
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](ctx)

    assert [m["to"] for m in ch.sent] == ["ben@bank.test"]
    assert "Mailed: ben@bank.test" in str(e.value)
    assert "NOT mailed: cara@bank.test" in str(e.value)
    assert ctx.scratch["sent"] == ["ben@bank.test"]       # durable across the retry

    # the retry: ben is skipped, cara is attempted again
    tried = []

    def mint2(uid, meeting_id, email, expires_in_sec=30 * 86400):
        tried.append(email)
        return "97.ok2"

    monkeypatch.setattr(production.mt, "mint_transcript_share", mint2)
    out = reg.steps["email_attendees"](_ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR, scratch=ctx.scratch))
    assert tried == ["cara@bank.test"]                    # ben is NOT minted or mailed twice
    assert [m["to"] for m in ch.sent] == ["ben@bank.test", "cara@bank.test"]
    assert isinstance(out, Done) and out.result["sent"] == 2


def test_the_fan_out_is_HELD_when_a_SCAFFOLD_cannot_be_minted(monkeypatch):
    """The share gate's twin. There are two ways to send a button that opens onto nothing — no
    capability, and no record — and both take the same branch: nothing goes out, and the reason
    names who was mailed and who was not."""
    fake = FakeScaffolds(fail=lambda who: StepError(
        f"no scaffold could be minted for {who}: HTTP 400 — preset asks/minutes-review-invite.md "
        "is empty", retryable=False) if who == "ben@bank.test" else None)
    monkeypatch.setattr(production, "mint_scaffold", fake)
    reg, ch = _attendee_rig(monkeypatch,
                            mint=lambda uid, mid, email, expires_in_sec=30 * 86400: "97.ok")
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](_ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR))
    assert ch.sent == []
    reason = str(e.value)
    assert "HELD the attendee fan-out" in reason and "ben@bank.test" in reason
    assert "Mailed: nobody" in reason and "NOT mailed" in reason
    assert e.value.retryable is False


def test_a_meeting_with_no_row_id_never_reaches_the_mint(monkeypatch):
    """A ref that is a NATIVE id (meeting_ref degrades to one when no row exists) cannot be minted
    against, and the step says so instead of mailing a link to a meeting nobody can open."""
    def mint(uid, meeting_id, email, expires_in_sec=30 * 86400):
        raise AssertionError("must not be reached")

    reg, ch = _attendee_rig(monkeypatch, mint=mint, row={})
    refs = dict(ATTENDEE_REFS, meeting_id="abc-defg-hij")
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](_ctx(refs, ATTENDEE_PRIOR))
    assert ch.sent == []
    assert "no row id" in str(e.value)
    assert e.value.retryable is False


def test_a_5xx_mint_is_retryable_but_still_sends_nothing(monkeypatch):
    """The platform having a moment is a different fact from a meeting that cannot be addressed —
    but neither is a reason to send a broken link."""
    def mint(uid, meeting_id, email, expires_in_sec=30 * 86400):
        raise production.mt.ShareMintError(meeting_id=meeting_id, identity=email, status=503,
                                           detail="upstream unreachable", retryable=True)

    reg, ch = _attendee_rig(monkeypatch, mint=mint)
    with pytest.raises(StepError) as e:
        reg.steps["email_attendees"](_ctx(dict(ATTENDEE_REFS), ATTENDEE_PRIOR))
    assert ch.sent == [] and e.value.retryable is True


# ── mint_scaffold: the wire, and the refusal ─────────────────────────────────────────────────
def _mint_rig(monkeypatch, status, body):
    """`common.mint_scaffold` over a recorded HTTP call — no network, no agent-api."""
    calls = []

    def fake_http(method, url, headers, payload=None, timeout=20):
        calls.append({"method": method, "url": url, "headers": headers, "body": payload})
        return status, body

    monkeypatch.setenv("VEXA_INTERNAL_SECRET", "internal-tier-secret-for-tests")
    monkeypatch.setattr(common, "http", fake_http)
    return calls


def test_mint_scaffold_posts_the_record_and_returns_the_url(monkeypatch):
    calls = _mint_rig(monkeypatch, 201, {"id": "abc", "url": "https://app.test/?s=abc"})
    url = common.mint_scaffold("prep", "anna@bank.test", opening="prep", meeting_id=41,
                               refs={"title": "Pilot sync"},
                               provenance={"flow": "meeting_prep", "minted_by": "7"})
    assert url == "https://app.test/?s=abc"
    call = calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/internal/scaffolds")
    # THE INTERNAL TIER. A browser client through the gateway holds no such secret and therefore
    # cannot mint a scaffold for anybody — the same gate the meeting room uses.
    assert call["headers"]["X-Internal-Secret"] == "internal-tier-secret-for-tests"
    assert call["body"] == {"who": "anna@bank.test", "kind": "prep", "opening": "prep",
                            "meeting": "41", "refs": {"title": "Pilot sync"},
                            "provenance": {"flow": "meeting_prep", "minted_by": "7"}}
    # THE RECORD CARRIES A PRESET NAME, NEVER TEXT — the whole reason the URL never carried one.
    assert "\n" not in call["body"]["opening"] and " " not in call["body"]["opening"]


def test_a_4xx_mint_is_a_fact_about_this_touch_and_is_not_retried(monkeypatch):
    _mint_rig(monkeypatch, 400, {"detail": "preset asks/nope.md is empty"})
    with pytest.raises(StepError) as e:
        common.mint_scaffold("prep", "a@b.test", opening="nope", meeting_id=41)
    assert "nope" in str(e.value) and "a@b.test" in str(e.value)
    assert "worse than no mail" in str(e.value)
    assert e.value.retryable is False


def test_a_5xx_mint_is_retryable(monkeypatch):
    _mint_rig(monkeypatch, 503, {"detail": "VEXA_UI_URL is not set on agent-api"})
    with pytest.raises(StepError) as e:
        common.mint_scaffold("prep", "a@b.test", opening="prep")
    assert e.value.retryable is True


def test_a_2xx_carrying_no_url_fails_like_any_other_mint(monkeypatch):
    """The `mint_transcript_share` lesson, applied one layer up: a caller asked for a link and did
    not get one, and the reason it did not is worth the same noise as a 404."""
    _mint_rig(monkeypatch, 201, {"id": "abc"})
    with pytest.raises(StepError):
        common.mint_scaffold("prep", "a@b.test", opening="prep")


# ── the mint itself: no non-2xx is ever swallowed ────────────────────────────────────────────
def test_mint_returns_the_token_and_asks_by_row_id(monkeypatch):
    import flows_steps.meeting as mt

    calls = []
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")
    monkeypatch.setattr(mt, "http", lambda m, url, h, b=None, **kw:
                        (calls.append((m, url, b)) or (200, {"token": "97.tok", "id": "g1"})))
    assert mt.mint_transcript_share("7", 97, "ben@bank.test") == "97.tok"
    method, url, body = calls[0]
    assert method == "POST" and url.endswith("/meetings/97/share")
    assert body["mode"] == "restricted" and body["allowed_emails"] == ["ben@bank.test"]


@pytest.mark.parametrize("status,body,retryable", [
    (404, {"detail": "Meeting 97 not found"}, False),
    (403, {"detail": "Invalid API key"}, False),
    (429, {"detail": "Rate limit exceeded"}, True),
    (503, {"detail": "upstream unreachable"}, True),
    (200, {"id": "g1"}, False),          # 2xx with NO token is a failure too
])
def test_mint_never_swallows_a_non_token_answer(monkeypatch, status, body, retryable):
    """The exact defect: this used to `return None` on every one of these, so the caller's
    `except Exception` never fired and the mail shipped without a capability."""
    import flows_steps.meeting as mt

    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")
    monkeypatch.setattr(mt, "http", lambda *a, **k: (status, body))
    with pytest.raises(mt.ShareMintError) as e:
        mt.mint_transcript_share("7", 97, "ben@bank.test")
    assert e.value.status == status
    assert str(body.get("detail", body))[:40] in str(e.value)   # the response body survives
    assert e.value.retryable is retryable
    assert isinstance(e.value, StepError)        # an uncaught mint failure still fails its step
