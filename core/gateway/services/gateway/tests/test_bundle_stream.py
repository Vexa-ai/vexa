"""The meeting-bundle rows are relayed as streams, with their own deadline — and only they are.

A bundle is one file of up to hundreds of MB. Through the buffered forward it would be read whole
into this process and capped by the 30 s total timeout; these tests hold the three bundle rows to the
streamed forward instead:

  * an import's body reaches the hop as an async stream of the bytes sent, never a buffered `bytes`;
  * an export's answer is relayed chunk by chunk with its Content-Length and Content-Disposition;
  * the hop carries `BUNDLE_TIMEOUT`, and every other meetings row still rides the buffered leg.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis
from gateway import create_app
from gateway.app import BUNDLE_TIMEOUT


class _Recording(FakeDownstream):
    def __init__(self, chunks, headers):
        super().__init__()
        self.chunks, self.headers, self.opened = chunks, headers, None

    @asynccontextmanager
    async def open_stream(self, method, url, *, headers=None, params=None, content=None, timeout=None):
        body = None
        if content is not None:
            assert not isinstance(content, (bytes, bytearray)), "the body must be relayed, not buffered"
            body = b"".join([c async for c in content])
        self.opened = {"method": method, "url": url, "body": body, "timeout": timeout, "params": params,
                       "headers": headers or {}}
        chunks, head = self.chunks, self.headers

        class _Streamed:
            status_code = 200
            headers = head

            async def aiter_bytes(_self):
                for c in chunks:
                    yield c

        yield _Streamed()


def _client(downstream):
    app = create_app(FakeAuthorizer(), downstream, FakeRedis())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_an_import_body_is_relayed_as_a_stream_with_the_bundle_deadline():
    payload = bytes(range(256)) * 4096          # 1 MiB of non-UTF-8 bytes
    down = _Recording([b'{"imported": true}'], {"content-type": "application/json"})
    async with _client(down) as c:
        r = await c.post("/meetings/import?dry_run=true", content=payload,
                         headers={"X-API-Key": VALID_KEY, "Content-Type": "application/zip"})
    assert r.status_code == 200 and r.json() == {"imported": True}
    assert down.opened["body"] == payload
    assert down.opened["timeout"] is BUNDLE_TIMEOUT
    assert down.opened["params"] == {"dry_run": "true"}
    assert down.opened["headers"].get("content-type") == "application/zip"


async def test_an_export_is_relayed_chunk_by_chunk_with_its_file_headers():
    chunks = [b"PK\x03\x04", b"\xff" * 1000, b"\x00" * 1000]
    down = _Recording(chunks, {"content-type": "application/zip", "content-length": "2004",
                               "content-disposition": 'attachment; filename="m.meeting-bundle.zip"'})
    async with _client(down) as c:
        r = await c.get("/meetings/42/export", headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200
    assert r.content == b"".join(chunks)
    assert r.headers["content-disposition"] == 'attachment; filename="m.meeting-bundle.zip"'
    assert r.headers["content-length"] == "2004"
    assert down.opened["url"].endswith("/meetings/42/export") and down.opened["timeout"] is BUNDLE_TIMEOUT


async def test_other_meetings_rows_keep_the_buffered_forward():
    down = _Recording([], {})
    async with _client(down) as c:
        r = await c.get("/meetings/42", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    assert down.opened is None and down.last is not None and down.last["url"].endswith("/meetings/42")


async def test_the_bundle_deadline_is_longer_than_the_buffered_one_and_still_bounded():
    assert BUNDLE_TIMEOUT.read and BUNDLE_TIMEOUT.read > 30 and BUNDLE_TIMEOUT.write > 30
    assert BUNDLE_TIMEOUT.connect <= 10 and BUNDLE_TIMEOUT.pool <= 10
