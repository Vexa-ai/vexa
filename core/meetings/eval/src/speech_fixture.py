#!/usr/bin/env python3
"""Build a captured-signal.v1 fixture out of REAL speech with KNOWN text.

This closes the "capture gap" named in COUNTING-FIXTURES.md: the counting fixtures start at
stage 2 (STT), so their speaker labels are an assignment rather than something the capture layer
produced. A fixture in `captured-signal.v1` shape starts one stage earlier — raw per-channel PCM
with the glow name on every frame — so replaying it drives the REAL capture → segmentation →
attribution path, and because each turn's text is known, the transcript it produces can be scored
for CONTENT, not just structure.

Input is the TTS clip pool (`~/vexa-test-rig/cache/<S>.json`: `{text, b64 wav, durSec}` per clip),
which is real 16 kHz mono speech whose words are already known. Output is a session JSONL plus a
truth sidecar naming what each turn said.

    python3 src/speech_fixture.py --speakers A,B --turns 4 --out /tmp/speech-2spk

Emits <out>.captured-signal.jsonl (header + 200 ms frames) and <out>.truth.json.
"""
import argparse
import base64
import io
import json
import math
import os
import struct
import wave

CACHE = os.path.expanduser(os.environ.get("EVAL_CACHE", "~/vexa-test-rig/cache"))
SAMPLE_RATE = 16000
FRAME_SAMPLES = 3200  # 200 ms — the frame size the live capture layer emits
NAMES = {"A": "Anna", "B": "Boris", "C": "Galina", "D": "Dmitry",
         "E": "Elena", "F": "Fyodor", "G": "Grigory", "H": "Hanna", "V": "Vera"}


def load_clips(speaker):
    """Real speech clips for one speaker: (known text, int16 mono samples)."""
    with open(os.path.join(CACHE, f"{speaker}.json")) as f:
        clips = json.load(f)
    out = []
    for c in clips:
        w = wave.open(io.BytesIO(base64.b64decode(c["b64"])), "rb")
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{speaker}: clip is not 16k mono s16 "
                             f"({w.getframerate()}Hz {w.getnchannels()}ch {w.getsampwidth()}B)")
        raw = w.readframes(w.getnframes() or (1 << 30))
        out.append((c["text"].strip(), memoryview(raw).cast("h")))
    return out


def frames_of(samples, start_ms, speaker_index, speaker_name, seq0):
    """Slice one turn's PCM into the wire frames the capture layer would have emitted."""
    frames, seq, n = [], seq0, len(samples)
    for off in range(0, n, FRAME_SAMPLES):
        chunk = samples[off:off + FRAME_SAMPLES]
        f32 = struct.pack(f"<{len(chunk)}f", *(s / 32768.0 for s in chunk))
        rms = math.sqrt(sum((s / 32768.0) ** 2 for s in chunk) / max(1, len(chunk)))
        frames.append({
            "seq": seq,
            "ts": start_ms + round(off / SAMPLE_RATE * 1000),
            "speakerIndex": speaker_index,
            "speakerName": speaker_name,
            "pcm": base64.b64encode(f32).decode(),
            "pcm_len": len(chunk),
            "rms": round(rms, 6),
            "lane": "gmeet",
        })
        seq += 1
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speakers", default="A,B", help="clip-pool ids, e.g. A,B")
    ap.add_argument("--turns", type=int, default=4, help="total turns, alternating speakers")
    ap.add_argument("--gap-ms", type=int, default=800, help="silence between turns")
    ap.add_argument("--out", default="/tmp/speech-fixture")
    ap.add_argument("--started-at", default="2026-07-19T12:00:00.000Z")
    args = ap.parse_args()

    speakers = args.speakers.split(",")
    pools = {s: load_clips(s) for s in speakers}

    header = {
        "type": "captured_signal_header", "v": 1, "platform": "google_meet",
        "native_meeting_id": f"speech-{'-'.join(speakers)}-{args.turns}",
        "language": "en", "lane": "gmeet", "sample_rate": SAMPLE_RATE,
        "started_at": args.started_at,
    }

    lines, truth, t_ms, seq = [json.dumps(header)], [], 0, 0
    for turn in range(args.turns):
        sid = speakers[turn % len(speakers)]
        text, samples = pools[sid][turn // len(speakers) % len(pools[sid])]
        idx = speakers.index(sid)
        fr = frames_of(samples, t_ms, idx, NAMES.get(sid, sid), seq)
        lines += [json.dumps(f) for f in fr]
        dur_ms = round(len(samples) / SAMPLE_RATE * 1000)
        truth.append({"turn": turn, "speakerIndex": idx, "speaker": NAMES.get(sid, sid),
                      "text": text, "startMs": t_ms, "endMs": t_ms + dur_ms})
        seq += len(fr)
        t_ms += dur_ms + args.gap_ms

    sig_path, truth_path = f"{args.out}.captured-signal.jsonl", f"{args.out}.truth.json"
    with open(sig_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(truth_path, "w") as f:
        json.dump({"header": header, "turns": truth}, f, indent=2)

    words = sum(len(t["text"].split()) for t in truth)
    print(f"{sig_path}\n  {len(lines)-1} frames · {len(truth)} turns · "
          f"{t_ms/1000:.1f}s · {words} known words · speakers {','.join(sorted({t['speaker'] for t in truth}))}")
    print(f"{truth_path}")


if __name__ == "__main__":
    main()
