#!/usr/bin/env python3
"""Score our diarized transcripts (real Whisper) vs Deepgram GT.
PRIMARY = speaker-switch placement; SECONDARY = frame speaker accuracy.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from score import frame_labels, optimal_map_accuracy
from switch_eval import gt_switches, match

HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")
gt = json.load(open(os.path.join(S, "dgclip.gt.json")))
dur = max(u["end"] for u in gt)
switches, turns = gt_switches(gt)
gt_iv = [(u["start"], u["end"], u["speaker"]) for u in gt]

def seg_switches(segs):
    s = sorted(segs, key=lambda x: x["start"])
    return [s[i]["start"] for i in range(1, len(s)) if s[i]["speaker"] != s[i-1]["speaker"]]

def score_file(path, tol=0.75):
    t = json.load(open(path))["transcript"]
    hyp_iv = [(x["start"], x["end"], x["speaker"]) for x in t]
    acc, _ = optimal_map_accuracy(frame_labels(gt_iv, dur), frame_labels(hyp_iv, dur))
    sw = seg_switches(t)
    r, p, dt, _ = match(sw, switches, tol)
    f1 = 2*r*p/(r+p) if (r+p) else 0
    return dict(n_clusters=len(set(x["speaker"] for x in t)), n_switches=len(sw),
                frame_acc=round(acc,3), recall=round(r,3), prec=round(p,3),
                f1=round(f1,3), med_dt_ms=round(dt) if dt else None)

print(f"GT: {len(turns)} turns, {len(switches)} real switches, dur={dur:.0f}s")
print(f"{'file':>10} | {'cl':>3} {'sw':>3} {'frameAcc':>8} {'recall':>6} {'prec':>5} {'F1':>5} {'dt_ms':>5}")
print("-"*60)
for name in ["baseline", "stitch"]:
    s = score_file(os.path.join(S, f"dgclip_tx.{name}.json"))
    print(f"{name:>10} | {s['n_clusters']:>3} {s['n_switches']:>3} {s['frame_acc']:>8.3f} "
          f"{s['recall']:>6.3f} {s['prec']:>5.3f} {s['f1']:>5.3f} {str(s['med_dt_ms']):>5}")
