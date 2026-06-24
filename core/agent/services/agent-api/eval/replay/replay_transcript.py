#!/usr/bin/env python3
"""replay_transcript.py — drive the live meeting copilot without a real meeting.

XADDs ``transcript.v1`` Transcription segments onto the meeting's redis Stream ``tc:meeting:{id}`` — the
same wire the [bot] produces — paced, then a ``session_end``. The live-meeting dispatch's worker
(``agent_api.worker.serve_meeting``) consumes it by schema, gates, and emits proactive cards on its
Stream. A scripted Acme-renewal call: a NEW person joins, a TOPIC (SSO/SCIM) lands, and an ACTION item
appears — so every salience path fires.

  python -m agent_api.eval.replay.replay_transcript --meeting-id acme-renewal --redis redis://localhost:6379/0
"""
from __future__ import annotations

import argparse
import json
import time

TRANSCRIPT = [
    ("Jane Liu", "Thanks for making time — we want to get the renewal closed this quarter."),
    ("You", "Of course. I'll send updated pricing for the 250-seat tier."),
    ("Raj Patel", "Our security team needs SSO and SCIM provisioning before we can sign."),
    ("Jane Liu", "I'd also like to bring in Priya, our new procurement lead, on the next call."),
    ("Priya Shah", "Hi everyone — I'll own the contract paperwork from our side going forward."),
    ("You", "Great. I'll send the renewal quote by Friday and the SSO docs this week."),
    ("Raj Patel", "Tuesday or Thursday next week works for the technical kickoff."),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting-id", default="acme-renewal")
    ap.add_argument("--session-uid", default="sess-acme-1")
    ap.add_argument("--redis", default="redis://localhost:6379/0")
    ap.add_argument("--gap-ms", type=int, default=1200)
    args = ap.parse_args()

    import redis

    r = redis.from_url(args.redis, decode_responses=True)
    stream = f"tc:meeting:{args.meeting_id}"
    t = 0.0
    for i, (speaker, text) in enumerate(TRANSCRIPT):
        seg = {
            "segment_id": f"{args.session_uid}:{i}", "speaker": speaker, "text": text,
            "start": t, "end": t + 5, "completed": True, "language": "en",
        }
        payload = {
            "type": "transcription", "session_uid": args.session_uid,
            "meeting_id": args.meeting_id, "segments": [seg],
        }
        r.xadd(stream, {"payload": json.dumps(payload)})
        print(f"[{t:5.1f}s] {speaker}: {text}", flush=True)
        t += 5
        time.sleep(args.gap_ms / 1000)
    r.xadd(stream, {"payload": json.dumps({"type": "session_end", "session_uid": args.session_uid})})
    print("session_end", flush=True)


if __name__ == "__main__":
    main()
