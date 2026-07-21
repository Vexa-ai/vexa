#!/usr/bin/env python3
"""scripted_bot.py — the e2e rig's deterministic meeting-bot (BOT_COMMAND override).

The lite runtime's process backend documents that an operator-provided BOT_COMMAND wins over the
baked launcher; the e2e driver points it here, so a capture spawned through the REAL control plane
(ensure → captures → runtime → process spawn) runs THIS scripted worker instead of a Chromium that
would fail to join a synthetic Meet. Everything downstream of the spawn stays production machinery:
the sealed invocation arrives in VEXA_BOT_CONFIG, lifecycle goes to the REAL callback receiver
(meetingApiCallbackUrl), segments ride the REAL ``transcription_segments`` stream (the collector's
XREADGROUP source), and the stop path is the REAL ``bot_commands:meeting:{id}`` leave command.

Scenario is scripted by the MEETING URL the driver chose (the invocation carries it verbatim):
  * ``…-lobb-…``  — lobby-only: joining → awaiting_admission, no segments, leave/SIGTERM → completed.
                    Workstream A's settlement contract says this capture settles 0 seconds.
  * anything else — normal: joining → active, one speaker-attributed segment per ~1.5s, leave/SIGTERM
                    → completed. Settlement equals the scripted active window.

Terminal discipline: on leave (redis pub/sub) or SIGTERM (runtime delete_workload) the bot POSTs a
``completed`` lifecycle event BEFORE exiting — the parent classifies a user stop as completed, never
failed (lifecycle/stop.py). A second terminal POST is an idempotent no-op replay by design.

Runs under the lite image's meeting venv (BOT_COMMAND uses /opt/venvs/meeting/bin/python) for the
``redis`` client; lifecycle POSTs are stdlib urllib so a venv drift can only cost the segment leg.
"""
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone

SEGMENT_STREAM = "transcription_segments"
SEGMENT_INTERVAL_S = 1.5
MAX_LIFETIME_S = 240  # backstop only: the driver stops the capture long before this


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config() -> dict:
    raw = os.environ.get("VEXA_BOT_CONFIG") or os.environ.get("BOT_CONFIG") or ""
    cfg = json.loads(raw)
    if not isinstance(cfg, dict):
        raise ValueError("VEXA_BOT_CONFIG is not an object")
    return cfg


def _post_lifecycle(url: str, connection_id: str, status: str) -> None:
    body = json.dumps({
        "connection_id": connection_id,
        "status": status,
        "timestamp": _now_iso(),
    }).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
    print(f"[scripted-bot] lifecycle {status} -> {url}", flush=True)


def _segment_envelope(cfg: dict, index: int) -> dict:
    """One collector-honest transcription envelope carrying a single completed segment."""
    now = datetime.now(timezone.utc)
    start = float(index) * 2.0
    return {
        "type": "transcription",
        "meeting_id": int(cfg["meeting_id"]),
        "native_meeting_id": cfg.get("nativeMeetingId"),
        "platform": cfg.get("platform") or "google_meet",
        "segments": [{
            "segment_id": f"e2e-seg-{index:04d}",
            "start": start,
            "end": start + 1.5,
            "text": f"Synthetic segment {index}: the quick brown fox files minutes.",
            "language": "en",
            "speaker": "Alice Example" if index % 2 == 0 else "Bob Example",
            "completed": True,
            "absolute_start_time": now.isoformat(),
            "absolute_end_time": now.isoformat(),
        }],
    }


def main() -> int:
    cfg = _config()
    connection_id = cfg["connectionId"]
    meeting_id = int(cfg["meeting_id"])
    callback_url = cfg["meetingApiCallbackUrl"]
    lobby = "-lobb-" in (cfg.get("meetingUrl") or "")

    terminal_sent = False

    def send_terminal() -> None:
        nonlocal terminal_sent
        if terminal_sent:
            return
        terminal_sent = True
        try:
            _post_lifecycle(callback_url, connection_id, "completed")
        except Exception as error:  # noqa: BLE001 — the reconcile backstop owns recovery
            print(f"[scripted-bot] terminal post failed: {error}", flush=True)

    def on_sigterm(_signum, _frame):
        # runtime delete_workload SIGTERMs the group (a lobby withdraw does this immediately);
        # post the terminal the real bot would post on its way out, then exit cleanly.
        send_terminal()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_sigterm)

    import redis  # the meeting venv ships it; imported after config so a parse error is loud first

    client = redis.Redis.from_url(cfg.get("redisUrl") or os.environ.get("REDIS_URL") or "redis://localhost:6379/0")
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(f"bot_commands:meeting:{meeting_id}")

    def leave_requested() -> bool:
        message = pubsub.get_message(timeout=0.1)
        while message is not None:
            try:
                payload = json.loads(message.get("data") or b"{}")
            except (ValueError, TypeError):
                payload = {}
            if payload.get("action") == "leave":
                return True
            message = pubsub.get_message(timeout=0.05)
        return False

    _post_lifecycle(callback_url, connection_id, "joining")
    time.sleep(0.5)

    deadline = time.monotonic() + MAX_LIFETIME_S
    if lobby:
        _post_lifecycle(callback_url, connection_id, "awaiting_admission")
        while time.monotonic() < deadline and not leave_requested():
            time.sleep(0.2)
    else:
        _post_lifecycle(callback_url, connection_id, "active")
        index = 0
        next_segment = time.monotonic()
        while time.monotonic() < deadline and not leave_requested():
            if time.monotonic() >= next_segment:
                client.xadd(SEGMENT_STREAM, {"payload": json.dumps(_segment_envelope(cfg, index))})
                index += 1
                next_segment = time.monotonic() + SEGMENT_INTERVAL_S
            time.sleep(0.1)
        print(f"[scripted-bot] emitted {index} segments", flush=True)

    send_terminal()
    return 0


if __name__ == "__main__":
    sys.exit(main())
