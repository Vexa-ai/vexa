"""The edge refuses an over-size request body with a 413 before anything downstream sees it."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from gateway import create_app
from gateway.body_limit import MAX_BODY_BYTES
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY, "content-type": "application/json"}


def _client():
    downstream = FakeDownstream(status_code=201, body={"id": 1})
    return TestClient(create_app(FakeAuthorizer(), downstream, FakeRedis())), downstream


def test_oversize_body_is_413_and_never_forwarded():
    c, downstream = _client()
    body = json.dumps({"platform": "google_meet", "bot_name": "x" * (MAX_BODY_BYTES + 1)})
    r = c.post("/bots", headers=AUTH, content=body)
    assert r.status_code == 413
    assert "larger than" in r.json()["detail"]
    assert downstream.last is None


def test_oversize_chunked_body_is_413():
    c, downstream = _client()

    def chunks():
        for _ in range(3):
            yield b"x" * (MAX_BODY_BYTES // 2)

    r = c.post("/bots", headers=AUTH, content=chunks())
    assert r.status_code == 413
    assert downstream.last is None


def test_normal_body_is_forwarded():
    c, downstream = _client()
    r = c.post("/bots", headers=AUTH, json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201
    assert downstream.last is not None


def test_multipart_upload_is_not_capped_at_the_edge():
    c, downstream = _client()
    r = c.post("/bots", headers={"x-api-key": VALID_KEY},
               files={"file": ("a.bin", b"x" * (MAX_BODY_BYTES + 1))})
    assert r.status_code != 413
