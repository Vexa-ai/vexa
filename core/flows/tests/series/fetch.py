#!/usr/bin/env python3
"""fetch — rebuild a series fixture from its published source.

The fixtures in this tree are checked in so the harness runs with no network. This script is how
they were MADE, kept next to them so the provenance is reproducible rather than asserted:

    python3 fetch.py --url https://www.youtube.com/watch?v=XXXX --lang en \\
        --out finos-<slug>/ep1.jsonl --trim-min 35

It shells out to `yt-dlp --write-auto-sub --skip-download` (captions only — no audio, no video
is ever downloaded), then converts the WebVTT cues into the transcript fixture shape.

FIXTURE SHAPE — one JSON object per line, the same columns the `transcriptions` table carries
and `flows_steps/meeting.py: FIXTURE_LINES` uses:

    {"start": 0.0, "end": 6.0, "speaker": "Anna", "text": "…", "language": "en"}

`speaker` is **null** for auto-captions, which carry no diarization. We keep it null rather than
invent speakers: an invented speaker would be the harness lying to the thing it exists to test.
Where a source publishes a real speaker-labelled transcript, the label goes in.

COPYRIGHT: these are internal test fixtures drawn from PUBLIC meetings, trimmed to what testing
needs, with sources cited in each series README. Not for republication.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
TAG = re.compile(r"<[^>]+>")
SPEAKER = re.compile(r"^\s*(?:\[|\()?([A-Z][A-Za-zÄÖÜäöüß.\- ]{1,30}?)(?:\]|\))?\s*:\s+")


def secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """WebVTT → (start, end, line). YouTube auto-captions are a ROLLING WINDOW: every cue repeats
    the tail of the previous cue and appends one new line. Dedup therefore has to happen at LINE
    level, not cue level — cue-level comparison keeps every line twice, which is what the first
    version of this parser did and why the fixture read double."""
    out: list[tuple[float, float, str]] = []
    recent: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = TS.search(lines[i])
        if not m:
            i += 1
            continue
        start, end = secs(*m.groups()[:4]), secs(*m.groups()[4:])
        i += 1
        while i < len(lines) and lines[i].strip() and not TS.search(lines[i]):
            line = re.sub(r"\s+", " ", html.unescape(TAG.sub("", lines[i])).strip())
            i += 1
            if not line or line in recent[-4:]:
                continue
            recent.append(line)
            out.append((start, end, line))
    return out


def group(cues: list[tuple[float, float, str]], target_s: float) -> list[dict]:
    """Caption lines are ~2 s of text; a transcript segment is an utterance. Group lines up to
    `target_s`, and break on `>>` — YouTube's own speaker-change marker, the only turn signal
    auto-captions carry. We use it as a boundary and never as a name."""
    segs: list[dict] = []
    buf: list[str] = []
    s0 = e0 = 0.0

    def flush() -> None:
        nonlocal buf
        txt = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if txt:
            segs.append({"start": round(s0, 2), "end": round(e0, 2), "text": txt})
        buf = []

    for start, end, line in cues:
        turn = line.lstrip().startswith(">>")
        line = re.sub(r"^\s*>>+\s*", "", line)
        if turn and buf:
            flush()
        if not buf:
            s0 = start
        buf.append(line)
        e0 = end
        if e0 - s0 >= target_s and line.rstrip().endswith((".", "?", "!")):
            flush()
    flush()
    return segs


def split_speaker(seg: dict) -> dict:
    """A published transcript may prefix `Name:`. Auto-captions do not — those stay null."""
    m = SPEAKER.match(seg["text"])
    if m and len(m.group(1).split()) <= 3:
        return {**seg, "speaker": m.group(1).strip(), "text": seg["text"][m.end():].strip()}
    return {**seg, "speaker": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="YouTube URL (omit when --vtt is given)")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out", required=True, help="path to the .jsonl fixture to write")
    ap.add_argument("--trim-min", type=float, default=0.0,
                    help="keep only the first N minutes (record the trim in the series README)")
    ap.add_argument("--group-s", type=float, default=8.0)
    ap.add_argument("--vtt", default=None, help="skip yt-dlp; convert an already-downloaded .vtt")
    args = ap.parse_args()
    if not args.url and not args.vtt:
        ap.error("one of --url or --vtt is required")

    if args.vtt:
        vtt = Path(args.vtt).read_text()
    else:
        with tempfile.TemporaryDirectory() as td:
            cmd = ["yt-dlp", "--write-auto-sub", "--write-sub", "--sub-lang", args.lang,
                   "--skip-download", "--sub-format", "vtt", "-o", f"{td}/cap", args.url]
            r = subprocess.run(cmd, capture_output=True, text=True)
            found = sorted(Path(td).glob("*.vtt"))
            if not found:
                print(r.stdout[-3000:], file=sys.stderr)
                print(r.stderr[-3000:], file=sys.stderr)
                return 1
            vtt = found[0].read_text()

    cues = parse_vtt(vtt)
    segs = [split_speaker(s) for s in group(cues, args.group_s)]
    for s in segs:
        s["language"] = args.lang
    if args.trim_min:
        cut = args.trim_min * 60
        segs = [s for s in segs if s["start"] < cut]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in segs))
    dur = segs[-1]["end"] / 60 if segs else 0
    print(f"{out}: {len(segs)} segments · {dur:.1f} min · {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
