#!/usr/bin/env python3
"""Single-pass STT as ground truth for the REAL-TIME pipeline.

The hard part of scoring a live transcript has always been the reference: a human transcript is
expensive, TTS fixtures aren't real meetings, and a mock says nothing about content. But the
reference is obtainable for free — send the SAME audio the pipeline received to the SAME STT
service in one pass, unconstrained by streaming.

That comparison separates the two failure sources that look identical downstream:

  * what the MODEL cannot extract from this audio  → missing from the single-pass reference too
  * what our STREAMING DESIGN throws away          → present in the reference, absent live

Only the second is our bug, and only this comparison isolates it.

    export TRANSCRIPTION_SERVICE_URL=... TRANSCRIPTION_SERVICE_TOKEN=...
    python3 src/single_pass_truth.py <session.captured-signal.jsonl> [--realtime <url|file>]

`--realtime` accepts a desktop transcripts URL (http://localhost:8056/transcripts/<platform>/<id>)
or a JSON file of segments. Without it, only the reference is produced.
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.request
import wave

SR = 16000
CHUNK_SEC = 60.0      # one submission per minute of audio — well inside the service's limits
OVERLAP_SEC = 3.0     # carried across chunk edges so a word split by the cut still appears


def load_session(path):
    """The audio the pipeline actually received, in capture order, plus its wall span."""
    frames = []
    with open(path) as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            d = json.loads(line)
            if i == 0 or not d.get("pcm"):
                continue
            frames.append((d["ts"], base64.b64decode(d["pcm"])))
    frames.sort(key=lambda x: x[0])
    pcm = b"".join(b for _, b in frames)
    span = (frames[-1][0] - frames[0][0]) / 1000 if frames else 0.0
    window = (frames[0][0] / 1000, frames[-1][0] / 1000) if frames else (0.0, 0.0)
    return pcm, span, len(frames), window


def wav_bytes(f32_le: bytes) -> bytes:
    """float32 PCM -> 16-bit WAV (what the service ingests)."""
    import struct
    n = len(f32_le) // 4
    floats = struct.unpack(f"<{n}f", f32_le[: n * 4])
    pcm16 = struct.pack(f"<{n}h", *(max(-32768, min(32767, int(x * 32767))) for x in floats))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm16)
    return buf.getvalue()


def transcribe(wav: bytes, url: str, token: str, model: str, lang: str) -> str:
    b = "----singlepass"
    body = (
        f'--{b}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\nverbose_json\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{lang}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/audio/transcriptions", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={b}",
                 "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return (json.loads(r.read()).get("text") or "").strip()


def words(s):
    return re.findall(r"[\w']+", (s or "").lower())


def lcs(a, b):
    """Order-preserving overlap — a word recovered out of order is not a match."""
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            cur[j] = prev[j - 1] + 1 if a[i - 1] == b[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(b)]


def realtime_words(src, window):
    """The live transcript CLIPPED to the session's own time window.

    A live store keeps growing while a recorded session is a snapshot; scoring the whole store
    against a reference built from part of it counts later speech as invention and understates
    precision. Clip first — the same class of error as scoring a store mid-flush."""
    if src.startswith("http"):
        with urllib.request.urlopen(src, timeout=30) as r:
            d = json.loads(r.read())
    else:
        d = json.load(open(src))
    segs = d.get("segments") if isinstance(d, dict) else d
    lo, hi = window
    kept, dropped = [], 0
    for s in segs:
        t = (s.get("text") or "").strip()
        if not t:
            continue
        if lo and (s.get("start", 0) < lo - 1 or s.get("start", 0) > hi + 1):
            dropped += 1
            continue
        kept.append(t)
    if dropped:
        print(f"  (clipped {dropped} live segment(s) outside the session window)")
    return " ".join(kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--realtime", help="desktop transcripts URL or a JSON file of segments")
    ap.add_argument("--lang", default=os.environ.get("TX_LANG", "en"))
    ap.add_argument("--model", default=os.environ.get("TRANSCRIPTION_MODEL", "whisper-1"))
    ap.add_argument("--out", help="write the reference text here")
    ap.add_argument("--reference", help="reuse a previously written reference instead of re-running STT")
    args = ap.parse_args()

    url = os.environ.get("TRANSCRIPTION_SERVICE_URL")
    token = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN")
    if not url or not token:
        sys.exit("TRANSCRIPTION_SERVICE_URL and TRANSCRIPTION_SERVICE_TOKEN are required")

    pcm, span, nframes, window = load_session(args.session)
    audio_sec = len(pcm) / 4 / SR
    print(f"session: {nframes} frames · {audio_sec:.1f}s audio over {span:.1f}s wall "
          f"· capture duty cycle {audio_sec / span * 100:.1f}%" if span else "")

    if args.reference:
        ref = open(args.reference).read()
        ref_w = words(ref)
        print(f"REFERENCE (reused): {len(ref_w)} words")
        if args.realtime:
            rt_w = words(realtime_words(args.realtime, window))
            m = lcs(ref_w, rt_w)
            print(f"REAL-TIME         : {len(rt_w)} words")
            print(f"\n  recall    {m / max(1, len(ref_w)):.3f}   ({m}/{len(ref_w)})")
            print(f"  precision {m / max(1, len(rt_w)):.3f}   ({len(rt_w) - m} live words not in the reference)")
            print(f"  => streaming loses {1 - m / max(1, len(ref_w)):.1%}; invents/duplicates {1 - m / max(1, len(rt_w)):.1%}")
        return

    step = int((CHUNK_SEC - OVERLAP_SEC) * SR) * 4
    size = int(CHUNK_SEC * SR) * 4
    parts = []
    for off in range(0, len(pcm), step):
        chunk = pcm[off:off + size]
        if len(chunk) < SR * 4:      # < 1s tail, nothing to transcribe
            break
        t = transcribe(wav_bytes(chunk), url, token, args.model, args.lang)
        parts.append(t)
        print(f"  [{off / 4 / SR:6.1f}s] {len(words(t)):4d} words")
    ref = " ".join(parts)
    ref_w = words(ref)
    print(f"\nREFERENCE (single pass): {len(ref_w)} words · {len(ref_w) / audio_sec * 60:.0f} wpm over the delivered audio")
    if args.out:
        open(args.out, "w").write(ref)
        print(f"  written: {args.out}")

    if not args.realtime:
        return
    rt_w = words(realtime_words(args.realtime, window))
    m = lcs(ref_w, rt_w)
    print(f"REAL-TIME              : {len(rt_w)} words · {len(rt_w) / audio_sec * 60:.0f} wpm")
    print(f"\n  recall    {m / max(1, len(ref_w)):.3f}   ({m}/{len(ref_w)} reference words the live transcript kept, in order)")
    print(f"  precision {m / max(1, len(rt_w)):.3f}   ({len(rt_w) - m} live words not in the reference — invention/duplication)")
    print(f"  => the streaming design loses {1 - m / max(1, len(ref_w)):.1%} of what this same model "
          f"extracts from this same audio in one pass")


if __name__ == "__main__":
    main()
