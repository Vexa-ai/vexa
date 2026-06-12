#!/usr/bin/env python3
"""Score diarizer commits against AMI ground truth.

Metrics (all designed for RELATIVE ranking across configs):
  - n_clusters vs n_gt_speakers (over/under-clustering)
  - frame_accuracy: % of speech frames correctly attributed after optimal
    cluster->GT-speaker mapping (10ms frames); 1 - this ≈ DER-confusion
  - boundary recall: fraction of GT speaker-change instants matched by a
    commit boundary within tol  (1 - recall = missed-split rate)
  - boundary precision: fraction of commit boundaries that match a GT change
    within tol  (1 - precision = false-positive-split rate)
  - boundary_timing_ms: mean |Δt| for matched boundaries (the "few words off")

Usage: score.py --gt X.gt.json --commits X.commits.json [--tol 0.75] [--out X.score.json]
"""
import json, argparse, numpy as np

def load(p): return json.load(open(p))

def frame_labels(intervals, dur, hop=0.01):
    """intervals: list of (start,end,label). Returns array of label-or-None per frame.
    On overlap, last-writer-wins (approximate; fine for relative scoring)."""
    n = int(np.ceil(dur / hop))
    lab = np.full(n, "", dtype=object)
    for s, e, l in intervals:
        a = max(0, int(s / hop)); b = min(n, int(e / hop))
        lab[a:b] = l
    return lab

def boundaries(intervals):
    """speaker-change instants: start of a turn whose speaker differs from the
    previous (by time) turn's speaker."""
    iv = sorted(intervals, key=lambda x: x[0])
    bs = []
    prev = None
    for s, e, l in iv:
        if prev is None or l != prev:
            bs.append(s)
        prev = l
    return bs

def match_boundaries(gt_bs, hyp_bs, tol):
    gt = sorted(gt_bs); hyp = sorted(hyp_bs)
    used = [False] * len(hyp)
    matched = 0; errs = []
    for g in gt:
        best = -1; bestd = tol + 1
        for j, h in enumerate(hyp):
            if used[j]: continue
            d = abs(h - g)
            if d <= tol and d < bestd:
                bestd = d; best = j
        if best >= 0:
            used[best] = True; matched += 1; errs.append(bestd)
    recall = matched / len(gt) if gt else 0.0
    precision = matched / len(hyp) if hyp else 0.0
    timing = float(np.mean(errs)) if errs else None
    return recall, precision, timing, matched

def optimal_map_accuracy(gt_lab, hyp_lab):
    """frame accuracy after optimal hyp-cluster -> gt-speaker assignment."""
    n = min(len(gt_lab), len(hyp_lab))
    gt_lab = gt_lab[:n]; hyp_lab = hyp_lab[:n]
    speech = gt_lab != ""
    gt_s = gt_lab[speech]; hyp_s = hyp_lab[speech]
    if len(gt_s) == 0: return 0.0, {}
    gts = sorted(set(gt_s)); hyps = sorted(set(h for h in hyp_s if h != ""))
    gi = {g: i for i, g in enumerate(gts)}; hi = {h: i for i, h in enumerate(hyps)}
    M = np.zeros((len(hyps), len(gts)), dtype=np.int64)
    for h, g in zip(hyp_s, gt_s):
        if h == "": continue
        M[hi[h], gi[g]] += 1
    try:
        from scipy.optimize import linear_sum_assignment
        ri, ci = linear_sum_assignment(-M)
        correct = M[ri, ci].sum()
        mapping = {hyps[r]: gts[c] for r, c in zip(ri, ci)}
    except Exception:
        # greedy fallback
        mapping = {}; correct = 0; usedg = set()
        order = np.dstack(np.unravel_index(np.argsort(-M, axis=None), M.shape))[0]
        usedh = set()
        for r, c in order:
            if r in usedh or c in usedg: continue
            usedh.add(r); usedg.add(c); mapping[hyps[r]] = gts[c]; correct += M[r, c]
    return correct / len(gt_s), mapping

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--commits", required=True)
    ap.add_argument("--tol", type=float, default=0.75)
    ap.add_argument("--out")
    a = ap.parse_args()

    gt = load(a.gt)
    cm = load(a.commits)
    commits = cm["commits"]
    dur = cm.get("durationS") or max(t["end"] for t in gt)

    gt_iv = [(t["start"], t["end"], t["speaker"]) for t in gt]
    hyp_iv = [(c["startS"], c["endS"], c["speakerId"]) for c in commits]

    gt_lab = frame_labels(gt_iv, dur)
    hyp_lab = frame_labels(hyp_iv, dur)
    acc, mapping = optimal_map_accuracy(gt_lab, hyp_lab)

    gt_bs = boundaries(gt_iv); hyp_bs = boundaries(hyp_iv)
    rec, prec, timing, matched = match_boundaries(gt_bs, hyp_bs, a.tol)

    n_gt_spk = len(set(t["speaker"] for t in gt))
    res = dict(
        config=cm.get("config", {}),
        n_gt_speakers=n_gt_spk,
        n_clusters=cm.get("nClusters", len(set(c["speakerId"] for c in commits))),
        n_commits=len(commits),
        frame_accuracy=round(acc, 4),
        confusion_der=round(1 - acc, 4),
        boundary_recall=round(rec, 4),
        boundary_precision=round(prec, 4),
        boundary_timing_ms=round(timing * 1000, 1) if timing is not None else None,
        gt_boundaries=len(gt_bs),
        hyp_boundaries=len(hyp_bs),
        matched_boundaries=matched,
        cluster_map={k: v for k, v in mapping.items()},
        realtime_x=round(dur / cm["wallS"], 2) if cm.get("wallS") else None,
    )
    print(json.dumps(res, indent=2))
    if a.out:
        json.dump(res, open(a.out, "w"), indent=2)

if __name__ == "__main__":
    main()
