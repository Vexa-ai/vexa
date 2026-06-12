#!/usr/bin/env python3
"""Offline sanity-check of the TurnGate logic (port of turn-gate.ts) over the
captured per-commit embeddings. Confirms it produces sensible turns + speaker
labels before live Teams testing. Scores label assignment vs Deepgram GT.
"""
import json, sys, os, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from score import frame_labels, optimal_map_accuracy
from switch_eval import gt_switches

S = os.path.join(os.path.dirname(__file__), "samples")
SR = 16000
CFG = dict(switchDist=0.55, minStableSamples=1.5*SR, convergeEps=0.10,
           matchThreshold=0.55, newSpeakerMargin=0.10, maxHoldMs=2500, emaAlpha=0.7)

def norm(v):
    v = np.asarray(v, float); n = np.linalg.norm(v); return v/n if n > 1e-8 else v

class TurnGate:
    def __init__(self, cfg):
        self.cfg = cfg; self.centroids = {}; self.counter = 0; self.held = None
        self.flushes = []  # (name, tStartMs, samples)
    def _mint(self, c):
        i = f"speaker_{self.counter}"; self.counter += 1; self.centroids[i] = c.copy(); return i
    def _assign(self, c, allow_new):
        if not self.centroids: return self._mint(c)
        nid, near, sec = None, 1e9, 1e9
        for i, cc in self.centroids.items():
            d = 1 - float(np.dot(c, cc))
            if d < near: sec, near, nid = near, d, i
            elif d < sec: sec = d
        if near < self.cfg["matchThreshold"]: return nid
        if allow_new and (sec - near >= self.cfg["newSpeakerMargin"] or len(self.centroids) < 1): return self._mint(c)
        return nid
    def _update(self, name, c):
        a = self.cfg["emaAlpha"]; self.centroids[name] = norm(a*self.centroids[name] + (1-a)*c)
    def _flush(self, name, samples, tStartMs):
        self.flushes.append((name, tStartMs, samples))
    def _close(self):
        h = self.held; self.held = None
        if h and h["name"] is None:
            self._flush(self._assign(h["centroid"], False), h["pending"], h["startMs"])
    def _stab(self, tEndMs):
        h = self.held
        enough = h["voiced"] >= self.cfg["minStableSamples"]
        conv = len(h["embs"]) < 2 or (1 - float(np.dot(h["embs"][-1], h["centroid"])) < self.cfg["convergeEps"])
        timed = tEndMs - h["startMs"] >= self.cfg["maxHoldMs"]
        stab = enough and conv
        if not stab and not timed: return
        name = self._assign(h["centroid"], stab); h["name"] = name
        self._flush(name, h["pending"], h["startMs"]); h["pending"] = 0
        if stab: self._update(name, h["centroid"])
    def on_commit(self, emb, samples, tStartMs, tEndMs):
        e = norm(emb)
        if self.held and 1 - float(np.dot(e, self.held["centroid"])) > self.cfg["switchDist"]:
            self._close()
        if not self.held:
            self.held = dict(embs=[e], centroid=e, voiced=samples, name=None, startMs=tStartMs, pending=samples)
        else:
            h = self.held; h["embs"].append(e); h["centroid"] = norm(np.mean(h["embs"], axis=0))
            h["voiced"] += samples
            if h["name"] is None: h["pending"] += samples
        if self.held["name"] is not None:
            self._flush(self.held["name"], samples, tStartMs)
        else:
            self._stab(tEndMs)

recs = json.load(open(f"{S}/dgclip_bnd.emb.json"))["records"]
g = TurnGate(CFG)
for r in recs:
    g.on_commit(r["emb"], r["durSamples"], r["tStartMs"], r["tEndMs"])
g.held and g.finish() if hasattr(g, "finish") else g._close()

# build hyp intervals from flushes: each flush = (name, tStartMs, samples)
hyp = []
for name, t0, samp in g.flushes:
    hyp.append((t0/1000.0, t0/1000.0 + samp/SR, name))
gt = json.load(open(f"{S}/dgclip.gt.json")); dur = max(u["end"] for u in gt)
gt_iv = [(u["start"], u["end"], u["speaker"]) for u in gt]
acc, mapping = optimal_map_accuracy(frame_labels(gt_iv, dur), frame_labels(hyp, dur))
names = [f[0] for f in g.flushes]
from collections import Counter
print(f"GT: 3 speakers, {len(gt_switches(gt)[0])} switches")
print(f"TurnGate: {len(g.flushes)} flushes, {len(set(names))} speakers {sorted(set(names))}")
print(f"  flush-name histogram: {dict(Counter(names))}")
print(f"  frame accuracy vs GT (optimal map): {acc:.3f}")
print(f"  mapping: {mapping}")
