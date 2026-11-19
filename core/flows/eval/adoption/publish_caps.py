"""Publish a synthetic dailies fixture into the rig's caps directory so `meeting_seed` can seed
it exactly like a recorded one — the harness stays on the product's own path, and nothing about
the seeding leg is special-cased for synthetic input.

The fixture keeps `synthetic: true` in its own meta; the caps file is only the segment list the
rig reads, so it carries no provenance of its own. That is why the fixture, not the caps file,
is the record of what this is.
"""
import glob
import json
import os
import sys
from pathlib import Path

CAPS = Path(os.path.expanduser("~/.storm/caps"))
SRC = Path(os.path.expanduser("~/dna-fixtures/synthetic"))

out = []
for f in sorted(glob.glob(str(SRC / "*.transcript.json"))):
    d = json.load(open(f))
    if not d.get("meeting", {}).get("synthetic"):
        print(f"  skip {f} — not marked synthetic")
        continue
    vid = "synth-" + Path(f).name.replace(".transcript.json", "")
    segs = [{"start": s["t"], "end": s["end"], "speaker": s["speaker"], "text": s["text"]}
            for s in d["segments"]]
    (CAPS / f"{vid}.segments.json").write_text(json.dumps(segs, indent=1))
    mins = round(segs[-1]["end"] / 60.0, 1)
    out.append((vid, d["meeting"]["title"], len(segs), mins,
                d["meeting"]["show"], d["meeting"]["department"]))
    print(f"  {vid}  {len(segs)} segs  {mins} min  ({d['meeting']['show']} / {d['meeting']['department']})")

json.dump([{"video_id": v, "title": t, "segments": n, "minutes": m, "show": sh, "department": dp}
           for v, t, n, m, sh, dp in out],
          open(os.path.expanduser("~/sim-runs/r1/synthetic-index.json"), "w"), indent=1)
print(f"\n{len(out)} synthetic fixtures published to {CAPS}")
