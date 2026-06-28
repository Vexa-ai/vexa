#!/usr/bin/env python3
"""counting_matrix — the FAST, DETERMINISTIC gate: push every scenario's stage-3 segments through the
collector and assert the 1..N + speaker oracle survives to `tc:meeting:{native}` (stage 5).

This stops at stage 5 ON PURPOSE: stages 1–5 are deterministic (TTS/STT/collector — same in ⇒ same out),
so they make a reproducible CI gate. Stage 6 (the LLM copilot) is proven separately (counting_replay.py,
20/20) but its timing is model-bound, so it's not part of the fast gate.

Publishes to `transcription_segments` with native STAMPED (the P23 path) → collector writes
`tc:meeting:{native}` → reads it back → asserts. Runs against the local vexa-v012 stack via docker exec.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

STORE = Path.home() / "vexa-test-rig" / "fixtures" / "google_meet"
SCENARIOS = ["silence", "overlap", "dynamic", "continuation", "solo"]
_W = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}


def nums_in(text):
    out = []
    for tok in re.findall(r"\d+|[a-z]+", str(text).lower()):
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _W:
            out.append(_W[tok])
    return out


def run_one(fx: Path) -> dict:
    segs = [json.loads(l) for l in (fx / "3-segments.jsonl").read_text().splitlines() if l.strip()]
    truth = [json.loads(l) for l in (fx / "truth.jsonl").read_text().splitlines() if l.strip()]
    n = max(x for t in truth for x in t["numbers"])
    native = f"mtx-{fx.name}"
    payloads = [json.dumps({"type": "transcription", "meeting_id": "900002",
                            "native_meeting_id": native, "platform": "google_meet", "segments": [s]})
                for s in segs]
    # publish all, then read tc:meeting:{native} back — pure collector path, no copilot.
    script = f"""
import os,sys,json,time,redis
r=redis.from_url(os.environ.get('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
r.delete('tc:meeting:{native}')
for line in sys.stdin:
    line=line.strip()
    if line: r.xadd('transcription_segments',{{'payload':line}})
# wait until the collector has DRAINED (native stream stops growing) — robust to batch size / 100s of segs
prev=-1
for _ in range(40):
    time.sleep(1.0)
    cur=r.xlen('tc:meeting:{native}')
    if cur>0 and cur==prev: break
    prev=cur
rows=r.xrange('tc:meeting:{native}')
out=[]
for _id,f in rows:
    try: p=json.loads(f['payload'])
    except: continue
    for sg in p.get('segments',[]):
        out.append({{'speaker':sg.get('speaker'),'text':sg.get('text')}})
print(json.dumps(out))
"""
    res = subprocess.run(["docker", "exec", "-i", "vexa-v012-meeting-api-1", "python", "-c", script],
                         input="\n".join(payloads), text=True, capture_output=True)
    native_segs = json.loads(res.stdout.strip().splitlines()[-1]) if res.stdout.strip() else []
    got = set(nums_in(" ".join(s["text"] for s in native_segs)))
    stage3 = set(nums_in(" ".join(s["text"] for s in segs)))     # what STT actually produced (the input)
    spk_ok = all(s.get("speaker") for s in native_segs)
    # ATTRIBUTE the loss (fail-loud applied to the test): the DOWNSTREAM gate passes iff it relays
    # everything stage-3 gave it (got == stage3). Numbers missing from stage3 are an STT (stage-2) loss,
    # not a downstream loss — reported separately so a failure points at the right stage.
    downstream_lossless = got == stage3
    return {"n": n, "native_segments": len(native_segs), "reached": len(got),
            "stage3": len(stage3), "stt_recall": round(len(stage3) / n, 3),
            "downstream_lossless": downstream_lossless, "speakers_present": spk_ok,
            "downstream_dropped": sorted(stage3 - got)}


def main():
    # default: the 1to20 scenario matrix; or pass explicit fixture dirs as args (e.g. the 1-500 fixture).
    fixtures = [Path(p) for p in sys.argv[1:]] or [STORE / f"count-{sc}-1to20" for sc in SCENARIOS]
    print(f"{'fixture':24} {'n':>4} {'STT(s2)':>9} {'down(s4-5)':>11} {'spk':>4}  verdict")
    rc = 0
    for fx in fixtures:
        if not fx.exists():
            print(f"{fx.name:24}  (no fixture)")
            continue
        r = run_one(fx)
        ok = r["downstream_lossless"] and r["speakers_present"]
        rc |= 0 if ok else 1
        stt = f"{r['reached']}/{r['stage3']}={r['stt_recall']}"  # downstream-reached / stage3-in
        down = "LOSSLESS" if r["downstream_lossless"] else f"DROP {r['downstream_dropped'][:6]}"
        print(f"{fx.name:24} {r['n']:>4} {('r=' + str(r['stt_recall'])):>9} {down:>11} "
              f"{str(r['speakers_present']):>4}  {'PASS' if ok else 'FAIL'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
