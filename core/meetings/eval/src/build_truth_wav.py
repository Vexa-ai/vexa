#!/usr/bin/env python3
"""Build a speaker's microphone WAV out of the cached TTS corpus — and the truth beside it.

Every content number in the corpus today is scored against a SINGLE-PASS STT reference, and that
reference has been wrong twice: it duplicates itself at chunk seams, and it disagrees with a second
cut of itself by 4-24% depending on the entry. A reference that needs its own error bars cannot
settle whether the pipeline lost a word.

A TTS clip does not have that problem. The text is known before any audio exists, so a WAV
concatenated from clips carries exact truth: which words, in which order, at which offset, from
which speaker. Feed that WAV to a synthetic participant's fake microphone and the meeting itself
becomes the fixture.

    python3 build_truth_wav.py --speaker A --name anna --out <dir> [--gap-sec 1.0] [--clips 16]

Writes <dir>/<name>.wav (16 kHz mono, the rate the whole pipeline speaks) and <dir>/<name>.truth.json:

    {"speaker": "anna", "sampleRate": 16000, "durationSec": 269.4,
     "clips": [{"i": 0, "startSec": 0.0, "endSec": 14.2, "text": "Anna here. Caching would…"}, …]}

The offsets are the contract: a scorer that knows when a speaker's WAV started in wall time can map
any transcript segment back to the clip that produced it, and therefore to the words that were
really said and the person who really said them.
"""
import argparse
import base64
import io
import json
import os
import wave

CACHE = os.environ.get("EVAL_CACHE", os.path.expanduser("~/vexa-test-rig/cache"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", required=True, help="cache key: A=Anna, B=Boris, V=Vera, …")
    ap.add_argument("--name", required=True, help="display name this speaker joins under")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gap-sec", type=float, default=1.0,
                    help="silence between clips — a turn boundary the segmenter must find")
    ap.add_argument("--clips", type=int, default=0, help="use at most N clips (0 = all)")
    args = ap.parse_args()

    with open(os.path.join(CACHE, f"{args.speaker}.json")) as fh:
        clips = json.load(fh)
    if args.clips:
        clips = clips[: args.clips]

    os.makedirs(args.out, exist_ok=True)
    frames = b""
    params = None
    manifest = []
    for i, clip in enumerate(clips):
        src = wave.open(io.BytesIO(base64.b64decode(clip["b64"])))
        if params is None:
            params = src.getparams()
        elif (src.getframerate(), src.getnchannels(), src.getsampwidth()) != (
            params.framerate, params.nchannels, params.sampwidth
        ):
            # Concatenating mismatched formats produces audio that plays at the wrong speed, which
            # then scores as catastrophic word loss. Refuse rather than emit a fixture that lies.
            raise SystemExit(f"clip {i} format {src.getparams()} != {params}")
        bytes_per_sec = params.framerate * params.nchannels * params.sampwidth
        start = len(frames) / bytes_per_sec
        frames += src.readframes(src.getnframes())
        end = len(frames) / bytes_per_sec
        frames += b"\x00" * int(args.gap_sec * bytes_per_sec)
        manifest.append({"i": i, "startSec": round(start, 3), "endSec": round(end, 3), "text": clip["text"]})

    wav_path = os.path.join(args.out, f"{args.name}.wav")
    out = wave.open(wav_path, "wb")
    out.setparams(params)
    out.writeframes(frames)
    out.close()

    duration = len(frames) / (params.framerate * params.nchannels * params.sampwidth)
    truth = {
        "speaker": args.name,
        "cacheKey": args.speaker,
        "sampleRate": params.framerate,
        "durationSec": round(duration, 2),
        "words": sum(len(c["text"].split()) for c in clips),
        "clips": manifest,
    }
    with open(os.path.join(args.out, f"{args.name}.truth.json"), "w") as fh:
        json.dump(truth, fh, indent=1)

    print(f"{wav_path}  {duration:.1f}s · {len(clips)} clips · {truth['words']} words · {params.framerate} Hz")


if __name__ == "__main__":
    main()
