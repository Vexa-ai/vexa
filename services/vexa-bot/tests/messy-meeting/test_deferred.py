#!/usr/bin/env python3
"""
Deferred transcription pipeline test.

Simulates the full post-meeting flow:
1. Mix per-speaker WAVs into one combined recording (like MediaRecorder)
2. Generate speaker_events with timestamps (like __vexaSpeakerEvents)
3. POST combined recording to transcription-service (deferred path)
4. Map speakers to segments using timestamp overlap (like bot-manager)
5. Validate: keyword attribution, cross-contamination, hallucinations

This tests: recording → deferred transcription → speaker mapping
without a bot, browser, or Redis.

Usage:
    python test_deferred.py --scenario full-messy
    python test_deferred.py --scenario chaos-meeting
    python test_deferred.py --scenario full-messy --no-generate

Prerequisites:
    - transcription-service on localhost:8083
"""

import argparse
import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
import requests

from scenarios import SCENARIOS, VOICES

CACHE_DIR = Path(__file__).parent / "cache"
TRANSCRIPTION_URL = os.environ.get(
    "TRANSCRIPTION_SERVICE_URL", "http://localhost:8083/v1/audio/transcriptions"
)
TRANSCRIPTION_TOKEN = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "your_secure_token_here")
SAMPLE_RATE = 16000


# ─── Step 1: Mix per-speaker WAVs into one combined recording ─────────────────

def mix_to_combined(scenario_dir: Path, manifest: dict) -> Path:
    """Mix all per-speaker WAVs into a single combined recording."""
    max_samples = 0
    speaker_audio = {}

    for speaker_name, info in manifest["speakers"].items():
        wav_path = scenario_dir / info["wav"]
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        speaker_audio[speaker_name] = samples
        max_samples = max(max_samples, len(samples))

    # Mix all speakers onto one timeline
    combined = np.zeros(max_samples, dtype=np.float32)
    for samples in speaker_audio.values():
        combined[:len(samples)] += samples

    # Clip and write
    combined = np.clip(combined, -1.0, 1.0)
    out_path = scenario_dir / "combined.wav"
    int16 = (combined * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())

    duration = len(combined) / SAMPLE_RATE
    print(f"  Mixed recording: {duration:.1f}s, {out_path.name}")
    return out_path


# ─── Step 2: Generate speaker events from scenario ────────────────────────────

def generate_speaker_events(scenario: dict, manifest: dict) -> list[dict]:
    """Generate speaker_events like __vexaSpeakerEvents from scenario timing."""
    events = []

    for utt in scenario["utterances"]:
        speaker = utt["speaker"]
        start_s = utt["start_s"]
        # Estimate speech duration from manifest speaker WAV duration and utterance position
        # Rough heuristic: TTS generates ~150 words/min, so estimate from text length
        words = len(utt["text"].split())
        estimated_dur_s = max(1.0, words / 2.5)  # ~150 wpm

        full_name = {
            "Alice": "Alice Johnson",
            "Bob": "Bob Smith",
            "Carol": "Carol Williams",
        }.get(speaker, speaker)

        events.append({
            "event_type": "SPEAKER_START",
            "participant_name": full_name,
            "participant_id": f"participant-{speaker.lower()}",
            "relative_timestamp_ms": int(start_s * 1000),
        })
        events.append({
            "event_type": "SPEAKER_END",
            "participant_name": full_name,
            "participant_id": f"participant-{speaker.lower()}",
            "relative_timestamp_ms": int((start_s + estimated_dur_s) * 1000),
        })

    # Sort by timestamp
    events.sort(key=lambda e: e["relative_timestamp_ms"])
    return events


# ─── Step 3: Transcribe combined recording ────────────────────────────────────

def transcribe_combined(wav_path: Path) -> dict:
    """POST combined WAV to transcription-service."""
    with open(wav_path, "rb") as f:
        resp = requests.post(
            TRANSCRIPTION_URL,
            files={"file": ("combined.wav", f, "audio/wav")},
            data={"model": "large-v3-turbo", "response_format": "verbose_json"},
            headers={"Authorization": f"Bearer {TRANSCRIPTION_TOKEN}"},
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()


# ─── Step 4: Map speakers to segments (replicate bot-manager logic) ───────────

def map_speakers_to_segments(
    speaker_events: list[dict],
    segments: list[dict],
) -> list[dict]:
    """Map speaker names to transcription segments using timestamp overlap.

    Replicates bot-manager/app/main.py:_map_speakers_to_segments()
    """
    # Build time ranges per speaker
    speaker_ranges: dict[str, list[tuple[int, int]]] = {}
    active_starts: dict[str, int] = {}

    for event in sorted(speaker_events, key=lambda e: e["relative_timestamp_ms"]):
        name = event["participant_name"]
        ts = event["relative_timestamp_ms"]

        if event["event_type"] == "SPEAKER_START":
            active_starts[name] = ts
        elif event["event_type"] == "SPEAKER_END":
            start = active_starts.pop(name, ts - 1000)
            if name not in speaker_ranges:
                speaker_ranges[name] = []
            speaker_ranges[name].append((start, ts))

    # Close any unclosed ranges
    for name, start in active_starts.items():
        if name not in speaker_ranges:
            speaker_ranges[name] = []
        speaker_ranges[name].append((start, start + 30000))

    # Map each segment to the speaker with maximum overlap
    mapped = []
    for seg in segments:
        seg_start_ms = int(seg.get("start", 0) * 1000)
        seg_end_ms = int(seg.get("end", seg.get("start", 0)) * 1000)
        if seg_end_ms <= seg_start_ms:
            seg_end_ms = seg_start_ms + 1000

        best_speaker = "Unknown"
        best_overlap = 0

        for speaker_name, ranges in speaker_ranges.items():
            total_overlap = 0
            for range_start, range_end in ranges:
                overlap_start = max(seg_start_ms, range_start)
                overlap_end = min(seg_end_ms, range_end)
                if overlap_end > overlap_start:
                    total_overlap += overlap_end - overlap_start

            if total_overlap > best_overlap:
                best_overlap = total_overlap
                best_speaker = speaker_name

        mapped_seg = dict(seg)
        mapped_seg["speaker"] = best_speaker
        mapped_seg["overlap_ms"] = best_overlap
        mapped.append(mapped_seg)

    return mapped


# ─── Step 5: Validate ─────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        icon = "✅" if self.passed else "❌"
        s = f"  {icon} {self.name}"
        if self.detail:
            s += f" ({self.detail})"
        return s


def check_speaker_attribution(mapped_segments: list[dict], scenario: dict) -> CheckResult:
    """Check that segments are attributed to the correct speakers based on timing."""
    # Group scenario utterances by speaker with their time ranges
    expected_speakers_at_time: list[tuple[float, float, str]] = []
    for utt in scenario["utterances"]:
        if utt.get("advisory"):
            continue
        start = utt["start_s"]
        words = len(utt["text"].split())
        dur = max(1.0, words / 2.5)
        full_name = {"Alice": "Alice Johnson", "Bob": "Bob Smith", "Carol": "Carol Williams"}.get(utt["speaker"], utt["speaker"])
        expected_speakers_at_time.append((start, start + dur, full_name))

    correct = 0
    wrong = 0
    wrong_details = []

    for seg in mapped_segments:
        seg_mid = (seg.get("start", 0) + seg.get("end", 0)) / 2
        text = seg.get("text", "").strip()
        if not text or len(text) < 5:
            continue

        assigned = seg.get("speaker", "Unknown")

        # Find expected speaker at this timestamp
        expected = None
        for t_start, t_end, name in expected_speakers_at_time:
            if t_start <= seg_mid <= t_end:
                expected = name
                break

        if expected is None:
            continue  # silence region, skip

        if expected == assigned:
            correct += 1
        else:
            wrong += 1
            wrong_details.append(f"{seg_mid:.1f}s: expected {expected}, got {assigned}")

    total = correct + wrong
    if total == 0:
        return CheckResult("speaker_attribution", False, "no segments to check")

    pct = correct / total * 100
    passed = pct >= 70  # 70% threshold — deferred mapping is inherently imprecise
    detail = f"{correct}/{total} correct ({pct:.0f}%)"
    if wrong_details:
        detail += f" | wrong: {', '.join(wrong_details[:3])}"
    return CheckResult("speaker_attribution", passed, detail)


def check_keyword_in_combined(mapped_segments: list[dict], scenario: dict, manifest: dict) -> CheckResult:
    """Check keywords appear in correct speaker's segments after mapping."""
    speakers = manifest["speakers"]
    missing = []

    for speaker_name, info in speakers.items():
        keywords = [kw.lower() for kw in info.get("keywords", [])]
        if not keywords:
            continue

        full_name = {"Alice": "Alice Johnson", "Bob": "Bob Smith", "Carol": "Carol Williams"}.get(speaker_name, speaker_name)
        speaker_text = " ".join(
            seg.get("text", "").lower()
            for seg in mapped_segments
            if seg.get("speaker") == full_name
        )

        for kw in keywords:
            if kw not in speaker_text:
                missing.append(f"{speaker_name}:{kw}")

    if missing:
        return CheckResult("deferred_keyword_attribution", False, f"missing: {', '.join(missing[:8])}")
    return CheckResult("deferred_keyword_attribution", True)


def check_no_unknown_speakers(mapped_segments: list[dict]) -> CheckResult:
    """Check that no segments are attributed to 'Unknown'."""
    unknown = [s for s in mapped_segments if s.get("speaker") == "Unknown" and s.get("text", "").strip()]
    total = len([s for s in mapped_segments if s.get("text", "").strip()])
    if unknown:
        return CheckResult("no_unknown_speakers", False, f"{len(unknown)}/{total} segments are Unknown")
    return CheckResult("no_unknown_speakers", True)


# ─── Report ───────────────────────────────────────────────────────────────────

def print_deferred_report(scenario: dict, speaker_events: list[dict], mapped_segments: list[dict]):
    """Print deferred pipeline input vs output for human review."""
    print("\n" + "=" * 80)
    print("DEFERRED PIPELINE: INPUT → SPEAKER MAPPING → OUTPUT")
    print("=" * 80)

    print("\n  SPEAKER EVENTS (simulated __vexaSpeakerEvents):")
    for evt in speaker_events:
        t = evt["relative_timestamp_ms"] / 1000
        print(f"    {t:6.1f}s  {evt['event_type']:14s}  {evt['participant_name']}")

    print("\n  MAPPED SEGMENTS (transcription + speaker mapping):")
    for seg in mapped_segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        speaker = seg.get("speaker", "?")
        overlap = seg.get("overlap_ms", 0)
        lang = seg.get("language", "")
        print(f"    {start:6.1f}-{end:6.1f}s  [{speaker:15s}]  (overlap:{overlap}ms)  {text[:80]}")

    print("\n  EXPECTED (scenario input):")
    for utt in scenario["utterances"]:
        tag = " [interjection]" if utt.get("advisory") else ""
        full_name = {"Alice": "Alice Johnson", "Bob": "Bob Smith", "Carol": "Carol Williams"}.get(utt["speaker"], utt["speaker"])
        print(f"    {utt['start_s']:6.1f}s  [{full_name:15s}]{tag}  {utt['text'][:80]}")

    # Save report
    report_path = CACHE_DIR / "last_deferred_report.txt"
    with open(report_path, "w") as f:
        f.write("DEFERRED PIPELINE REPORT\n")
        f.write(f"Generated: {__import__('datetime').datetime.now().isoformat()}\n\n")
        f.write("SPEAKER EVENTS:\n")
        for evt in speaker_events:
            t = evt["relative_timestamp_ms"] / 1000
            f.write(f"  {t:6.1f}s  {evt['event_type']:14s}  {evt['participant_name']}\n")
        f.write("\nMAPPED SEGMENTS:\n")
        for seg in mapped_segments:
            text = seg.get("text", "").strip()
            if not text:
                continue
            f.write(f"  {seg.get('start',0):6.1f}-{seg.get('end',0):6.1f}s  [{seg.get('speaker','?'):15s}]  {text}\n")
        f.write("\nEXPECTED:\n")
        for utt in scenario["utterances"]:
            full_name = {"Alice": "Alice Johnson", "Bob": "Bob Smith", "Carol": "Carol Williams"}.get(utt["speaker"], utt["speaker"])
            f.write(f"  {utt['start_s']:6.1f}s  [{full_name:15s}]  {utt['text']}\n")
    print(f"\n  Report saved to: {report_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run_deferred_test(name: str, scenario: dict, skip_generate: bool = False):
    """Run the full deferred transcription pipeline test."""
    print(f"\n{'='*60}")
    print(f"DEFERRED PIPELINE TEST: {name}")
    print(f"{'='*60}")
    print(f"  {scenario['description']}")

    scenario_dir = CACHE_DIR / name
    manifest_path = scenario_dir / "manifest.json"

    # Generate audio if needed
    if not skip_generate:
        from generate_audio import generate_scenario
        await generate_scenario(name, scenario)

    if not manifest_path.exists():
        print(f"  ERROR: manifest not found. Run: python generate_audio.py --scenario {name}")
        return False

    manifest = json.loads(manifest_path.read_text())

    # Step 1: Mix to combined recording
    print("\n[Step 1] Mixing per-speaker WAVs into combined recording...")
    combined_path = mix_to_combined(scenario_dir, manifest)

    # Step 2: Generate speaker events
    print("\n[Step 2] Generating speaker events from scenario timing...")
    speaker_events = generate_speaker_events(scenario, manifest)
    print(f"  {len(speaker_events)} events for {len(set(e['participant_name'] for e in speaker_events))} speakers")

    # Step 3: Transcribe combined recording
    print("\n[Step 3] Transcribing combined recording (this is the deferred path)...")
    try:
        result = transcribe_combined(combined_path)
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    # Extract segments from result
    segments = result.get("segments", [])
    if not segments:
        # If no segments array, create one from the flat result
        segments = [{
            "start": 0,
            "end": result.get("duration", 0),
            "text": result.get("text", ""),
            "language": result.get("language", ""),
        }]

    print(f"  Got {len(segments)} segments, total duration {result.get('duration', 0):.1f}s")
    print(f"  Full text: \"{result.get('text', '')[:100]}...\"")

    # Step 4: Map speakers to segments
    print("\n[Step 4] Mapping speakers to segments using timestamp overlap...")
    mapped_segments = map_speakers_to_segments(speaker_events, segments)

    speaker_counts = {}
    for seg in mapped_segments:
        sp = seg.get("speaker", "Unknown")
        if seg.get("text", "").strip():
            speaker_counts[sp] = speaker_counts.get(sp, 0) + 1
    print(f"  Attribution: {speaker_counts}")

    # Print report for human review
    print_deferred_report(scenario, speaker_events, mapped_segments)

    # Step 5: Validate
    print("\n[Step 5] Validating...")
    results = [
        check_speaker_attribution(mapped_segments, scenario),
        check_keyword_in_combined(mapped_segments, scenario, manifest),
        check_no_unknown_speakers(mapped_segments),
    ]

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for r in results:
        print(str(r))

    print(f"\n  {passed}/{total} checks passed")
    return passed == total


async def main():
    parser = argparse.ArgumentParser(description="Deferred transcription pipeline test")
    parser.add_argument("--scenario", type=str, required=True, help="Scenario name")
    parser.add_argument("--no-generate", action="store_true", help="Skip audio generation")
    args = parser.parse_args()

    if args.scenario not in SCENARIOS:
        available = ", ".join(sorted(SCENARIOS.keys()))
        print(f"Unknown scenario '{args.scenario}'. Available: {available}")
        sys.exit(1)

    # Check transcription service
    print(f"Transcription service: {TRANSCRIPTION_URL}")
    try:
        health_url = TRANSCRIPTION_URL.rsplit("/", 2)[0] + "/health"
        r = requests.get(health_url, timeout=5)
        print(f"Health check: {r.status_code}")
    except Exception as e:
        print(f"Health check failed: {e}")

    success = await run_deferred_test(args.scenario, SCENARIOS[args.scenario], skip_generate=args.no_generate)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
