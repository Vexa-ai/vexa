"""gate:compose · wizard STT flow (#502 C1 / PR #504) — the fresh-install autonomous proof.

RED→GREEN on one stack, one user, one native_meeting_id:

  RED   (negative control): the stack boots with NO TRANSCRIPTION_SERVICE_URL env. A default
        (transcription-on) POST /bots must be refused 503 with the typed reason — AND must leave
        NO meeting row behind (the review's finding 1: the gate fires BEFORE the DB write, so a
        refused spawn cannot orphan a `requested` row whose retry would 409 on the dedup guard).

  GREEN: configure STT through the SAME endpoint the wizard's Settings→Models surface calls —
        the terminal proxies PUT /api/admin/settings/transcription verbatim to admin-api's
        `PUT /internal/settings/transcription` (clients/terminal/src/app/api/admin/settings/
        [key]/route.ts) — then retry the IDENTICAL POST /bots: the spawn is accepted (201).
        Presence, not liveness, is what the resolver gates on (bot-context returns the stored
        url; `request_bot` raises only when transcribe_enabled and no url resolved), so a stub
        URL on the compose network is sufficient and honest.

The retry reusing the SAME native_meeting_id is deliberate: it is the exact user journey the
orphan-row bug broke (503 → fix config → retry → 409). Cleanup restores the platform setting and
stops the bot so the shared stack is left as found.
"""
from __future__ import annotations

import json
import uuid

import pytest

from conftest import http, post_json, requires_docker
from mock_scenarios_test import MOCK_BOT, _meeting, _stop_bot, _wait_meeting
from stack_test import _create_user

pytestmark = requires_docker


def _put_transcription_setting(stack, payload: dict):
    """The wizard's write path: terminal's /api/admin/settings/transcription is a verbatim proxy
    to this internal endpoint (same method, body, and X-Internal-Secret header)."""
    return http(
        "PUT", f"{stack.admin_api}/internal/settings/transcription",
        headers={"X-Internal-Secret": stack.internal_secret, "Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def test_wizard_stt_flow(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id = f"wiz-{uuid.uuid4().hex[:6]}"
    spawn_body = {
        "platform": "google_meet",
        "native_meeting_id": native_id,
        # transcribe_enabled deliberately OMITTED — the default (true) is the fresh-install path.
        "bot_name": "mock:normal" if MOCK_BOT else "wizard-flow-probe",
    }
    headers = {"x-user-id": str(user_id), "x-user-limits": "5"}

    # Baseline: the platform setting must start empty, or the RED leg proves nothing.
    code, body = http(
        "GET", f"{stack.admin_api}/internal/settings/transcription",
        headers={"X-Internal-Secret": stack.internal_secret},
    )
    assert code == 200, f"read transcription setting: {code} {body}"
    if (body.get("value") or {}).get("url"):
        pytest.skip("stack already has a platform transcription backend configured — RED leg impossible")

    try:
        # ── RED: unconfigured → typed 503, and NO orphaned meeting row ─────────────────────────
        code, body = post_json(f"{stack.meeting_api}/bots", spawn_body, headers=headers)
        if code == 201:
            # env-configured stack (TRANSCRIPTION_SERVICE_URL set) — the negative control is
            # impossible here; don't fake a pass.
            _stop_bot(stack, user_id, native_id)
            pytest.skip("stack has env STT configured — RED leg impossible")
        assert code == 503, f"unconfigured spawn must 503, got {code} {body}"
        detail = str(body.get("detail") if isinstance(body, dict) else body)
        assert "no transcription backend configured" in detail, f"typed reason missing: {detail}"
        assert _meeting(stack, user_id, native_id) is None, (
            "refused spawn left an orphaned meeting row — the gate must fire before the DB write"
        )

        # ── GREEN: configure STT via the wizard's endpoint, retry the SAME spawn ───────────────
        code, body = _put_transcription_setting(
            stack, {"url": "http://stt-stub:9090/v1", "token": "wizard-flow-token"}
        )
        assert code == 200, f"wizard settings write: {code} {body}"

        code, body = post_json(f"{stack.meeting_api}/bots", spawn_body, headers=headers)
        assert code == 201, (
            f"spawn after wizard STT config must be accepted, got {code} {body} "
            "(a 409 here = the RED leg orphaned a row; a 503 = the gate ignores Settings)"
        )
        m = _meeting(stack, user_id, native_id)
        assert m is not None and m["status"] in {"requested", "active", "awaiting_admission",
                                                 "joining", "completed"}, f"meeting after green: {m}"

        if MOCK_BOT:
            # let the mock complete so the stack winds down clean; not part of the gate assertion.
            _stop_bot(stack, user_id, native_id)
            _wait_meeting(stack, user_id, native_id, statuses={"completed", "failed"}, timeout=90)
    finally:
        _stop_bot(stack, user_id, native_id)
        # restore: empty string clears the field (admin-api's documented clear semantics).
        _put_transcription_setting(stack, {"url": "", "token": ""})
