#!/usr/bin/env python3
"""Render a conversation script → 16 kHz mono WAV + ground-truth JSON.

Supports two TTS backends (top-level `tts` field on the script):

  - "piper" (default): each speaker has `piper_voice` naming a model under
    `eval/tts-voices/`. Uses `eval/tts-bin/piper/piper`.
  - "espeak": each speaker has `voice`/`espeak_pitch`/`espeak_speed`.

Per-turn fields:
  - `pause_after_ms` (int, default 0): silence inserted AFTER this turn.
  - `overlap_with_prev_ms` (int, default 0): this turn's audio starts EARLY
    by this many ms, overlapping the tail of the previous turn. Used for
    realistic conversation interjections ("yeah", "right", "exactly")
    where the next speaker begins before the previous one finishes.
    When set, `pause_after_ms` on the PREVIOUS turn is ignored.

Audio mixing model:
  - Working buffer is int32 (so overlap = add without clip).
  - On final write, clamp each sample back to int16.
  - Soft scaling (-6 dB) is applied to OVERLAPPING regions so the mixed
    audio doesn't sound twice as loud during interjections.

Stdlib + ffmpeg + (piper OR espeak-ng) — no pip installs.

Usage:
    eval/render.py eval/conversations/<script>.json
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # int16
INT16_MAX = 32_767
INT16_MIN = -32_768
# Per-track gain applied during overlap regions so mixed speech doesn't clip.
# -6 dB = ×0.5; -3 dB ≈ ×0.707. Use -3 dB so each voice stays clearly audible.
OVERLAP_GAIN = 0.707

HERE = Path(__file__).resolve().parent
PIPER_BIN = HERE / "tts-bin" / "piper" / "piper"
PIPER_VOICES = HERE / "tts-voices"


# ───────────────────────────────────────────────────────────── TTS backends

def _run_ffmpeg_to_pcm(input_wav: str) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_wav,
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-f", "s16le",
            "-loglevel", "error",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return proc.stdout


def synthesize_with_piper(text: str, voice_name: str) -> bytes:
    voice_onnx = PIPER_VOICES / f"{voice_name}.onnx"
    voice_json = PIPER_VOICES / f"{voice_name}.onnx.json"
    if not voice_onnx.is_file():
        raise SystemExit(f"piper voice not found: {voice_onnx}")
    if not PIPER_BIN.is_file():
        raise SystemExit(f"piper binary not found at {PIPER_BIN}")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as raw:
        raw_path = raw.name
    try:
        subprocess.run(
            [str(PIPER_BIN), "--model", str(voice_onnx), "--output_file", raw_path],
            input=text.encode("utf-8"),
            check=True,
            capture_output=True,
        )
        return _run_ffmpeg_to_pcm(raw_path)
    finally:
        Path(raw_path).unlink(missing_ok=True)


def synthesize_with_espeak(text: str, voice: str, pitch: int, speed: int) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as raw:
        raw_path = raw.name
    try:
        subprocess.run(
            [
                "espeak-ng",
                "-v", voice,
                "-p", str(pitch),
                "-s", str(speed),
                "-w", raw_path,
                text,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _run_ffmpeg_to_pcm(raw_path)
    finally:
        Path(raw_path).unlink(missing_ok=True)


def synthesize_turn(text: str, speaker_cfg: dict, tts_backend: str) -> bytes:
    if tts_backend == "piper":
        return synthesize_with_piper(text, speaker_cfg["piper_voice"])
    if tts_backend == "espeak":
        return synthesize_with_espeak(
            text,
            voice=speaker_cfg["voice"],
            pitch=int(speaker_cfg.get("espeak_pitch", 50)),
            speed=int(speaker_cfg.get("espeak_speed", 175)),
        )
    raise SystemExit(f"unknown tts backend: {tts_backend!r}")


# ───────────────────────────────────────────────────────── int16/int32 helpers

def int16_bytes_to_samples(pcm: bytes) -> list[int]:
    n = len(pcm) // 2
    return list(struct.unpack(f"<{n}h", pcm))


def samples_int32_to_bytes(samples: list[int]) -> bytes:
    """Clamp int32 → int16 and pack."""
    out = bytearray(len(samples) * 2)
    for i, v in enumerate(samples):
        if v > INT16_MAX:
            v = INT16_MAX
        elif v < INT16_MIN:
            v = INT16_MIN
        struct.pack_into("<h", out, i * 2, v)
    return bytes(out)


def mix_into(buf: list[int], start_sample: int, turn_samples: list[int], gain: float) -> int:
    """Mix `turn_samples` into `buf` starting at `start_sample`. Returns
    the (exclusive) end-sample index. Extends `buf` with zeros as needed."""
    end_sample = start_sample + len(turn_samples)
    if end_sample > len(buf):
        buf.extend([0] * (end_sample - len(buf)))
    if gain == 1.0:
        for i, s in enumerate(turn_samples):
            buf[start_sample + i] += s
    else:
        for i, s in enumerate(turn_samples):
            buf[start_sample + i] += int(round(s * gain))
    return end_sample


# ───────────────────────────────────────────────────────────────── main loop

def render(script_path: Path, out_dir: Path) -> None:
    script = json.loads(script_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    script_id = script["id"]
    tts_backend = script.get("tts", "piper")
    print(f"[render] backend={tts_backend}", flush=True)

    speakers_cfg = script["speakers"]
    mix_buffer: list[int] = []
    truth: list[dict] = []
    # Sample index where the most-recently-placed turn ends (NOT counting the
    # post-pause). Used as the anchor for the NEXT turn's overlap calc.
    last_turn_end_sample = 0
    last_turn_pause_after_ms = 0

    for i, turn in enumerate(script["turns"]):
        speaker_key = turn["speaker"]
        if speaker_key not in speakers_cfg:
            raise SystemExit(f"turn {i}: unknown speaker {speaker_key!r}")
        cfg = speakers_cfg[speaker_key]
        pcm = synthesize_turn(turn["text"], cfg, tts_backend)
        samples = int16_bytes_to_samples(pcm)
        n_samples = len(samples)

        overlap_ms = int(turn.get("overlap_with_prev_ms", 0))
        if overlap_ms > 0 and i > 0:
            # Start this turn EARLY into the previous one's tail.
            overlap_samples = int(SAMPLE_RATE * overlap_ms / 1000)
            start_sample = max(0, last_turn_end_sample - overlap_samples)
            # During the overlap window, this turn is mixed (attenuated).
            # Beyond the overlap, full-amplitude.
            overlap_end_sample = min(start_sample + overlap_samples, last_turn_end_sample)
            overlap_n = overlap_end_sample - start_sample
            if overlap_n > 0:
                # Scale the existing buffer's overlap region too (so both
                # voices sound roughly equal during the mixed window).
                for s in range(start_sample, overlap_end_sample):
                    mix_buffer[s] = int(round(mix_buffer[s] * OVERLAP_GAIN))
                mix_into(mix_buffer, start_sample, samples[:overlap_n], OVERLAP_GAIN)
                mix_into(mix_buffer, overlap_end_sample, samples[overlap_n:], 1.0)
            else:
                mix_into(mix_buffer, start_sample, samples, 1.0)
        else:
            # Append after the previous turn's pause (or at 0).
            pause_samples = int(SAMPLE_RATE * last_turn_pause_after_ms / 1000) if i > 0 else 0
            start_sample = last_turn_end_sample + pause_samples
            mix_into(mix_buffer, start_sample, samples, 1.0)

        end_sample = start_sample + n_samples
        start_ms = round(start_sample * 1000.0 / SAMPLE_RATE, 1)
        end_ms = round(end_sample * 1000.0 / SAMPLE_RATE, 1)

        truth.append({
            "speaker": speaker_key,
            "text": turn["text"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": round(n_samples * 1000.0 / SAMPLE_RATE, 1),
            "overlap_with_prev_ms": overlap_ms,
        })
        voice_tag = cfg.get("piper_voice") or cfg.get("voice") or "?"
        overlap_note = f"  (overlap={overlap_ms}ms)" if overlap_ms > 0 else ""
        print(
            f"[render] turn {i}: {speaker_key} ({voice_tag}) "
            f"{n_samples / SAMPLE_RATE:.2f}s → {start_ms / 1000:.2f}–{end_ms / 1000:.2f}s{overlap_note}",
            flush=True,
        )
        last_turn_end_sample = end_sample
        last_turn_pause_after_ms = int(turn.get("pause_after_ms", 0))

    total_samples = len(mix_buffer)
    wav_path = out_dir / f"{script_id}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(BYTES_PER_SAMPLE)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples_int32_to_bytes(mix_buffer))

    truth_path = out_dir / f"{script_id}.ground-truth.json"
    truth_path.write_text(
        json.dumps(
            {
                "id": script_id,
                "tts": tts_backend,
                "sample_rate": SAMPLE_RATE,
                "total_duration_ms": round(total_samples * 1000.0 / SAMPLE_RATE, 1),
                "turns": truth,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"[render] wrote {wav_path} ({total_samples / SAMPLE_RATE:.2f}s @ {SAMPLE_RATE}Hz mono)")
    print(f"[render] wrote {truth_path} ({len(truth)} turns)")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render.py <conversation.json>", file=sys.stderr)
        return 2
    script_path = Path(sys.argv[1])
    if not script_path.is_file():
        print(f"no such file: {script_path}", file=sys.stderr)
        return 2
    out_dir = script_path.parent.parent / "corpus"
    render(script_path, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
