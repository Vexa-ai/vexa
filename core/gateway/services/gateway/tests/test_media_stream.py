"""A recording's bytes are relayed as a stream, never buffered at the edge (brain card 98e0be7c).

The raw media row and its `download` alias rode the buffered forward: each answer — a whole master,
since a browser's first `Range: bytes=0-` asks for all of it — was read into this process before a
byte went out. These hold both rows to `_forward_meeting_media`:

  * the answer goes out piece by piece as the hop yields it, with the media headers a 200/206/416
    needs (Content-Length, Content-Range, Accept-Ranges, Content-Type, Content-Disposition);
  * the caller's Range reaches meeting-api, on the media deadline;
  * a connect that never opened is retried like on the buffered leg; an unreachable hop is a 502;
  * every other meetings row keeps the buffered forward.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis
from gateway import app as gateway_app
from gateway import create_app
from gateway.app import MEDIA_TIMEOUT

AUTH = {"X-API-Key": VALID_KEY}


class _Media(FakeDownstream):
    def __init__(self, status, headers, chunks, *, fail_connects=0):
        super().__init__()
        self.media_status, self.media_headers, self.chunks = status, headers, chunks
        self.fail_connects, self.connects, self.opened, self.closed = fail_connects, 0, None, False

    @asynccontextmanager
    async def open_stream(self, method, url, *, headers=None, params=None, content=None, timeout=None):
        self.connects += 1
        if self.connects <= self.fail_connects:
            raise httpx.ConnectError("connection refused")
        self.opened = {"method": method, "url": url, "headers": headers or {}, "params": params,
                       "timeout": timeout, "content": content}
        status, head, chunks, outer = self.media_status, self.media_headers, self.chunks, self

        class _Streamed:
            status_code = status
            headers = head

            async def aiter_bytes(_self):
                for c in chunks:
                    yield c

        try:
            yield _Streamed()
        finally:
            outer.closed = True


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def _no_wait(_s):
        return None
    monkeypatch.setattr(gateway_app, "_retry_sleep", _no_wait)


def _client(down):
    app = create_app(FakeAuthorizer(), down, FakeRedis())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_a_range_answer_is_relayed_piece_by_piece_with_its_media_headers():
    chunks = [bytes([i]) * 1000 for i in range(5)]
    down = _Media(206, {"content-type": "audio/webm", "content-length": "5000",
                        "content-range": "bytes 500-5499/722448", "accept-ranges": "bytes",
                        "x-vexa-internal": "never relayed"}, chunks)
    async with _client(down) as c:
        r = await c.get("/recordings/369743266808/media/827161823280/raw?type=audio",
                        headers={**AUTH, "Range": "bytes=500-5499"})
    assert r.status_code == 206
    assert r.content == b"".join(chunks)
    assert r.headers["content-range"] == "bytes 500-5499/722448"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == "5000"
    assert r.headers["content-type"].startswith("audio/webm")
    assert "x-vexa-internal" not in r.headers
    assert down.opened["url"].endswith("/recordings/369743266808/media/827161823280/raw")
    assert down.opened["params"] == {"type": "audio"}
    assert down.opened["headers"].get("range") == "bytes=500-5499", "the caller's Range reaches the hop"
    assert down.opened["timeout"] is MEDIA_TIMEOUT
    assert down.opened["content"] is None
    assert down.closed


async def test_the_download_alias_streams_to_raw_with_its_disposition():
    down = _Media(200, {"content-type": "audio/wav", "content-length": "3",
                        "content-disposition": 'attachment; filename="meeting-5-audio.wav"'}, [b"abc"])
    async with _client(down) as c:
        r = await c.get("/recordings/5/media/9/download", headers=AUTH)
    assert r.status_code == 200 and r.content == b"abc"
    assert r.headers["content-disposition"] == 'attachment; filename="meeting-5-audio.wav"'
    assert down.opened["url"].endswith("/recordings/5/media/9/raw")


async def test_an_unsatisfiable_range_is_relayed_as_is():
    down = _Media(416, {"content-type": "application/json", "content-range": "bytes */100"},
                  [b'{"detail":"Requested range not satisfiable"}'])
    async with _client(down) as c:
        r = await c.get("/recordings/5/media/9/raw", headers={**AUTH, "Range": "bytes=500-"})
    assert r.status_code == 416 and r.headers["content-range"] == "bytes */100"


async def test_the_edge_sends_each_piece_as_it_arrives():
    """Straight through the ASGI app (httpx's ASGI transport collects the body): 64 upstream pieces
    leave the edge as 64 body messages, none larger than one piece. The buffered forward sent one."""
    piece = 256 * 1024
    chunks = [b"\x00" * piece for _ in range(64)]
    down = _Media(206, {"content-type": "audio/wav", "content-length": str(piece * 64),
                        "content-range": f"bytes 0-{piece * 64 - 1}/{piece * 64}"}, chunks)
    app = create_app(FakeAuthorizer(), down, FakeRedis())
    sizes: list[int] = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sizes.append(len(message["body"]))

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/recordings/5/media/9/raw",
             "raw_path": b"/recordings/5/media/9/raw", "query_string": b"type=audio",
             "root_path": "", "client": ("t", 1), "server": ("gw", 80),
             "headers": [(b"x-api-key", VALID_KEY.encode()), (b"range", b"bytes=0-")]}
    await app(scope, receive, send)
    assert sum(sizes) == piece * 64
    assert len(sizes) == 64 and max(sizes) == piece


async def test_a_connect_that_never_opened_is_retried():
    down = _Media(200, {"content-type": "audio/wav", "content-length": "2"}, [b"ok"], fail_connects=2)
    async with _client(down) as c:
        r = await c.get("/recordings/5/media/9/raw", headers=AUTH)
    assert r.status_code == 200 and r.content == b"ok" and down.connects == 3


async def test_an_unreachable_hop_is_still_a_502():
    down = _Media(200, {}, [], fail_connects=10_000)
    async with _client(down) as c:
        r = await c.get("/recordings/5/media/9/raw", headers=AUTH)
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream unreachable: ConnectError"


async def test_no_key_never_reaches_the_hop():
    down = _Media(200, {}, [b"x"])
    async with _client(down) as c:
        assert (await c.get("/recordings/5/media/9/raw")).status_code == 401
    assert down.connects == 0


async def test_the_master_metadata_row_keeps_the_buffered_forward():
    down = _Media(200, {}, [])
    async with _client(down) as c:
        r = await c.get("/recordings/5/master?type=audio", headers=AUTH)
    assert r.status_code == 200
    assert down.opened is None and down.last["url"].endswith("/recordings/5/master")


def test_the_media_deadline_is_bounded():
    assert MEDIA_TIMEOUT.read is not None and 30 <= MEDIA_TIMEOUT.read <= 120
    assert MEDIA_TIMEOUT.connect <= 10 and MEDIA_TIMEOUT.pool <= 10
