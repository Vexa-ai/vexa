#!/usr/bin/env python3
"""
Mock concurrent speaker feed — publishes segments to Redis stream
in the collector's expected format to test dedup behavior.

Simulates two speakers talking simultaneously with overlapping time ranges.
"""
import json
import time
import hmac
import hashlib
import base64
import redis

import os
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM = "transcription_segments"
MEETING_ID = 99900  # test meeting
SESSION_UID = "mock-concurrent-test"

# Mint a test JWT
def mint_jwt(meeting_id, secret="token"):
    def b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps({
        "meeting_id": meeting_id,
        "user_id": 23,
        "platform": "google_meet",
        "native_meeting_id": "mock-test",
        "scope": "transcribe:write",
        "iss": "bot-manager",
        "aud": "transcription-collector",
        "exp": int(time.time()) + 86400,
    }).encode())
    sig = b64url(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

TOKEN = mint_jwt(MEETING_ID)

def publish(r, segments, msg_type="transcription"):
    payload = json.dumps({
        "type": msg_type,
        "token": TOKEN,
        "uid": SESSION_UID,
        "platform": "google_meet",
        "meeting_id": str(MEETING_ID),
        "segments": segments,
    })
    mid = r.xadd(STREAM, {"payload": payload})
    return mid

def main():
    r = redis.from_url(REDIS_URL, decode_responses=True)

    # Session start
    r.xadd(STREAM, {"payload": json.dumps({
        "type": "session_start",
        "token": TOKEN,
        "uid": SESSION_UID,
        "platform": "google_meet",
        "meeting_id": str(MEETING_ID),
        "start_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })})
    print("Published session_start")
    time.sleep(1)

    # === Scenario 1: Two speakers, overlapping time ranges ===
    print("\n=== Scenario 1: Concurrent speakers, overlapping times ===")

    # Alice draft at 10-16s
    publish(r, [{"start": 10.0, "end": 16.0, "text": "Alice talking about the project timeline", "language": "en", "completed": False, "speaker": "Alice"}])
    print("  Alice draft  10-16s")

    # Bob draft at 12-18s (overlaps with Alice)
    publish(r, [{"start": 12.0, "end": 18.0, "text": "Bob discussing the budget constraints", "language": "en", "completed": False, "speaker": "Bob"}])
    print("  Bob draft    12-18s (overlaps Alice)")
    time.sleep(1)

    # Alice confirmed at 10-16s
    publish(r, [{"start": 10.0, "end": 16.0, "text": "Alice talking about the project timeline and next steps", "language": "en", "completed": True, "speaker": "Alice"}])
    print("  Alice conf   10-16s")

    # Bob confirmed at 12-18s
    publish(r, [{"start": 12.0, "end": 18.0, "text": "Bob discussing the budget constraints for Q2", "language": "en", "completed": True, "speaker": "Bob"}])
    print("  Bob conf     12-18s")
    time.sleep(1)

    # === Scenario 2: Same speaker, draft→confirmed (should dedup correctly) ===
    print("\n=== Scenario 2: Same speaker draft→confirmed (normal dedup) ===")

    publish(r, [{"start": 20.0, "end": 26.0, "text": "Carol starting her update", "language": "en", "completed": False, "speaker": "Carol"}])
    print("  Carol draft  20-26s")

    publish(r, [{"start": 20.0, "end": 29.0, "text": "Carol starting her update on the design review", "language": "en", "completed": True, "speaker": "Carol"}])
    print("  Carol conf   20-29s (expands)")
    time.sleep(1)

    # === Scenario 3: Three speakers, all overlapping ===
    print("\n=== Scenario 3: Three concurrent speakers ===")

    publish(r, [{"start": 30.0, "end": 38.0, "text": "Alice I think we should prioritize the backend work", "language": "en", "completed": True, "speaker": "Alice"}])
    print("  Alice conf   30-38s")

    publish(r, [{"start": 31.0, "end": 37.0, "text": "Bob yeah but the frontend needs attention too", "language": "en", "completed": True, "speaker": "Bob"}])
    print("  Bob conf     31-37s (inside Alice)")

    publish(r, [{"start": 32.0, "end": 40.0, "text": "Carol let me check the sprint board for capacity", "language": "en", "completed": True, "speaker": "Carol"}])
    print("  Carol conf   32-40s (overlaps both)")
    time.sleep(2)

    # Check what ended up in the Redis hash
    print("\n=== Results in Redis hash ===")
    hash_key = f"meeting:{MEETING_ID}:segments"
    segments = r.hgetall(hash_key)
    if not segments:
        print("  NO SEGMENTS IN HASH — collector may have filtered everything")
    else:
        for k in sorted(segments.keys(), key=float):
            seg = json.loads(segments[k])
            print(f"  [{k}] {seg.get('speaker', '?'):10s} | completed={seg.get('completed', '?')} | {seg.get('text', '')[:60]}")

    # Session end
    r.xadd(STREAM, {"payload": json.dumps({
        "type": "session_end",
        "token": TOKEN,
        "uid": SESSION_UID,
    })})
    print("\nPublished session_end")

if __name__ == "__main__":
    main()
