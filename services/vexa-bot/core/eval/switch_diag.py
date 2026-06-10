#!/usr/bin/env python3
"""Per-switch diagnostic: for each Deepgram real switch, find the nearest
candidate from each signal source. Reveals WHICH switches each signal can
catch and which are invisible to all of them.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from switch_eval import gt_switches
from fast_sweep import replay

S = os.path.join(os.path.dirname(__file__), "samples")
gt = json.load(open(f"{S}/dgclip.gt.json"))
switches, turns = gt_switches(gt)

bnd = json.load(open(f"{S}/dgclip_bnd.boundaries.json"))["boundaries"]
pyn = {}  # kind -> [times]
for b in bnd:
    pyn.setdefault(b["kind"], []).append(b["tMs"]/1000.0)
all_pyn = sorted(b["tMs"]/1000.0 for b in bnd)
switch_pyn = sorted(b["tMs"]/1000.0 for b in bnd
                    if b["kind"] in ("speaker→speaker", "silence→speaker", "overlap-onset", "overlap-offset"))

emb = json.load(open(f"{S}/dgclip_bnd.emb.json"))
commits = replay(emb["records"], dict(newSpeakerThreshold=0.55, veryFarThreshold=0.90))
commit_bounds = sorted(c[1] for c in commits)  # every commit start = a cut
cluster_changes = sorted(commits[i][1] for i in range(1, len(commits))
                         if commits[i][0] != commits[i-1][0])

def nearest(t, arr):
    if not arr: return None, None
    best, bv = 1e9, None
    for x in arr:
        if abs(x-t) < best: best, bv = abs(x-t), x
    return bv, best

print(f"Deepgram {len(switches)} real switches. For each, nearest candidate (dt in s):\n")
print(f"{'switch@s':>9} | {'pyannote(any)':>22} | {'pyannote(switchy)':>20} | {'commit-cut':>10} | {'cluster-chg':>11}")
print("-"*86)
for sw in switches:
    pa, pad = nearest(sw, all_pyn)
    ps, psd = nearest(sw, switch_pyn)
    cb, cbd = nearest(sw, commit_bounds)
    cc, ccd = nearest(sw, cluster_changes)
    # which kind is the nearest switchy pyannote?
    kind = next((b["kind"] for b in bnd if abs(b["tMs"]/1000.0-ps)<1e-6), "") if ps else ""
    print(f"{sw:>9.1f} | {f'{pad:.2f} (@{pa:.1f})':>22} | {f'{psd:.2f} {kind[:8]}':>20} | "
          f"{f'{cbd:.2f}':>10} | {f'{ccd:.2f}':>11}")

def recall(arr, tol=0.75):
    return sum(1 for sw in switches if nearest(sw, arr)[1] <= tol) / len(switches)
print(f"\nrecall@0.75s:  pyannote(any) n={len(all_pyn)} r={recall(all_pyn):.3f} | "
      f"pyannote(switchy) n={len(switch_pyn)} r={recall(switch_pyn):.3f} | "
      f"commit-cut n={len(commit_bounds)} r={recall(commit_bounds):.3f} | "
      f"cluster-chg n={len(cluster_changes)} r={recall(cluster_changes):.3f}")
