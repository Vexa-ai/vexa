#!/usr/bin/env python3
"""
Meeting simulation script.

Reads YouTube auto-generated captions (json3 format) and publishes them
to Redis pub/sub in the same format as the transcription-collector,
simulating a live meeting for the decision-listener.

Usage:
    python simulate_meeting.py <captions.json3> [--meeting-id SIM-001] [--speed 10] [--batch-size 5]

The script:
  1. Parses json3 captions into sentence-level segments
  2. Groups them into batches (simulating how the real transcription-collector delivers)
  3. Publishes each batch to Redis channel tc:meeting:{meeting_id}:mutable
  4. Waits proportionally between batches (real time / speed factor)
"""
import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis


def parse_json3(path: str) -> list[dict]:
    """Parse YouTube json3 captions into text segments with timestamps."""
    with open(path) as f:
        data = json.load(f)

    events = data.get("events", [])
    segments = []

    for ev in events:
        segs = ev.get("segs", [])
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue

        start_ms = ev.get("tStartMs", 0)
        dur_ms = ev.get("dDurationMs", 0)

        segments.append(
            {
                "start_ms": start_ms,
                "duration_ms": dur_ms,
                "text": text,
            }
        )

    return segments


def group_into_sentences(raw_segments: list[dict], max_words: int = 30) -> list[dict]:
    """
    Merge small caption fragments into sentence-level segments.
    YouTube auto-captions are typically 3-8 words each; we merge until
    we hit punctuation or max_words.
    """
    sentences = []
    current_words = []
    current_start = None

    for seg in raw_segments:
        if current_start is None:
            current_start = seg["start_ms"]

        words = seg["text"].split()
        current_words.extend(words)
        current_end_ms = seg["start_ms"] + seg["duration_ms"]

        # Check if we should flush
        text_so_far = " ".join(current_words)
        ends_sentence = bool(re.search(r"[.!?]$", text_so_far))
        long_enough = len(current_words) >= max_words

        if ends_sentence or long_enough:
            sentences.append(
                {
                    "start_ms": current_start,
                    "end_ms": current_end_ms,
                    "text": text_so_far,
                }
            )
            current_words = []
            current_start = None

    # Flush remaining
    if current_words and current_start is not None:
        sentences.append(
            {
                "start_ms": current_start,
                "end_ms": raw_segments[-1]["start_ms"] + raw_segments[-1]["duration_ms"],
                "text": " ".join(current_words),
            }
        )

    return sentences


def assign_speakers(sentences: list[dict]) -> list[dict]:
    """
    Heuristic speaker assignment for auto-captions that lack speaker IDs.
    Assigns speakers based on pauses between segments.
    In a real system, diarization would handle this.
    """
    # For this meeting, we know it's a staff meeting with multiple speakers
    # Use gaps > 2s as potential speaker changes
    speakers = [
        "Speaker 1",
        "Speaker 2",
        "Speaker 3",
        "Speaker 4",
        "Speaker 5",
    ]
    current_speaker_idx = 0
    last_end_ms = 0

    for seg in sentences:
        gap_ms = seg["start_ms"] - last_end_ms
        if gap_ms > 2000:
            # Potential speaker change
            current_speaker_idx = (current_speaker_idx + 1) % len(speakers)

        seg["speaker"] = speakers[current_speaker_idx]
        last_end_ms = seg["end_ms"]

    return sentences


def build_transcript_segments(
    sentences: list[dict], meeting_id: str
) -> list[dict]:
    """
    Convert parsed sentences into the segment format expected by
    the transcription-collector pub/sub channel.
    """
    # Use a fixed base time for absolute timestamps
    base_time = datetime.now(timezone.utc)

    segments = []
    for i, sent in enumerate(sentences):
        start_sec = sent["start_ms"] / 1000.0
        end_sec = sent["end_ms"] / 1000.0

        # Compute absolute start time
        from datetime import timedelta

        abs_start = base_time + timedelta(seconds=start_sec)

        segments.append(
            {
                "start": start_sec,
                "end": end_sec,
                "start_time": start_sec,
                "text": sent["text"],
                "speaker": sent["speaker"],
                "language": "en",
                "absolute_start_time": abs_start.isoformat(),
            }
        )

    return segments


async def run_simulation(
    captions_path: str,
    meeting_id: str,
    speed: float,
    batch_size: int,
    redis_host: str,
    redis_port: int,
):
    """Main simulation loop."""
    print(f"{'='*60}")
    print(f"Meeting Simulation")
    print(f"{'='*60}")
    print(f"  Captions:   {captions_path}")
    print(f"  Meeting ID: {meeting_id}")
    print(f"  Speed:      {speed}x")
    print(f"  Batch size: {batch_size} segments per publish")
    print(f"  Redis:      {redis_host}:{redis_port}")
    print(f"{'='*60}")

    # Parse captions
    print("\n[1/3] Parsing captions...")
    raw_segments = parse_json3(captions_path)
    print(f"  Raw caption fragments: {len(raw_segments)}")

    sentences = group_into_sentences(raw_segments)
    print(f"  Merged sentences: {len(sentences)}")

    sentences = assign_speakers(sentences)
    speakers = set(s["speaker"] for s in sentences)
    print(f"  Speakers detected: {len(speakers)} ({', '.join(sorted(speakers))})")

    segments = build_transcript_segments(sentences, meeting_id)

    duration_sec = segments[-1]["end"] if segments else 0
    print(f"  Duration: {duration_sec/60:.1f} min")
    print(f"  Simulated duration: {duration_sec/speed/60:.1f} min")

    # Connect to Redis
    print("\n[2/3] Connecting to Redis...")
    r = aioredis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    try:
        await r.ping()
        print("  Connected!")
    except Exception as e:
        print(f"  ERROR: Cannot connect to Redis: {e}")
        sys.exit(1)

    channel = f"tc:meeting:{meeting_id}:mutable"
    print(f"  Publishing to: {channel}")

    # Clear any existing decisions for this meeting
    decisions_key = f"meeting:{meeting_id}:decisions"
    await r.delete(decisions_key)
    print(f"  Cleared existing decisions for {meeting_id}")

    # Publish segments in batches
    print(f"\n[3/3] Starting simulation...")
    print(f"  Publishing {len(segments)} segments in batches of {batch_size}")
    print(f"  {'─'*50}")

    total_batches = (len(segments) + batch_size - 1) // batch_size
    start_time = time.monotonic()
    last_segment_time = 0.0

    # Minimum delay between batches — must be > debounce (0.8s) + LLM call time (~1-2s)
    # to ensure the decision-listener processes each window
    min_batch_delay = 3.0  # seconds

    for batch_idx in range(0, len(segments), batch_size):
        batch = segments[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1

        # Always wait at least min_batch_delay between publishes
        # This ensures the decision-listener debounce window passes
        # and the LLM has time to process each window
        if batch_idx > 0:
            first_seg_time = batch[0]["start"]
            realtime_gap = (first_seg_time - last_segment_time) / speed
            wait_sec = max(realtime_gap, min_batch_delay)
            await asyncio.sleep(wait_sec)

        last_segment_time = batch[-1]["end"]

        # Build the pub/sub payload (same format as transcription-collector)
        payload = {
            "event": "segments_updated",
            "meeting_id": meeting_id,
            "payload": {
                "segments": batch,
            },
        }

        await r.publish(channel, json.dumps(payload))

        # Print progress
        elapsed = time.monotonic() - start_time
        sim_time_sec = batch[-1]["end"]
        sim_mins = int(sim_time_sec // 60)
        sim_secs = int(sim_time_sec % 60)

        preview = batch[-1]["text"][:60]
        speaker = batch[-1]["speaker"]
        print(
            f"  [{sim_mins:02d}:{sim_secs:02d}] batch {batch_num:3d}/{total_batches} "
            f"({len(batch)} segs) | {speaker}: {preview}..."
        )

    elapsed_total = time.monotonic() - start_time
    print(f"  {'─'*50}")
    print(f"\n  Simulation complete!")
    print(f"  Real time elapsed: {elapsed_total:.1f}s")
    print(f"  Meeting time covered: {duration_sec/60:.1f} min")
    print(f"  Effective speed: {duration_sec/elapsed_total:.1f}x")

    # Show what was detected
    print(f"\n{'='*60}")
    print("Checking detected items...")
    items_raw = await r.lrange(decisions_key, 0, -1)
    items = [json.loads(r) for r in items_raw]
    print(f"  Total items detected: {len(items)}")
    for item in items:
        etype = item.get("type", "?")
        summary = item.get("summary", "")[:80]
        speaker = item.get("speaker", "?")
        conf = item.get("confidence", 0)
        entities = item.get("entities", [])
        ent_labels = [f"{e['type']}:{e['label']}" for e in entities[:3]]
        ent_str = f" | entities: [{', '.join(ent_labels)}]" if ent_labels else ""
        print(f"  [{etype:25s}] ({conf:.0%}) {speaker}: {summary}{ent_str}")

    await r.aclose()
    print(f"\n{'='*60}")
    print("Done! Open the dashboard to see results in the Anthology tab.")
    print(f"Meeting ID: {meeting_id}")
    print(f"URL: http://localhost:3001/meetings/{meeting_id}")


def main():
    parser = argparse.ArgumentParser(description="Simulate a live meeting from YouTube captions")
    parser.add_argument("captions", help="Path to YouTube json3 captions file")
    parser.add_argument("--meeting-id", default="SIM-001", help="Meeting ID to use (default: SIM-001)")
    parser.add_argument("--speed", type=float, default=10.0, help="Speedup factor (default: 10x)")
    parser.add_argument("--batch-size", type=int, default=5, help="Segments per batch (default: 5)")
    parser.add_argument("--redis-host", default="localhost", help="Redis host (default: localhost)")
    parser.add_argument("--redis-port", type=int, default=6379, help="Redis port (default: 6379)")
    args = parser.parse_args()

    asyncio.run(
        run_simulation(
            captions_path=args.captions,
            meeting_id=args.meeting_id,
            speed=args.speed,
            batch_size=args.batch_size,
            redis_host=args.redis_host,
            redis_port=args.redis_port,
        )
    )


if __name__ == "__main__":
    main()
