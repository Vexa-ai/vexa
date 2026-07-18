#!/usr/bin/env python3
"""ws_tail.py — subscribe to the gateway ``/ws`` feed and record every frame as JSONL.

The probe's live-transcript listener: it REUSES the stdlib WebSocket client the compose
stack proof already carries (``deploy/compose/tests/_ws.py``) — the probe invents no new
client. ``journey.sh`` starts it BEFORE spawning the bot, so live transcript frames are
caught the moment the collector fans them out, and greps the JSONL afterwards.

usage: ws_tail.py <gateway_url> <api_key> <platform> <native_id> <seconds> <out_file>

Writes one JSON object per line (the ``subscribed`` ack first, then each frame).
Exits 0 iff the subscribe ack arrived.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "deploy" / "compose" / "tests"))
from _ws import WS  # noqa: E402 — the reused stdlib WS client

gateway, api_key, platform, native_id, seconds, out_file = sys.argv[1:7]
ws_url = gateway.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")

acked = False
with open(out_file, "w") as out:
    try:
        ws = WS(f"{ws_url}/ws?api_key={api_key}", timeout=15)
    except Exception as e:  # noqa: BLE001 — the probe reads this line as the failure evidence
        out.write(json.dumps({"type": "probe-error", "error": str(e)}) + "\n")
        sys.exit(1)
    ws.send_text(json.dumps({"action": "subscribe",
                             "meetings": [{"platform": platform, "native_id": native_id}]}))
    deadline = time.time() + float(seconds)
    while time.time() < deadline:
        try:
            raw = ws.recv_text(timeout=min(5.0, max(0.5, deadline - time.time())))
        except TimeoutError:
            continue
        except Exception:  # noqa: BLE001 — closed by peer / handshake torn down → stop tailing
            break
        try:
            frame = json.loads(raw)
        except ValueError:
            frame = {"type": "raw", "raw": raw[:500]}
        out.write(json.dumps(frame) + "\n")
        out.flush()
        if frame.get("type") == "subscribed":
            acked = True
    ws.close()
sys.exit(0 if acked else 1)
