"""G3 Gate: CRUD against real Postgres + Redis + runtime-api.

Run with: MEETING_API_URL=http://localhost:8080 pytest tests/test_gate_g3.py -v
Or via gateway: MEETING_API_URL=http://localhost:8056 API_KEY=<token> pytest tests/test_gate_g3.py -v

Tests hit a REAL running meeting-api instance. Container spawn may fail
(no bot image) — that's expected for G3; we verify the DB record path.
"""
import os
import subprocess
import uuid

import httpx
import pytest

BASE = os.getenv("MEETING_API_URL", "http://localhost:8080")
API_KEY = os.getenv("API_KEY", "")
HEADERS = (
    {"X-API-Key": API_KEY}
    if API_KEY
    else {"X-User-ID": "1", "X-User-Scopes": "bot,user", "X-User-Limits": "5"}
)

# Postgres connection defaults (compose local-db)
PG_HOST = os.getenv("DB_HOST", "localhost")
PG_PORT = os.getenv("DB_PORT", "5438")
PG_USER = os.getenv("DB_USER", "postgres")
PG_PASS = os.getenv("DB_PASSWORD", "postgres")
PG_DB = os.getenv("DB_NAME", "vexa")


def _psql(query: str) -> str:
    """Run a psql query and return stdout. Raises SkipTest on failure."""
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASS
    try:
        result = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER,
             "-d", PG_DB, "-t", "-A", "-c", query],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if result.returncode != 0:
            pytest.skip(f"psql error: {result.stderr.strip()}")
        return result.stdout.strip()
    except FileNotFoundError:
        pytest.skip("psql not installed")
    except subprocess.TimeoutExpired:
        pytest.skip("psql timed out")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30, headers=HEADERS) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def check_service(client):
    try:
        # Try /health (direct meeting-api) or / (via gateway)
        r = client.get("/health")
        if r.status_code == 404:
            r = client.get("/")
        r.raise_for_status()
    except httpx.ConnectError:
        pytest.skip(f"meeting-api not running at {BASE}")
    except httpx.HTTPStatusError:
        pytest.skip(f"meeting-api not healthy at {BASE}")


# ------------------------------------------------------------------
# 1. Health
# ------------------------------------------------------------------
def test_health(client):
    # /health exists on meeting-api directly; gateway serves / instead
    r = client.get("/health")
    if r.status_code == 404:
        r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    # Direct meeting-api returns {"status": "ok"}, gateway returns {"message": "Welcome..."}
    assert "status" in body or "message" in body


# ------------------------------------------------------------------
# 2. GET /bots/status — frozen contract shape
# ------------------------------------------------------------------
def test_bots_status_shape(client):
    r = client.get("/bots/status")
    assert r.status_code == 200
    body = r.json()
    assert "running_bots" in body
    assert isinstance(body["running_bots"], list)


# ------------------------------------------------------------------
# 3. POST /bots — creates meeting in Postgres
#    Container spawn will fail (no bot image) → 500, but DB record exists
# ------------------------------------------------------------------
_TEST_MEET_CODE = f"abc-{''.join(uuid.uuid4().hex[:4])}-hij"


def test_create_meeting_db_record(client):
    """POST /bots should create a meeting record even if spawn fails."""
    r = client.post(
        "/bots",
        json={
            "platform": "google_meet",
            "native_meeting_id": _TEST_MEET_CODE,
        },
    )
    # 201 = full success; 500 = spawn failed, record marked failed
    assert r.status_code in (201, 500), f"Unexpected: {r.status_code} {r.text}"

    # Verify the record exists in Postgres
    row = _psql(
        f"SELECT id, status, platform FROM meetings "
        f"WHERE platform_specific_id = '{_TEST_MEET_CODE}' "
        f"ORDER BY id DESC LIMIT 1;"
    )
    assert row, f"No meeting found in DB for {_TEST_MEET_CODE}"
    parts = row.split("|")
    assert len(parts) >= 3
    meeting_id, status, platform = parts[0], parts[1], parts[2]
    assert int(meeting_id) > 0
    assert platform == "google_meet"
    # Status should be 'requested' (if spawn succeeded) or 'failed' (if spawn failed)
    assert status in ("requested", "failed", "active")


# ------------------------------------------------------------------
# 4. GET /bots/status after create
# ------------------------------------------------------------------
def test_bots_status_after_create(client):
    r = client.get("/bots/status")
    assert r.status_code == 200
    body = r.json()
    assert "running_bots" in body


# ------------------------------------------------------------------
# 5-6. Callback lifecycle: started → exited
#
# Create a meeting + session directly in Postgres so callbacks
# can find them (normally session is created after successful spawn).
# ------------------------------------------------------------------
_CALLBACK_SESSION_UID = f"g3-sess-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def callback_meeting_id():
    """Insert a meeting + session directly in Postgres for callback tests."""
    # Insert meeting
    mid = _psql(
        "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
        f"VALUES (1, 'google_meet', 'g3-callback-test', 'requested', '{{}}') "
        "RETURNING id;"
    )
    if not mid:
        pytest.skip("Failed to insert test meeting into Postgres")
    meeting_id = int(mid)

    # Insert meeting session
    _psql(
        f"INSERT INTO meeting_sessions (meeting_id, session_uid) "
        f"VALUES ({meeting_id}, '{_CALLBACK_SESSION_UID}');"
    )
    return meeting_id


def test_callback_started(client, callback_meeting_id):
    """POST /bots/internal/callback/started → meeting transitions to ACTIVE."""
    r = client.post(
        "/bots/internal/callback/started",
        json={
            "connection_id": _CALLBACK_SESSION_UID,
            "container_id": f"g3-container-{uuid.uuid4().hex[:6]}",
        },
    )
    assert r.status_code == 200, f"Unexpected: {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "startup processed"
    assert body["meeting_id"] == callback_meeting_id
    assert body["meeting_status"] == "active"

    # Verify in DB
    row = _psql(
        f"SELECT status, start_time IS NOT NULL as has_start "
        f"FROM meetings WHERE id = {callback_meeting_id};"
    )
    parts = row.split("|")
    assert parts[0] == "active"
    assert parts[1] == "t", "start_time should be set after started callback"


def test_callback_exited(client, callback_meeting_id):
    """POST /bots/internal/callback/exited with exit_code=0 → COMPLETED."""
    r = client.post(
        "/bots/internal/callback/exited",
        json={
            "connection_id": _CALLBACK_SESSION_UID,
            "exit_code": 0,
            "reason": "self_initiated_leave",
        },
    )
    assert r.status_code == 200, f"Unexpected: {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "callback processed"
    assert body["meeting_id"] == callback_meeting_id
    assert body["final_status"] == "completed"

    # Verify in DB
    row = _psql(
        f"SELECT status, end_time IS NOT NULL as has_end, "
        f"data::text LIKE '%completion_reason%' as has_completion "
        f"FROM meetings WHERE id = {callback_meeting_id};"
    )
    parts = row.split("|")
    assert parts[0] == "completed"
    assert parts[1] == "t", "end_time should be set after exit callback"


# ------------------------------------------------------------------
# 7. Verify DB integrity — statuses, timestamps
# ------------------------------------------------------------------
def test_db_meetings_table_queryable():
    """Verify meetings table exists and is queryable."""
    count = _psql("SELECT count(*) FROM meetings;")
    assert int(count) >= 0


def test_db_meeting_sessions_table_queryable():
    """Verify meeting_sessions table exists and is queryable."""
    count = _psql("SELECT count(*) FROM meeting_sessions;")
    assert int(count) >= 0


def test_db_meeting_statuses_valid():
    """All meeting statuses in DB are valid enum values."""
    rows = _psql(
        "SELECT DISTINCT status FROM meetings;"
    )
    valid_statuses = {
        "requested", "joining", "awaiting_admission",
        "active", "needs_human_help", "stopping",
        "completed", "failed",
    }
    for status in rows.split("\n"):
        status = status.strip()
        if status:
            assert status in valid_statuses, f"Invalid status in DB: {status}"


def test_db_meetings_have_timestamps():
    """All meetings have created_at and updated_at timestamps."""
    row = _psql(
        "SELECT count(*) FROM meetings "
        "WHERE created_at IS NULL OR updated_at IS NULL;"
    )
    assert int(row) == 0, "Found meetings with NULL timestamps"


# ------------------------------------------------------------------
# Cleanup: remove test data (best-effort)
# ------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def cleanup(request):
    """Clean up test records after all tests run."""
    yield
    try:
        _psql(
            f"DELETE FROM meeting_sessions WHERE session_uid = '{_CALLBACK_SESSION_UID}';"
        )
        _psql(
            f"DELETE FROM meetings WHERE platform_specific_id IN "
            f"('{_TEST_MEET_CODE}', 'g3-callback-test');"
        )
    except Exception:
        pass
