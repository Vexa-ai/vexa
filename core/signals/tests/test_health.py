import asyncio
import json

from meeting_signals.health import serve


def test_health_answers_json():
    async def go():
        server = await serve(0, lambda: {"status": "ok", "sources": []})
        port = server.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        raw = await r.read()
        server.close()
        return raw

    raw = asyncio.run(go())
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200") and json.loads(body)["status"] == "ok"
