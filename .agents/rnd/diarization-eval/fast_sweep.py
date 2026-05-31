#!/usr/bin/env python3
"""Host-side clustering sweep: replay the EXACT online-clustering algorithm
(ported from online-clustering.ts) over cached embeddings, score each config
against GT. No docker, no models — the whole grid runs in milliseconds.

Capture the embeddings once (eval-diarizer DIAR_DUMP_EMB), then iterate here
instantly.

Usage: fast_sweep.py --emb X.capture.emb.json --gt X.gt.json [--grid default]
"""
import sys, os, json, argparse, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from score import frame_labels, boundaries, match_boundaries, optimal_map_accuracy

def normalize(v):
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n

class Clustering:
    """Faithful port of OnlineSpeakerClustering.assignWithSeedGate +
    mergeClose (online-clustering.ts). stickyBias=0 so the sticky hint is a
    no-op and omitted."""
    def __init__(self, newSpeakerThreshold=0.45, veryFarThreshold=0.65,
                 maxSpeakers=None, emaAlpha=0.70):
        self.threshold = newSpeakerThreshold
        self.veryFar = veryFarThreshold
        self.maxSpeakers = maxSpeakers
        self.emaAlpha = emaAlpha
        self.centroids = {}   # id -> np.array (unit norm)

    def size(self): return len(self.centroids)

    def assign(self, emb, can_seed_new, allow_new):
        emb = normalize(np.asarray(emb, dtype=np.float64))
        if not self.centroids:
            if not can_seed_new or not allow_new:
                return ("speaker_0", float("nan"), False)
            self.centroids["speaker_0"] = emb.copy()
            return ("speaker_0", 0.0, True)
        nearest_id, nearest, second = None, np.inf, np.inf
        for cid, c in self.centroids.items():
            d = 1.0 - float(np.dot(emb, c))
            if d < nearest:
                second = nearest; nearest = d; nearest_id = cid
            elif d < second:
                second = d
        nearest_true = nearest
        under_cap = self.maxSpeakers is None or len(self.centroids) < self.maxSpeakers
        very_far = nearest >= self.veryFar
        gap_margin = 0.10
        has_gap = (len(self.centroids) < 2) or (second - nearest >= gap_margin) or (nearest >= self.veryFar)
        can_alloc = under_cap and has_gap and (very_far or (can_seed_new and allow_new))
        if nearest < self.threshold or not can_alloc:
            if nearest_true < 0.25:
                old = self.centroids[nearest_id]
                upd = self.emaAlpha * old + (1 - self.emaAlpha) * emb
                self.centroids[nearest_id] = normalize(upd)
            return (nearest_id, nearest_true, False)
        new_id = f"speaker_{len(self.centroids)}"
        self.centroids[new_id] = emb.copy()
        return (new_id, nearest_true, True)

    def merge_close(self, thr=0.30):
        result = {}
        did = True
        while did:
            did = False
            ids = list(self.centroids.keys())
            stop = False
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a = self.centroids[ids[i]]; b = self.centroids[ids[j]]
                    d = 1.0 - float(np.dot(a, b))
                    if d < thr:
                        self.centroids[ids[i]] = normalize((a + b) / 2.0)
                        del self.centroids[ids[j]]
                        tgt = ids[i]
                        while tgt in result: tgt = result[tgt]
                        result[ids[j]] = tgt
                        did = True; stop = True; break
                if stop: break
        return result

def replay(records, cfg):
    cl = Clustering(cfg.get("newSpeakerThreshold", 0.45),
                    cfg.get("veryFarThreshold", 0.65),
                    cfg.get("maxSpeakers"), cfg.get("emaAlpha", 0.70))
    cooldown = cfg.get("newClusterCooldownMs", 4000)
    merge_thr = cfg.get("mergeThreshold", 0.30)
    last_new = -np.inf
    rewrites = {}
    commits = []
    # minSeedUtteranceMs is recomputable from cached durSamples, so it's
    # sweepable here without re-capture. None → use the cached canSeedNew.
    seed_ms = cfg.get("minSeedUtteranceMs")
    for r in records:
        allow_new = (r["tEndMs"] - last_new) >= cooldown
        can_seed = r["canSeedNew"] if seed_ms is None else (r["durSamples"] >= seed_ms / 1000 * 16000)
        sid, dist, is_new = cl.assign(r["emb"], can_seed, allow_new)
        if is_new: last_new = r["tEndMs"]
        for old, kept in cl.merge_close(merge_thr).items():
            tgt = kept
            while tgt in rewrites: tgt = rewrites[tgt]
            rewrites[old] = tgt
        final = sid
        while final in rewrites: final = rewrites[final]
        commits.append((final, r["tStartMs"] / 1000.0, r["tEndMs"] / 1000.0))
    return commits

def score(commits, gt, dur, tol=0.75):
    gt_iv = [(t["start"], t["end"], t["speaker"]) for t in gt]
    hyp_iv = [(s, e, sp) for sp, s, e in commits]
    acc, _ = optimal_map_accuracy(frame_labels(gt_iv, dur), frame_labels(hyp_iv, dur))
    rec, prec, timing, _ = match_boundaries(boundaries(gt_iv), boundaries(hyp_iv), tol)
    return dict(n_clusters=len(set(c[0] for c in commits)), frame_acc=round(acc, 4),
                brecall=round(rec, 4), bprec=round(prec, 4),
                btime=round(timing * 1000, 1) if timing is not None else None)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    data = json.load(open(a.emb))
    records = data["records"]; dur = data["durationS"]
    gt = json.load(open(a.gt))
    n_gt = len(set(t["speaker"] for t in gt))

    NST = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    VFT = [0.40, 0.45, 0.55, 0.65]
    CD  = [0, 1000, 4000]
    rows = []
    import time; t0 = time.time()
    for nst in NST:
        for vft in VFT:
            if vft <= nst: continue
            for cd in CD:
                cfg = dict(newSpeakerThreshold=nst, veryFarThreshold=vft, newClusterCooldownMs=cd)
                s = score(replay(records, cfg), gt, dur)
                rows.append(dict(nst=nst, vft=vft, cd=cd, **s))
    ms = (time.time() - t0) * 1000
    rows.sort(key=lambda x: (x["frame_acc"], x["brecall"]), reverse=True)
    print(f"GT={n_gt} speakers, {len(records)} utterances, {len(rows)} configs in {ms:.0f}ms")
    print(f"{'nst':>4} {'vft':>4} {'cd':>5} | {'clust':>5} {'frameAcc':>8} {'bRecall':>7} {'bPrec':>6} {'btime':>6}")
    print("-" * 58)
    for x in rows[:a.top]:
        print(f"{x['nst']:>4} {x['vft']:>4} {x['cd']:>5} | {x['n_clusters']:>5} {x['frame_acc']:>8.3f} {x['brecall']:>7.3f} {x['bprec']:>6.3f} {str(x['btime']):>6}")
    json.dump(rows, open(a.emb.replace(".emb.json", ".fast_results.json"), "w"), indent=2)

if __name__ == "__main__":
    main()
