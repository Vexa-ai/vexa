"""F220 — `email_minutes` indexed `ctx.refs["title"]` UNCONDITIONALLY and raised
`KeyError('title')` on the agent-PRESENT ad hoc path. F212's sibling, one ref key over: an ad hoc
bot (no calendar event) reaches `meeting.completed` with `meeting_api.events.
meeting_completed_refs`'s real shape — `{uid, meeting_id, native, platform, completion_reason}`,
never `title` — so `process_meeting` writes the minutes and the very next step, the one mail that
always sends, died mailing them.

Every OTHER mail-shaped step in `post_meeting` already guessed `ctx.refs.get("title") or "your
meeting"` at its own call site — four independent spellings of one fallback, and a fifth crash
waiting in whichever step wrote a fifth. `_mail_title` (`flows_defs/production.py`, module-level,
beside `_organizer_address`) is the one resolver all of them route through now:

    refs["title"]
      -> the meeting row's own `data.title`, if a later annotation gave it one (`mt.meeting_row`)
      -> "{Platform} meeting", from `refs.platform` alone (skipped when platform is missing or
         the literal placeholder "unknown")
      -> "Meeting on <date>", the honest last resort

No network, no clock, no DB beyond what's monkeypatched — same rig `test_post_meeting_adhoc_mail.py`
and `test_link_loop.py` already use.
"""
from __future__ import annotations

import sys
from pathlib import Path

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import pytest
from flows import Done

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_link_loop import FakeScaffolds, _ctx, _rig  # noqa: E402
from flows_steps import common


@pytest.fixture(autouse=True)
def _person_settings_are_declared(monkeypatch):
    """THIS TEST PROCESS HAS AN IDENTITY DOMAIN, the way `conftest` gives it an admin key — and for
    the same reason, one door along.

    `person_settings` used to answer the DEFAULTS on any failure, so every test in this file that
    reached `setting()` without saying so got them from a swallowed exception: there is no identity
    service here, `require_internal_secret` refuses, and the broad `except` turned that into "this
    person prefers the defaults". 0.12.27 makes an unreachable identity RETRYABLE instead
    (`SettingsUnavailable`), so a test standing on that swallow now fails — correctly. The suite
    declares what a deployment declares. Tests that are ABOUT a preference still set their own;
    this is the baseline underneath them."""
    common.forget_person_settings()
    monkeypatch.setattr(common, "person_settings", lambda uid: dict(common._SETTING_DEFAULTS))



@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    """Every production touch mints a scaffold before it sends; this stands in for agent-api."""
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_admin_mail_override(monkeypatch):
    """The live mail-head reader is stubbed — see `test_link_loop`'s identical fixture for why a
    test that skips this can pass for the wrong reason (a neighbouring stack's 404)."""
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)


# `meeting_completed_refs`'s REAL shape (`meeting_api.events`) for a bot dispatched with no
# calendar invite in sight — no `title`, ever. This is F220's whole premise, and the gap
# `test_post_meeting_adhoc_mail.py`'s `ADHOC_REFS` left: that fixture always carried a title.
ADHOC_NO_TITLE_REFS = {"uid": "7", "meeting_id": 41, "native": "abc123", "platform": "zoom"}
PRIOR = {"process_meeting": {"report": "## Decided\n- ship it", "group": ""}}


def _no_row(uid, meeting_id, native=None):
    return None


# ── (1) RED: no title anywhere on the ref → email_minutes must not raise KeyError('title') ──────
def test_email_minutes_sends_with_a_derived_subject_when_the_refs_carry_no_title(
        monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: "dispatcher@bank.test" if uid == "7" else "")
    monkeypatch.setattr(production.mt, "meeting_row", _no_row)
    out = reg.steps["email_minutes"](_ctx(dict(ADHOC_NO_TITLE_REFS), PRIOR))
    assert isinstance(out, Done)
    assert len(ch.sent) == 1
    subject = ch.sent[0]["subject"]
    assert subject == "Minutes: Zoom meeting"          # tier 3: platform, no row title
    assert "## Decided" in ch.sent[0]["body"]           # the note still travels VERBATIM


# ── (2) refs carry a title → email_minutes' mail is unchanged ───────────────────────────────────
def test_email_minutes_keeps_the_refs_title_unchanged_when_one_is_present(monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: "dispatcher@bank.test" if uid == "7" else "")
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 41})
    refs = dict(ADHOC_NO_TITLE_REFS, title="Ad hoc sync")
    out = reg.steps["email_minutes"](_ctx(refs, PRIOR))
    assert isinstance(out, Done)
    assert ch.sent[0]["subject"] == "Minutes: Ad hoc sync"


# ── (2b) `_mail_title` itself never consults the row when the ref already answers ───────────────
# (isolates the resolver's own guess from `email_minutes`' UNRELATED row lookup for the scaffold's
# `row_id` — that one runs regardless of title and would make the check above a false negative.)
def test_mail_title_never_calls_the_row_lookup_when_the_ref_already_has_a_title(monkeypatch):
    looked_up = []
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, m, native=None: looked_up.append(1) or None)
    ctx = _ctx(dict(ADHOC_NO_TITLE_REFS, title="Ad hoc sync"))
    assert production._mail_title(ctx) == "Ad hoc sync"
    assert looked_up == []


# ── (3) the meeting row was annotated with a title after the fact → that wins over the platform ─
def test_mail_title_prefers_the_meeting_rows_own_title_over_the_platform_guess(monkeypatch):
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, m, native=None: {"data": {"title": "Renamed by Alice"}})
    ctx = _ctx(dict(ADHOC_NO_TITLE_REFS))
    assert production._mail_title(ctx) == "Renamed by Alice"


# ── (4) no title, no row, no usable platform → the honest "Meeting on <date>" last resort ───────
def test_mail_title_falls_back_to_a_dated_title_when_the_platform_is_unknown(monkeypatch):
    monkeypatch.setattr(production.mt, "meeting_row", _no_row)
    refs = {"uid": "7", "meeting_id": 41, "native": "", "platform": "unknown",
            "start": 1_700_000_000.0}
    ctx = _ctx(refs)
    title = production._mail_title(ctx)
    assert title.startswith("Meeting on ")
    assert title != "Meeting on your meeting"           # never composes over the old placeholder


# ── (5) never "your meeting" — that string was every OTHER step's own guess, not a resolved one ─
def test_mail_title_never_returns_your_meeting(monkeypatch):
    monkeypatch.setattr(production.mt, "meeting_row", _no_row)
    ctx = _ctx({"uid": "7", "meeting_id": 41, "native": "", "platform": ""})
    assert production._mail_title(ctx) != "your meeting"


# ── (6) stability: every mail-shaped step in ONE reaction agrees on the SAME resolved title ─────
def test_the_derived_title_is_stable_across_every_mail_shaped_step_in_one_reaction(
        monkeypatch, scaffolds):
    reg, ch = _rig()
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production, "address_for_uid",
                        lambda uid: "dispatcher@bank.test" if uid == "7" else "")
    monkeypatch.setattr(production, "ensure_platform_user", lambda email: "99")
    monkeypatch.setattr(production, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(production.ag, "workspace_init", lambda *_a, **_k: None)
    written = []
    monkeypatch.setattr(production.ag, "workspace_write",
                        lambda uid, path, content: written.append((uid, path, content)))
    monkeypatch.setattr(production.mt, "meeting_row", _no_row)

    scratch: dict = {}
    refs = dict(ADHOC_NO_TITLE_REFS)
    out_minutes = reg.steps["email_minutes"](_ctx(refs, PRIOR, scratch))
    out_attendees = reg.steps["email_attendees"](_ctx(refs, PRIOR, scratch))
    out_drop = reg.steps["drop_to_attendees"](
        _ctx(refs, {**PRIOR, "email_attendees": out_attendees.result}, scratch))

    assert isinstance(out_minutes, Done)
    assert isinstance(out_drop, Done)
    assert ch.sent[0]["subject"] == "Minutes: Zoom meeting"
    # `drop_to_attendees` filed the KG entity under the SAME resolved title `email_minutes` mailed
    # — not a second, independently-rolled guess that could (and, before this fix, effectively
    # did across the four call sites) disagree with it.
    entity_writes = [c for (_uid, path, c) in written if path.startswith("kg/entities/meeting/")
                     and not path.endswith("index.md")]
    assert entity_writes and all('title: "Zoom meeting"' in c for c in entity_writes)
