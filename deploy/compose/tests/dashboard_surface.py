#!/usr/bin/env python3
"""Dashboard FULL-SURFACE harness — proves the vendored dashboard works for SEND-BOT + live WS TRANSCRIPT,
against the REAL compose stack, through the dashboard's OWN routes (the surface a browser drives).

In order, against the running dashboard:
  1. config    — GET {dash}/api/config → the runtime SSOT the browser uses (wsUrl + authToken).
  2. send-bot  — POST {dash}/api/vexa/bots (the Next proxy → gateway/bots) with bot_name=mock:emit-n-segments
                 → a meeting is created (201) and the mock bot is spawned by the REAL runtime.
  3. transcript— connect to the EXACT wsUrl the config prescribed (+ its authToken), subscribe, and assert the
                 mock bot's transcript segments arrive in REAL TIME (collector → tc:…:mutable → gateway /ws).

So it exercises the dashboard's config + send-bot proxy + live-transcript WS — the two flows the user asked
for — deterministically, no browser/STT/live-meeting (the mock bot supplies the transcript). Exit 0 = green.
Env: DASHBOARD_URL (http://127.0.0.1:18030).
"""
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ws import WS  # the hand-rolled RFC6455 client the stack tests use

DASH = os.environ["DASHBOARD_URL"].rstrip("/")


def http(method, url, headers=None, body=None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main() -> int:
    # 1. config surface — the dashboard hands the browser its API + WS SSOT.
    code, cfg = http("GET", f"{DASH}/api/config")
    assert code == 200 and isinstance(cfg, dict) and cfg.get("wsUrl") and cfg.get("authToken"), \
        f"[1/config] {code} {cfg}"
    print(f"[1/config] 200 · wsUrl={cfg['wsUrl']} · authToken present")

    # 2. send-bot surface — POST through the dashboard's Next proxy → gateway → real runtime spawns the mock.
    native = f"dash-{uuid.uuid4().hex[:6]}"
    code, meeting = http(
        "POST", f"{DASH}/api/vexa/bots",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"platform": "google_meet", "native_meeting_id": native,
                         "bot_name": "mock:emit-n-segments"}).encode(),
    )
    assert code in (200, 201) and isinstance(meeting, dict) and meeting.get("id"), f"[2/send-bot] {code} {meeting}"
    print(f"[2/send-bot] {code} · meeting id={meeting['id']} status={meeting.get('status')} — mock bot spawned")

    # 3. live WS transcript — connect to the EXACT wsUrl the dashboard prescribed (its self-host authToken).
    ws_url = f"{cfg['wsUrl']}?api_key={cfg['authToken']}"
    ws = WS(ws_url, timeout=25)
    try:
        ws.send_text(json.dumps({"action": "subscribe",
                                 "meetings": [{"platform": "google_meet", "native_id": native}]}))
        got = []
        deadline = time.time() + 90
        while time.time() < deadline and not got:
            try:
                msg = json.loads(ws.recv_text(timeout=5))
            except Exception:
                continue
            if msg.get("type") == "transcript":
                for s in (msg.get("confirmed", []) or []) + (msg.get("pending", []) or []):
                    if "mock utterance" in (s.get("text") or ""):
                        got.append(s["text"])
        assert got, f"[3/transcript] no live mock-segment frame reached the dashboard's WS ({ws_url}) in 90s"
        print(f"[3/ws-transcript] LIVE · {len(got)} segment(s) rendered: {got[:3]}")
    finally:
        ws.close()

    print("\n✅ DASHBOARD SURFACE GREEN — config · send-bot · real-time WS transcript all work through the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
