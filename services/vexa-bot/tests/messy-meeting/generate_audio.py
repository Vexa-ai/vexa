#!/usr/bin/env python3
"""
Generate messy meeting audio from scenario definitions.

Uses edge-tts for speech synthesis and numpy for signal processing:
- Insert silence gaps between utterances
- Overlay pink noise at specified dB
- Mix overlapping speakers at timed offsets
- Output 16kHz mono 16-bit PCM WAV per speaker + manifest.json

Usage:
    python generate_audio.py --scenario full-messy
    python generate_audio.py --all
    python generate_audio.py --scenario overlap --force
"""

import argparse
import asyncio
import hashlib
import io
import json
import struct
import sys
import wave
from pathlib import Path

import edge_tts
import numpy as np

from scenarios import SCENARIOS, VOICES

SAMPLE_RATE = 16000
CACHE_DIR = Path(__file__).parent / "cache"


def scenario_hash(scenario: dict) -> str:
    """Deterministic hash of scenario definition for cache invalidation."""
    raw = json.dumps(scenario, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


async def synthesize_speech(text: str, voice: str) -> np.ndarray:
    """Generate speech audio using edge-tts, return as float32 numpy array at 16kHz."""
    communicate = edge_tts.Communicate(text, voice)
    audio_bytes = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes += chunk["data"]

    if not audio_bytes:
        raise RuntimeError(f"No audio generated for: {text[:50]}...")

    # edge-tts outputs mp3 — decode to raw PCM
    # Use a subprocess to convert mp3 → wav via ffmpeg (widely available)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", "pipe:0", "-f", "wav", "-ar", str(SAMPLE_RATE),
        "-ac", "1", "-acodec", "pcm_s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    wav_data, stderr = await proc.communicate(input=audio_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:200]}")

    # Parse WAV to numpy
    with io.BytesIO(wav_data) as f:
        with wave.open(f, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    return samples


def generate_pink_noise(num_samples: int) -> np.ndarray:
    """Generate pink noise (1/f) using Voss-McCartney algorithm."""
    # Simple approach: filter white noise
    white = np.random.randn(num_samples).astype(np.float32)
    # Apply simple 1/f filter via cumulative sum + decay
    b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004709510])
    a = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])
    from scipy.signal import lfilter
    pink = lfilter(b, a, white)
    # Normalize to unit RMS
    rms = np.sqrt(np.mean(pink ** 2))
    if rms > 0:
        pink = pink / rms
    return pink


def add_noise(audio: np.ndarray, noise_db: float) -> np.ndarray:
    """Add pink noise at specified dB level relative to signal."""
    noise = generate_pink_noise(len(audio))
    # Calculate scale factor from dB
    signal_rms = np.sqrt(np.mean(audio ** 2))
    if signal_rms == 0:
        return audio
    noise_rms = signal_rms * (10 ** (noise_db / 20))
    return audio + noise * noise_rms


def mix_to_timeline(utterance_audio: list[tuple[float, np.ndarray]]) -> np.ndarray:
    """Mix multiple (start_s, audio) pairs onto a single timeline."""
    if not utterance_audio:
        return np.array([], dtype=np.float32)

    # Find total length needed
    max_end = 0
    for start_s, audio in utterance_audio:
        end = int(start_s * SAMPLE_RATE) + len(audio)
        max_end = max(max_end, end)

    timeline = np.zeros(max_end, dtype=np.float32)
    for start_s, audio in utterance_audio:
        offset = int(start_s * SAMPLE_RATE)
        end = offset + len(audio)
        timeline[offset:end] += audio

    # Clip to prevent overflow
    timeline = np.clip(timeline, -1.0, 1.0)
    return timeline


def write_wav(path: Path, audio: np.ndarray):
    """Write float32 audio as 16kHz mono 16-bit PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())


async def generate_scenario(name: str, scenario: dict, force: bool = False) -> Path:
    """Generate audio files for a scenario. Returns output directory."""
    out_dir = CACHE_DIR / name
    hash_file = out_dir / ".hash"
    current_hash = scenario_hash(scenario)

    # Check cache
    if not force and hash_file.exists() and hash_file.read_text().strip() == current_hash:
        print(f"  [{name}] cached (hash {current_hash})")
        return out_dir

    print(f"  [{name}] generating audio...")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group utterances by speaker
    speaker_utterances: dict[str, list[tuple[float, dict]]] = {}
    for utt in scenario["utterances"]:
        speaker = utt["speaker"]
        if speaker not in speaker_utterances:
            speaker_utterances[speaker] = []
        speaker_utterances[speaker].append((utt["start_s"], utt))

    # Synthesize all utterances
    manifest_speakers = {}
    for speaker, utts in speaker_utterances.items():
        voice = VOICES[speaker]
        audio_segments = []

        for start_s, utt in utts:
            print(f"    {speaker} @ {start_s}s: \"{utt['text'][:50]}...\"")
            audio = await synthesize_speech(utt["text"], voice)

            # Apply noise if specified
            if utt.get("noise_db") is not None:
                audio = add_noise(audio, utt["noise_db"])

            audio_segments.append((start_s, audio))

        # Mix all utterances for this speaker onto one timeline
        timeline = mix_to_timeline(audio_segments)
        wav_path = out_dir / f"{speaker.lower()}.wav"
        write_wav(wav_path, timeline)

        # Collect keywords for manifest
        all_keywords = []
        for _, utt in utts:
            all_keywords.extend(utt.get("keywords", []))

        manifest_speakers[speaker] = {
            "voice": voice,
            "wav": f"{speaker.lower()}.wav",
            "duration_s": round(len(timeline) / SAMPLE_RATE, 2),
            "keywords": all_keywords,
            "language": utts[0][1].get("language", "en"),
            "advisory_utterances": [
                utt["text"] for _, utt in utts if utt.get("advisory")
            ],
        }

    # Write manifest
    manifest = {
        "scenario": name,
        "description": scenario["description"],
        "hash": current_hash,
        "sample_rate": SAMPLE_RATE,
        "speakers": manifest_speakers,
        "checks": scenario["checks"],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    # Write hash for cache
    hash_file.write_text(current_hash)

    total_duration = max(s["duration_s"] for s in manifest_speakers.values())
    print(f"    → {len(manifest_speakers)} speakers, {total_duration:.1f}s total")

    return out_dir


async def main():
    parser = argparse.ArgumentParser(description="Generate messy meeting audio")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", type=str, help="Scenario name")
    group.add_argument("--all", action="store_true", help="Generate all scenarios")
    parser.add_argument("--force", action="store_true", help="Regenerate even if cached")
    args = parser.parse_args()

    if args.all:
        names = sorted(SCENARIOS.keys())
    else:
        if args.scenario not in SCENARIOS:
            available = ", ".join(sorted(SCENARIOS.keys()))
            print(f"Unknown scenario '{args.scenario}'. Available: {available}")
            sys.exit(1)
        names = [args.scenario]

    print(f"Generating {len(names)} scenario(s)...\n")
    for name in names:
        await generate_scenario(name, SCENARIOS[name], force=args.force)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
