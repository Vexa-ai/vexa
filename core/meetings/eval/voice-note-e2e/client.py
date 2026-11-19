"""The headless voice-note client (#1783): what Minutes does, from a WAV file. Starts a voice note
through the gateway (`POST /bots {"platform":"voice_note"}`), streams the file's audio over
`WS /voice-notes/{id}/stream` as capture.v1 frames at real-time pace, says Stop, then waits for the
bot to end the note and reads the transcript the bot wrote. Prints one JSON report.

    python client.py --gateway http://127.0.0.1:G --wav speech.wav --bot-log-dir DIR
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys
import time
import wave


def read_pcm16k(path: str) -> list[float]:
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2, \
            "the WAV must be mono 16 kHz 16-bit (ffmpeg -ar 16000 -ac 1 -sample_fmt s16)"
        raw = w.readframes(w.getnframes())
    n = len(raw) // 2
    return [s / 32768.0 for s in struct.unpack(f"<{n}h", raw)]


def frame(ts_ms: float, pcm: list[float]) -> bytes:
    return struct.pack("<id", 0, ts_ms) + struct.pack(f"<{len(pcm)}f", *pcm)


async def run(a) -> dict:
    import httpx
    import redis.asyncio as aioredis
    from websockets.asyncio.client import connect

    key = os.environ["E2E_API_KEY"]
    async with httpx.AsyncClient(base_url=a.gateway, headers={"x-api-key": key}, timeout=30) as http:
        r = await http.post("/bots", json={"platform": "voice_note", "speaker_name": "E2E"})
        r.raise_for_status()
        note = r.json()
    native, mid = note["native_meeting_id"], note["id"]
    pcm = read_pcm16k(a.wav)
    sent_sec = len(pcm) / 16000
    ws_url = a.gateway.replace("http", "ws", 1) + f"/voice-notes/{native}/stream"
    t0 = time.time() * 1000
    frames = 0
    async with connect(ws_url, additional_headers={"x-api-key": key}) as ws:
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready", ready
        step = 1600
        for off in range(0, len(pcm), step):
            await ws.send(frame(t0 + off / 16.0, pcm[off:off + step]))
            frames += 1
            await asyncio.sleep(0.1 / a.speed)
        await ws.send(json.dumps({"type": "stop"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "stopped":
                acked = msg["received_sec"]
                break
    stopped_at = time.time()
    # The bot drains the stream, flushes the lane and reports its terminal state; its log says which.
    status = None
    for _ in range(int(a.wait)):
        for name in os.listdir(a.bot_log_dir):
            with open(os.path.join(a.bot_log_dir, name), errors="replace") as f:
                for line in f:
                    if f"mtg-{mid}-" in name and "orchestrator: emitting completed" in line:
                        status = line.split("emitting ", 1)[1].strip()
        if status:
            break
        await asyncio.sleep(1)
    r = aioredis.from_url(os.environ["E2E_REDIS_URL"], decode_responses=True)
    segs = {}
    for _id, fields in await r.xrange("transcription_segments", "-", "+"):
        p = json.loads(fields.get("payload", "{}"))
        if str(p.get("meeting_id")) != str(mid):
            continue
        for s in p.get("segments") or [p]:
            if s.get("text"):
                segs[s.get("segment_id") or _id] = s
    finals = sorted((s for s in segs.values() if s.get("completed", True)), key=lambda s: s.get("start", 0))
    text = " ".join(s["text"].strip() for s in finals)
    covered = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in finals)
    return {
        "meeting_id": mid, "native_meeting_id": native, "frames": frames,
        "seconds_sent": round(sent_sec, 2), "seconds_acknowledged": acked,
        "bot_terminal": status, "seconds_to_completed": round(time.time() - stopped_at, 1),
        "segments": len(finals), "seconds_transcribed": round(covered, 2),
        "speakers": sorted({s.get("speaker") for s in finals if s.get("speaker")}),
        "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--wait", type=float, default=180)
    ap.add_argument("--bot-log-dir", required=True)
    print(json.dumps(asyncio.run(run(ap.parse_args())), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
