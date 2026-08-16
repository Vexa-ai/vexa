"""Shared fixtures: a door wired to a fake meeting API and a temp identity store."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from chat_door.app import create_app
from chat_door.config import DoorConfig, SigningKey
from chat_door.meetings_client import MeetingsClient
from chat_door.store import FileIdentityStore
from chat_door.tokens import TokenSigner

TEST_KEY = b"test-signing-key-not-a-real-secret"

MEETING_126 = {
    "id": 126,
    "platform": "google_meet",
    "native_meeting_id": "uyz-nvfo-uuv",
    "data": {"name": "Henry Buisseret", "participants": ["Henry Buisseret"]},
}
SEGMENTS_126 = [
    {"speaker": "Henry Buisseret", "text": "so again it just sent the bot to the conversation"},
    {"speaker": "Dmitry Grankin", "text": "yes — and the promo code follows today"},
]


def make_meetings_transport(
    *,
    record_route_status: int = 200,
    segments: list[dict] | None = None,
    known_meetings: dict[str, dict] | None = None,
) -> httpx.MockTransport:
    """A fake meeting API.

    ``record_route_status`` lets a test make ``/meetings/{id}/transcript`` answer 404/405 so the
    fallback to ``/transcripts/by-id/{id}`` (the route that exists today) is exercised.
    """
    meetings = known_meetings if known_meetings is not None else {"126": MEETING_126}
    segs = SEGMENTS_126 if segments is None else segments

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/meetings/") and path.endswith("/transcript"):
            mid = path.split("/")[2]
            if record_route_status != 200:
                return httpx.Response(record_route_status, json={"detail": "not here"})
            if mid not in meetings:
                return httpx.Response(404, json={"detail": "unknown"})
            return httpx.Response(200, json={"segments": segs})
        if path.startswith("/transcripts/by-id/"):
            mid = path.rsplit("/", 1)[-1]
            if mid not in meetings:
                return httpx.Response(404, json={"detail": "unknown"})
            return httpx.Response(200, json={"segments": segs})
        if path.startswith("/meetings/"):
            mid = path.rsplit("/", 1)[-1]
            if mid not in meetings:
                return httpx.Response(404, json={"detail": "unknown"})
            return httpx.Response(200, json=meetings[mid])
        return httpx.Response(404, json={"detail": json.dumps({"path": path})})

    return httpx.MockTransport(handler)


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture
def config(store_dir: Path) -> DoorConfig:
    return DoorConfig(
        signing_key=SigningKey(TEST_KEY),
        base_url="http://door.test",
        meetings_url="http://meetings.test",
        store_dir=store_dir,
        link_ttl_seconds=3600,
        session_ttl_seconds=3600,
    )


@pytest.fixture
def signer() -> TokenSigner:
    return TokenSigner(TEST_KEY)


@pytest.fixture
def store(store_dir: Path) -> FileIdentityStore:
    return FileIdentityStore(store_dir)


@pytest.fixture
def door(config: DoorConfig, signer: TokenSigner, store: FileIdentityStore):
    """(client, signer, store) — the shipped app driven in-process."""
    meetings = MeetingsClient(config.meetings_url, transport=make_meetings_transport())
    app = create_app(config, signer=signer, store=store, meetings=meetings)
    with TestClient(app) as client:
        yield client, signer, store
