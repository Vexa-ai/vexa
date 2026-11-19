"""``GET /health`` without a web framework: one line of JSON, liveness only."""
from __future__ import annotations

import asyncio
import json
from typing import Callable


async def serve(port: int, snapshot: Callable[[], dict]) -> asyncio.base_events.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=5)
            path = request.decode("latin-1").split(" ")[1] if request.count(b" ") >= 2 else ""
            if path.split("?")[0] == "/health":
                status, body = "200 OK", json.dumps(snapshot()).encode()
            else:
                status, body = "404 Not Found", b'{"detail":"not found"}'
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                         "Connection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, IndexError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, "0.0.0.0", port)
