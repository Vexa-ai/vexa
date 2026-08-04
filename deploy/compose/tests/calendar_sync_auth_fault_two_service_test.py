"""#991 — real admin-api rejection remains a retryable meeting-api fault, never absence."""
from __future__ import annotations

import json
import os
import uuid

import pytest

from conftest import http, requires_docker

AUTH_FAULT_OVERLAY_ACTIVE = os.getenv("T303_CALENDAR_AUTH_FAULT") == "1"
pytestmark = [
    requires_docker,
    pytest.mark.skipif(
        not AUTH_FAULT_OVERLAY_ACTIVE,
        reason="requires the explicit calendar-sync wrong-secret Compose overlay",
    ),
]

WRONG_INTERNAL_SECRET = "t303-intentionally-wrong-internal-secret"


def _admin_headers(stack):
    return {"X-Admin-API-Key": stack.admin_token, "Content-Type": "application/json"}


def _create_user_and_token(stack) -> tuple[int, str]:
    email = f"calendar-991-auth-{uuid.uuid4().hex[:8]}@vexa.ai"
    code, body = http(
        "POST",
        f"{stack.admin_api}/admin/users",
        headers=_admin_headers(stack),
        body=json.dumps({"email": email, "name": "calendar-991-auth", "max_concurrent_bots": 1}).encode(),
    )
    assert code in (200, 201), f"create user: {code} {body}"
    user_id = body["id"]
    code, body = http(
        "POST",
        f"{stack.admin_api}/admin/users/{user_id}/tokens?scopes=bot",
        headers=_admin_headers(stack),
        body=b"",
    )
    assert code == 201, f"mint token: {code} {body}"
    return user_id, body["token"]


def test_real_admin_rejection_is_retryable_503_not_false_404(stack):
    user_id, token = _create_user_and_token(stack)
    marker = "t303-auth-fault-private-feed-marker"
    # Loopback keeps this test fail-closed even if its opt-in/overlay guard is later changed.
    feed_url = f"https://127.0.0.1/{marker}/calendar.ics"
    auth = {"X-API-Key": token, "Content-Type": "application/json"}

    effective_secret = stack.exec(
        "meeting-api", "python", "-c",
        "import os; print(os.environ.get('INTERNAL_API_SECRET', ''))",
    )
    assert effective_secret == WRONG_INTERNAL_SECRET

    code, saved = http(
        "PUT",
        f"{stack.gateway}/user/calendar",
        headers=auth,
        body=json.dumps({"ics_url": feed_url, "auto_join": False}).encode(),
    )
    assert code == 200, f"calendar save: {code} {saved}"
    assert saved["ics_url_set"] is True
    assert saved["auto_join"] is False
    assert marker not in json.dumps(saved)

    # The producer has the row under its correct internal credential.
    code, discovered = http(
        "GET",
        f"{stack.admin_api}/internal/calendar-configs",
        headers={"X-Internal-Secret": stack.internal_secret},
    )
    assert code == 200
    assert any(cfg["user_id"] == user_id and cfg["ics_url"] == feed_url
               for cfg in discovered["configs"])

    # The exact credential meeting-api uses in this overlay is rejected by the real admin-api.
    code, rejected = http(
        "GET",
        f"{stack.admin_api}/internal/calendar-configs",
        headers={"X-Internal-Secret": WRONG_INTERNAL_SECRET},
    )
    assert code == 403, f"wrong-secret control: {code} {rejected}"

    code, fault = http(
        "POST",
        f"{stack.gateway}/user/calendar/sync",
        headers={"X-API-Key": token},
    )
    assert code == 503, f"sync discovery fault: {code} {fault}"
    assert fault == {
        "detail": "calendar configuration is temporarily unavailable",
        "code": "calendar_config_discovery_unavailable",
        "retryable": True,
    }
    assert code != 404

    code, stamp = http(
        "GET",
        f"{stack.gateway}/user/calendar/sync",
        headers={"X-API-Key": token},
    )
    assert code == 200
    assert stamp["last_error"] == "calendar configuration is temporarily unavailable"
    assert marker not in json.dumps(stamp)
    assert WRONG_INTERNAL_SECRET not in json.dumps(stamp)

    logs_by_service = {
        service: stack.logs(service)
        for service in ("meeting-api", "gateway", "admin-api")
    }
    meeting_logs = logs_by_service["meeting-api"]
    assert "calendar_config_discovery_failed" in meeting_logs
    assert "authentication" in meeting_logs
    for logs in logs_by_service.values():
        assert marker not in logs
        assert WRONG_INTERNAL_SECRET not in logs
        assert token not in logs
