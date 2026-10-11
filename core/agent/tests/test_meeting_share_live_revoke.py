"""A person removed from a shared meeting loses the LIVE stream they already have open (P20).

`GET /api/meeting/stream` decided access once, when it opened; an owner who removed someone
mid-meeting left them watching until they reloaded. The stream now re-asks the same access union
while it is open and ends with an explicit `access-revoked` event — never silence. Offline: a fake
redis that always has another segment, and an access lookup the test flips mid-stream.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.routers import meetings as meetings_router  # noqa: E402
from shared.config import load_settings  # noqa: E402

OWNER, INVITEE, OUTSIDER, ROW = "71", "72", "73", "4242"


class _FakeRuntime:
    def launch(self, *a, **k): raise AssertionError("no runtime")


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


class _EndlessRedis:
    """Every read returns one more segment; the meeting ends (session_end) after ``ends_after``
    reads, so a stream nobody cuts off still terminates — the test client buffers the whole body."""
    def __init__(self, ends_after=50):
        self.n = 0
        self.ends_after = ends_after

    def xread(self, streams, count=500, block=15000):
        self.n += 1
        if self.n == self.ends_after:
            return [(f"tc:meeting:{ROW}", [(f"{self.n}-0", {"payload": json.dumps({"type": "session_end"})})])]
        if self.n > self.ends_after:
            return []
        payload = {"type": "transcription",
                   "segments": [{"speaker": "A", "text": f"line {self.n}", "start": self.n,
                                 "segment_id": f"s{self.n}"}]}
        return [(f"tc:meeting:{ROW}", [(f"{self.n}-0", {"payload": json.dumps(payload)})])]


def _app(monkeypatch, allowed: set, revoke_after_calls: "int | None" = None):
    """``revoke_after_calls``: after this many access lookups, INVITEE is no longer allowed — the
    owner removing them while their stream is open."""
    import redis
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: _EndlessRedis())
    # Re-check on every loop turn, and do not actually wait between the two asks.
    monkeypatch.setattr(meetings_router, "LIVE_ACCESS_RECHECK_SEC", 0.0)
    monkeypatch.setattr(meetings_router, "LIVE_ACCESS_RETRY_SEC", 0.0)
    calls = {"n": 0}

    def lookup(user_id, meeting_id, workspaces=None):
        calls["n"] += 1
        if revoke_after_calls is not None and calls["n"] > revoke_after_calls:
            allowed.discard(INVITEE)
        if str(meeting_id) != ROW or str(user_id) not in allowed:
            return None
        return {"id": int(ROW), "native_meeting_id": "abc-defg-hij", "user_id": int(OWNER)}

    client = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
        meeting_owner_lookup=lookup))
    return client, calls


def _open(client, uid):
    return client.stream("GET", "/api/meeting/stream", params={"meeting_id": ROW, "session_uid": ROW},
                         headers={"X-User-Id": uid})


def test_outsider_cannot_open_the_live_stream(monkeypatch):
    client, _ = _app(monkeypatch, {OWNER, INVITEE})
    with _open(client, OUTSIDER) as r:
        assert r.status_code == 403


def test_removed_invitee_is_cut_off_mid_stream_with_an_explicit_event(monkeypatch):
    client, calls = _app(monkeypatch, {OWNER, INVITEE}, revoke_after_calls=4)
    with _open(client, INVITEE) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "line 1" in body and "line 2" in body   # they were watching before the removal
    assert '"access-revoked"' in body
    tail = body.split('"access-revoked"', 1)[1]
    assert "line" not in tail, "no segment may follow the revocation"
    assert '"meeting-end"' not in body, "the stream ended because access ended, not the meeting"


def test_the_owner_keeps_streaming_through_the_rechecks(monkeypatch):
    client, calls = _app(monkeypatch, {OWNER}, revoke_after_calls=4)  # revokes INVITEE only
    with _open(client, OWNER) as r:
        body = "".join(r.iter_text())
    assert '"access-revoked"' not in body
    assert "line 40" in body and '"meeting-end"' in body
    assert calls["n"] >= 10  # the stream really did re-ask



def test_an_open_stream_ends_when_the_owner_deletes_the_transcript(monkeypatch):
    """R1801-6: deletion is re-checked while the stream is open, not only when it opens."""
    client, calls = _app(monkeypatch, {OWNER, INVITEE})
    state = {"n": 0}
    real = meetings_router.transcript_erased

    def erased_after_a_while(row):
        state["n"] += 1
        return state["n"] > 3
    monkeypatch.setattr(meetings_router, "transcript_erased", erased_after_a_while)
    with _open(client, INVITEE) as r:
        body = "".join(r.iter_text())
    assert '"access-revoked"' in body and "line 1" in body


def test_the_redis_read_never_blocks_longer_than_the_recheck():
    """R1801-6: the worst case a removed reader keeps a live view is bounded by the re-check, the
    read block and the retry — a read that blocked for the whole interval doubled it."""
    assert meetings_router.LIVE_READ_BLOCK_MS / 1000 < meetings_router.LIVE_ACCESS_RECHECK_SEC
    worst = (meetings_router.LIVE_ACCESS_RECHECK_SEC + meetings_router.LIVE_READ_BLOCK_MS / 1000
             + meetings_router.LIVE_ACCESS_RETRY_SEC)
    assert worst <= 17.5
