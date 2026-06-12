#!/usr/bin/env python3
"""Sweep clustering config on the SWITCH-DETECTION metric (primary) using
cached embeddings — instant, no docker. Scores switch recall/prec/F1 vs
Deepgram's real switches, plus frame accuracy (secondary).
"""
import json, os, sys, argparse
sys.path.insert(0, os.path.dirname(__file__))
from fast_sweep import replay
from score import frame_labels, optimal_map_accuracy
from switch_eval import gt_switches, match

HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")

def run(emb_path, gt_path, tol=0.75, top=20):
    emb = json.load(open(emb_path)); recs = emb["records"]; dur = emb["durationS"]
    gt = json.load(open(gt_path))
    switches, turns = gt_switches(gt)
    gt_iv = [(u["start"], u["end"], u["speaker"]) for u in gt]
    gframes = frame_labels(gt_iv, dur)

    NST = [0.62, 0.68, 0.72, 0.75, 0.80]
    VFT = [0.88, 0.92, 0.95]
    STICKY = [0.0, 0.05, 0.10, 0.15, 0.20]
    cd, seed = 0, None  # cooldown/seed were inert in prior sweep; fix them
    rows = []
    for nst in NST:
        for vft in VFT:
            if vft <= nst: continue
            for sticky in STICKY:
                cfg = dict(newSpeakerThreshold=nst, veryFarThreshold=vft,
                           newClusterCooldownMs=cd, minSeedUtteranceMs=seed,
                           stickyBias=sticky)
                commits = replay(recs, cfg)
                sw = [commits[i][1] for i in range(1, len(commits))
                      if commits[i][0] != commits[i-1][0]]
                r, p, dt, _ = match(sw, switches, tol)
                f1 = 2*r*p/(r+p) if (r+p) else 0
                hyp_iv = [(a, b, s) for s, a, b in commits]
                acc, _ = optimal_map_accuracy(gframes, frame_labels(hyp_iv, dur))
                rows.append(dict(nst=nst, vft=vft, sticky=sticky,
                                 ncl=len(set(c[0] for c in commits)), nsw=len(sw),
                                 recall=round(r,3), prec=round(p,3), f1=round(f1,3),
                                 facc=round(acc,3), dt=round(dt) if dt else None))
    # primary sort: switch F1, then recall, then frame acc
    rows.sort(key=lambda x: (x["f1"], x["recall"], x["facc"]), reverse=True)
    print(f"GT {len(turns)} turns / {len(switches)} switches, {len(recs)} commits, {len(rows)} configs (tol±{tol}s)")
    print(f"{'nst':>4} {'vft':>4} {'stky':>5} | {'ncl':>3} {'nsw':>3} {'recall':>6} {'prec':>5} {'F1':>5} {'facc':>5} {'dt':>4}")
    print("-"*64)
    for x in rows[:top]:
        print(f"{x['nst']:>4} {x['vft']:>4} {x['sticky']:>5.2f} | "
              f"{x['ncl']:>3} {x['nsw']:>3} {x['recall']:>6.3f} {x['prec']:>5.3f} "
              f"{x['f1']:>5.3f} {x['facc']:>5.3f} {str(x['dt']):>4}")
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", default=os.path.join(S, "dgclip_bnd.emb.json"))
    ap.add_argument("--gt", default=os.path.join(S, "dgclip.gt.json"))
    a = ap.parse_args()
    run(a.emb, a.gt)
