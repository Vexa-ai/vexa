"""SSE framing — turn a line stream into events. No HTTP, no timing, just the wire format."""
from __future__ import annotations

import json


async def read_sse_events(response):
    """Yield one parsed JSON event per SSE frame. A frame is one-or-more `data:` lines ended by a
    blank line:

        data: {"type":"card","card":{...}}
        <blank line>   ← frame complete → yield the parsed dict

    `id:`/`event:` lines are ignored — we just want the JSON payloads.
    """
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "":                       # blank line = end of one frame
            if data_lines:
                yield json.loads("".join(data_lines))
                data_lines = []
