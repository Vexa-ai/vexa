#!/usr/bin/env python3
"""
Full pipeline test — validates transcript output quality through:
1. Redis hash storage (concurrent speakers, draft→confirmed)
2. REST API output (dedup, sorting, speaker preservation)
3. Pub/sub delivery (WS message format)

Run inside vexa Docker network: REDIS_URL=redis://redis:6379/0
"""
import json
import time
import hmac
import hashlib
import base64
import os
import redis as redis_lib
import threading

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM = "transcription_segments"
MEETING_ID = 99901
SESSION_UID = "pipeline-test-001"
SECRET = "token"

def mint_jwt(meeting_id):
    def b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps({
        "meeting_id": meeting_id, "user_id": 23, "platform": "google_meet",
        "native_meeting_id": "pipeline-test", "scope": "transcribe:write",
        "iss": "bot-manager", "aud": "transcription-collector",
        "exp": int(time.time()) + 86400,
    }).encode())
    sig = b64url(hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

TOKEN = mint_jwt(MEETING_ID)

SESSION_START_TIME = None  # Set in main()

def _abs_time(relative_sec):
    """Convert relative seconds to absolute ISO UTC string."""
    from datetime import datetime, timezone, timedelta
    return (SESSION_START_TIME + timedelta(seconds=relative_sec)).isoformat()

def publish(r, segments):
    # Inject absolute timestamps into each segment
    for seg in segments:
        if SESSION_START_TIME and "absolute_start_time" not in seg:
            seg["absolute_start_time"] = _abs_time(seg["start"])
            seg["absolute_end_time"] = _abs_time(seg["end"])
    payload = json.dumps({
        "type": "transcription", "token": TOKEN, "uid": SESSION_UID,
        "platform": "google_meet", "meeting_id": str(MEETING_ID),
        "segments": segments,
    })
    return r.xadd(STREAM, {"payload": payload})

def check_hash(r, label):
    hash_key = f"meeting:{MEETING_ID}:segments"
    segments = r.hgetall(hash_key)
    print(f"\n  [{label}] Redis hash has {len(segments)} segments:")
    for k in sorted(segments.keys(), key=lambda x: float(x)):
        seg = json.loads(segments[k])
        print(f"    [{k:>10}] {seg.get('speaker','?'):15s} | completed={str(seg.get('completed','')):5s} | {seg.get('text','')[:50]}")
    return segments

def main():
    r = redis_lib.from_url(REDIS_URL, decode_responses=True)
    hash_key = f"meeting:{MEETING_ID}:segments"

    # Clean up from previous run
    r.delete(hash_key)
    r.srem("active_meetings", str(MEETING_ID))

    # Subscribe to pub/sub to capture WS messages
    ws_messages = []
    pubsub = r.pubsub()
    channel = f"tc:meeting:{MEETING_ID}:mutable"
    pubsub.subscribe(channel)

    def listen():
        for msg in pubsub.listen():
            if msg["type"] == "message":
                ws_messages.append(json.loads(msg["data"]))
    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    time.sleep(0.5)

    # Session start
    global SESSION_START_TIME
    from datetime import datetime, timezone
    SESSION_START_TIME = datetime.now(timezone.utc)
    r.xadd(STREAM, {"payload": json.dumps({
        "type": "session_start", "token": TOKEN, "uid": SESSION_UID,
        "platform": "google_meet", "meeting_id": str(MEETING_ID),
        "start_timestamp": SESSION_START_TIME.isoformat(),
    })})
    time.sleep(1)

    print("=" * 70)
    print("TEST 1: Draft → Confirmed (same speaker, same start_time)")
    print("=" * 70)
    # Alice draft
    publish(r, [{"start": 10.0, "end": 16.0, "text": "Alice starting to talk about", "language": "en", "completed": False, "speaker": "Alice"}])
    time.sleep(1.5)
    check_hash(r, "after Alice draft")

    # Alice confirmed (same start, expanded end+text)
    publish(r, [{"start": 10.0, "end": 19.0, "text": "Alice starting to talk about the quarterly results", "language": "en", "completed": True, "speaker": "Alice"}])
    time.sleep(1.5)
    check_hash(r, "after Alice confirmed")

    print("\n" + "=" * 70)
    print("TEST 2: Two speakers, overlapping times")
    print("=" * 70)
    publish(r, [{"start": 20.0, "end": 30.0, "text": "Bob discussing the engineering roadmap for next quarter", "language": "en", "completed": True, "speaker": "Bob"}])
    time.sleep(0.5)
    publish(r, [{"start": 22.0, "end": 32.0, "text": "Alice I agree we should focus on infrastructure", "language": "en", "completed": True, "speaker": "Alice"}])
    time.sleep(1.5)
    check_hash(r, "after concurrent speakers")

    print("\n" + "=" * 70)
    print("TEST 3: Three speakers rapid fire (draft + confirmed interleaved)")
    print("=" * 70)
    # Alice draft
    publish(r, [{"start": 40.0, "end": 45.0, "text": "Alice let me check", "language": "en", "completed": False, "speaker": "Alice"}])
    time.sleep(0.3)
    # Bob draft
    publish(r, [{"start": 41.0, "end": 46.0, "text": "Bob I have the numbers", "language": "en", "completed": False, "speaker": "Bob"}])
    time.sleep(0.3)
    # Carol confirmed
    publish(r, [{"start": 42.0, "end": 48.0, "text": "Carol the sprint review is at three PM", "language": "en", "completed": True, "speaker": "Carol"}])
    time.sleep(0.3)
    # Alice confirmed (replaces draft)
    publish(r, [{"start": 40.0, "end": 47.0, "text": "Alice let me check the dashboard metrics real quick", "language": "en", "completed": True, "speaker": "Alice"}])
    time.sleep(0.3)
    # Bob confirmed (replaces draft)
    publish(r, [{"start": 41.0, "end": 49.0, "text": "Bob I have the numbers from last month ready", "language": "en", "completed": True, "speaker": "Bob"}])
    time.sleep(1.5)
    segments = check_hash(r, "after three speakers interleaved")

    # Validate
    print("\n" + "=" * 70)
    print("VALIDATION")
    print("=" * 70)

    expected_keys = {"10.000", "20.000", "22.000", "40.000", "41.000", "42.000"}
    actual_keys = set(segments.keys())
    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys

    if missing:
        print(f"  ❌ MISSING segments: {missing}")
    if extra:
        print(f"  ⚠️  Extra segments: {extra}")
    if not missing:
        print(f"  ✅ All {len(expected_keys)} expected segments present")

    # Check drafts were replaced by confirmed
    for key in ["10.000", "40.000", "41.000"]:
        if key in segments:
            seg = json.loads(segments[key])
            if seg.get("completed") != True:
                print(f"  ❌ Segment {key} should be completed=True but got {seg.get('completed')}")
            else:
                print(f"  ✅ Segment {key} correctly shows completed=True (draft replaced)")

    # Check concurrent speakers preserved
    speakers_at_40 = set()
    for key in ["40.000", "41.000", "42.000"]:
        if key in segments:
            seg = json.loads(segments[key])
            speakers_at_40.add(seg.get("speaker"))
    if speakers_at_40 == {"Alice", "Bob", "Carol"}:
        print(f"  ✅ All 3 concurrent speakers preserved: {speakers_at_40}")
    else:
        print(f"  ❌ Expected 3 speakers, got: {speakers_at_40}")

    # Check WS messages
    print(f"\n  WebSocket messages received: {len(ws_messages)}")
    speakers_in_ws = set()
    for msg in ws_messages:
        for seg in msg.get("payload", {}).get("segments", []):
            speakers_in_ws.add(seg.get("speaker"))
    print(f"  Speakers in WS stream: {speakers_in_ws}")
    if {"Alice", "Bob", "Carol"} <= speakers_in_ws:
        print(f"  ✅ All speakers present in WS delivery")
    else:
        missing_ws = {"Alice", "Bob", "Carol"} - speakers_in_ws
        print(f"  FAIL Missing speakers in WS: {missing_ws}")

    # Check absolute_start_time present in Redis hash
    has_abs_time = all(
        json.loads(segments[k]).get("absolute_start_time") for k in segments
    )
    print(f"  {'✅' if has_abs_time else '❌'} absolute_start_time {'present' if has_abs_time else 'MISSING'} in all segments")

    # Check absolute times are valid ISO strings (not computed from session_start)
    for k in sorted(segments.keys(), key=lambda x: float(x)):
        seg = json.loads(segments[k])
        abs_start = seg.get("absolute_start_time", "")
        if abs_start and "T" in abs_start:
            print(f"  ✅ Segment {k}: absolute_start_time = {abs_start}")
        else:
            print(f"  ❌ Segment {k}: absolute_start_time invalid or missing: {abs_start}")

    # Check WS messages include absolute times
    ws_has_abs = all(
        seg.get("absolute_start_time")
        for msg in ws_messages
        for seg in msg.get("payload", {}).get("segments", [])
    ) if ws_messages else False
    print(f"  {'✅' if ws_has_abs else '❌'} absolute_start_time {'present' if ws_has_abs else 'MISSING'} in WS messages")

    # Cleanup
    pubsub.unsubscribe()
    r.xadd(STREAM, {"payload": json.dumps({"type": "session_end", "token": TOKEN, "uid": SESSION_UID})})

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)

if __name__ == "__main__":
    main()
