#!/usr/bin/env python3
"""Parse YouTube auto-caption VTT → transcript.v1 fixture (JSONL of {speaker, text, start}).

Auto-captions are rolling (each cue repeats the prior line + a new one, with inline word tags) and have
NO speaker labels. We: (1) dedup to the flowing transcript, (2) split into sentences, (3) group into
~2-sentence turns, (4) assign best-effort speakers by rotating the known panel on a turn boundary
(heuristic — attribution is not real), (5) cap to a slice so the replay is a couple of minutes.
"""
import json
import re
import sys

VTT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ytcap.en.vtt"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/gamestop-allin.jsonl"
MAX_TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 22
SENTS_PER_TURN = 2

# Best-effort speaker pool (All-In hosts + the guest). Rotated on turn boundaries — heuristic only.
SPEAKERS = ["Ryan Cohen", "Jason Calacanis", "Chamath Palihapitiya", "David Sacks", "David Friedberg"]

ts_re = re.compile(r"(\d\d):(\d\d):(\d\d)\.(\d+)\s*-->")
tag_re = re.compile(r"<[^>]+>")

flowing = []  # (start_seconds, text_line)
cur_start = 0.0
last = None
for line in open(VTT, encoding="utf-8"):
    line = line.rstrip("\n")
    m = ts_re.match(line.strip())
    if m:
        h, mi, s, ms = (int(x) for x in m.groups())
        cur_start = h * 3600 + mi * 60 + s + ms / 1000.0
        continue
    txt = tag_re.sub("", line).strip()
    if not txt or txt == last:
        continue
    if txt == "WEBVTT" or txt.startswith(("Kind:", "Language:", "NOTE")):
        continue
    last = txt
    flowing.append((cur_start, txt))

# Join into one stream, keeping the start time of each chunk; then split to sentences.
text = " ".join(t for _s, t in flowing)
text = re.sub(r"\s+", " ", text).strip()
# crude sentence split (keep the terminator)
sentences = re.split(r"(?<=[.!?])\s+", text)
# map each sentence to an approximate start time (proportional)
total = flowing[-1][0] if flowing else 0.0
starts = [flowing[min(int(i / max(1, len(sentences)) * len(flowing)), len(flowing) - 1)][0]
          for i in range(len(sentences))]

segments = []
turn = 0
for i in range(0, len(sentences), SENTS_PER_TURN):
    if turn >= MAX_TURNS:
        break
    chunk = " ".join(sentences[i:i + SENTS_PER_TURN]).strip()
    if len(chunk) < 8:
        continue
    speaker = SPEAKERS[turn % len(SPEAKERS)]
    segments.append({"speaker": speaker, "text": chunk, "start": round(starts[i], 1)})
    turn += 1

with open(OUT, "w", encoding="utf-8") as f:
    for seg in segments:
        f.write(json.dumps(seg) + "\n")

print(f"{len(segments)} segments → {OUT}")
for seg in segments[:6]:
    print(f"  [{seg['start']:6.1f}s] {seg['speaker']}: {seg['text'][:80]}")
