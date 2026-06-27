"""Regression tests for ``ws_authorize_subscribe`` (#384).

The validator used to pre-check ``Platform.construct_meeting_url``
BEFORE the DB ownership lookup, which deterministically rejected
WS subscriptions to Teams meetings stored under the 16-hex
hash-derived ``platform_specific_id`` (the form PR #140 introduced
for raw enterprise Teams URLs that can't round-trip to a join URL).
The fix reorders so DB lookup runs first and URL-construct is the
fallback sanity check for native_ids not present in the DB.

These tests pin the new contract:

1. Teams 16-hex native_meeting_id present in the DB → authorized,
   no errors. (Regression for #384.)
2. Teams Live numeric (existing happy path) still authorizes when
   the DB row exists.
3. Native_id missing from the DB → falls through to URL-construct;
   invalid form → "invalid native_meeting_id" error; valid form →
   "not authorized or not found for user" error.
4. Cross-tenant meeting (DB row owned by different user) → not
   authorized error (authorization == DB ownership).
"""

from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meeting_api.models import Meeting
from meeting_api.auth import UserProxy

from .conftest import MockResult


TEST_USER_ID = 9001
TEAMS_HEX_NATIVE_ID = "cdf9aa8702726f1f"
TEAMS_NUMERIC_NATIVE_ID = "1234567890123"


def _make_meeting(*, native_id: str, user_id: int = TEST_USER_ID, platform: str = "teams") -> MagicMock:
    """Minimal ``Meeting`` stand-in carrying the fields the validator + response use."""
    m = MagicMock(spec=Meeting)
    m.id = 7
    m.user_id = user_id
    m.platform = platform
    m.platform_specific_id = native_id
    m.created_at = datetime.now(timezone.utc)
    return m


def _make_user() -> UserProxy:
    user = UserProxy(TEST_USER_ID, 5, ["*"])
    user.email = "regression@example.com"
    user.data = {}
    return user


@pytest_asyncio.fixture
async def collector_client(mock_db) -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """``AsyncClient`` wired to the FastAPI app with collector deps overridden.

    Distinct from ``conftest.client``: that fixture overrides
    ``meeting_api.auth.get_user_and_token`` but the collector router
    declares ``Depends(get_current_user)`` from ``collector.auth`` —
    a separate dependency we need to override here.
    """
    from meeting_api.main import app
    from meeting_api.database import get_db
    from meeting_api.collector.auth import get_current_user

    user = _make_user()

    async def override_get_db():
        yield mock_db

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, mock_db

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_authorizes_teams_hex_native_id_present_in_db(collector_client):
    """Regression for #384.

    Teams 16-hex native_meeting_id (generated server-side from a raw
    enterprise URL per PR #140) MUST authorize when the meeting row
    belongs to the requesting user. Previously the
    ``Platform.construct_meeting_url`` pre-check rejected this form
    before the DB lookup ran.
    """
    client, mock_db = collector_client
    meeting = _make_meeting(native_id=TEAMS_HEX_NATIVE_ID)
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

    resp = await client.post(
        "/ws/authorize-subscribe",
        json={"meetings": [{"platform": "teams", "native_meeting_id": TEAMS_HEX_NATIVE_ID}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert len(body["authorized"]) == 1
    assert body["authorized"][0]["platform"] == "teams"
    assert body["authorized"][0]["native_id"] == TEAMS_HEX_NATIVE_ID
    assert body["authorized"][0]["meeting_id"] == "7"


@pytest.mark.asyncio
async def test_authorizes_teams_numeric_native_id_present_in_db(collector_client):
    """Existing happy path stays green: numeric Teams Live native_ids continue to authorize."""
    client, mock_db = collector_client
    meeting = _make_meeting(native_id=TEAMS_NUMERIC_NATIVE_ID)
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

    resp = await client.post(
        "/ws/authorize-subscribe",
        json={"meetings": [{"platform": "teams", "native_meeting_id": TEAMS_NUMERIC_NATIVE_ID}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert len(body["authorized"]) == 1


@pytest.mark.asyncio
async def test_missing_row_with_invalid_form_still_returns_invalid_error(collector_client):
    """When DB has no matching row AND the native_id isn't a valid URL
    component, the error message preserves the existing
    ``invalid native_meeting_id`` wording so unchanged callers see no
    behavioural drift on the negative path."""
    client, mock_db = collector_client
    mock_db.execute = AsyncMock(return_value=MockResult([]))  # no row

    resp = await client.post(
        "/ws/authorize-subscribe",
        json={"meetings": [{"platform": "teams", "native_meeting_id": "not-a-meeting"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorized"] == []
    assert len(body["errors"]) == 1
    assert "invalid native_meeting_id" in body["errors"][0]


@pytest.mark.asyncio
async def test_missing_row_with_valid_form_returns_not_authorized(collector_client):
    """When the native_id LOOKS valid (e.g. Teams numeric) but no row
    matches the requesting user, the error message stays at
    ``not authorized or not found for user`` — distinguishes
    'meeting exists but not yours' / 'meeting doesn't exist' from
    'this ID is structurally garbage'."""
    client, mock_db = collector_client
    mock_db.execute = AsyncMock(return_value=MockResult([]))

    resp = await client.post(
        "/ws/authorize-subscribe",
        json={"meetings": [{"platform": "teams", "native_meeting_id": TEAMS_NUMERIC_NATIVE_ID}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorized"] == []
    assert len(body["errors"]) == 1
    assert "not authorized or not found" in body["errors"][0]
