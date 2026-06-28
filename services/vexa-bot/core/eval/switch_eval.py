#!/usr/bin/env python3
"""Switch-detection scorer (pack #394 RnD).

PRIMARY metric per user: "we want to know when speakers switched - exactly
the place. Speaker clusters is secondary."

A speaker SWITCH = a point in time where the active speaker identity changes.
We derive the ground-truth switch set from Deepgram GT by collapsing adjacent
same-speaker utterances and taking the boundaries between different speakers.

Then we score candidate switch signals against it:
  - pyannote raw boundary events (speaker->speaker, silence->speaker, etc)
  - our committed cluster changes (consecutive commits, different label)

recall    = fraction of GT switches with a candidate within +/- tol
precision = fraction of candidates within tol of some GT switch
med_dt    = median |candidate - matched GT switch| (ms), placement accuracy

Usage:
  switch_eval.py --gt samples/dgclip.gt.json \
      --boundaries samples/dgclip_bnd.boundaries.json \
      [--emb samples/dgclip_bnd.emb.json] [--tol 0.75]
"""
import json, argparse, statistics, sys, os
sys.path.insert(0, os.path.dirname(__file__))


def gt_switches(gt):
    """Collapse adjacent same-speaker turns; return list of switch times (s)
    = the start of each turn whose speaker differs from the previous turn."""
    turns = []
    for u in gt:
        if turns and turns[-1]["speaker"] == u["speaker"]:
            turns[-1]["end"] = u["end"]
        else:
            turns.append(dict(speaker=u["speaker"], start=u["start"], end=u["end"]))
    switches = [turns[i]["start"] for i in range(1, len(turns))]
    return switches, turns


def match(cands, gts, tol):
    """Greedy one-to-one match within tol. Returns (recall, precision, med_dt_ms,
    matched pairs)."""
    cands = sorted(cands)
    gts = sorted(gts)
    used_c = set()
    pairs = []
    for g in gts:
        best, bi = tol + 1, -1
        for i, c in enumerate(cands):
            if i in used_c:
                continue
            d = abs(c - g)
            if d < best:
                best, bi = d, i
        if bi >= 0 and best <= tol:
            used_c.add(bi)
            pairs.append((g, cands[bi], best))
    recall = len(pairs) / len(gts) if gts else 0.0
    precision = len(pairs) / len(cands) if cands else 0.0
    med_dt = statistics.median([p[2] for p in pairs]) * 1000 if pairs else None
    return recall, precision, med_dt, pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--boundaries")
    ap.add_argument("--emb", help="emb.json: also score cluster-change switches")
    ap.add_argument("--tol", type=float, default=0.75)
    a = ap.parse_args()

    gt = json.load(open(a.gt))
    switches, turns = gt_switches(gt)
    print(f"GT: {len(gt)} utterances -> {len(turns)} speaker-turns "
          f"-> {len(switches)} real switches  (tol=±{a.tol}s)")
    print("  switch times (s):", [round(s, 1) for s in switches])
    print()

    if a.boundaries:
        data = json.load(open(a.boundaries))
        bnds = data["boundaries"]
        by_kind = {}
        for b in bnds:
            by_kind.setdefault(b["kind"], []).append(b["tMs"] / 1000.0)
        print(f"pyannote raw boundaries: {len(bnds)} total")
        for k, v in sorted(by_kind.items()):
            print(f"   {k:>16}: {len(v)}")
        print()
        # candidate sets to evaluate
        sets = {
            "speaker->speaker only": by_kind.get("speaker→speaker", []),
            "spk->spk + sil->spk": by_kind.get("speaker→speaker", []) + by_kind.get("silence→speaker", []),
            "all non-overlap": [b["tMs"]/1000.0 for b in bnds if b["kind"] in
                                ("speaker→speaker", "silence→speaker", "speaker→silence")],
            "all boundaries": [b["tMs"]/1000.0 for b in bnds],
        }
        print(f"{'candidate signal':>26} | {'n':>4} {'recall':>6} {'prec':>6} {'med_dt_ms':>9}")
        print("-" * 64)
        for name, cands in sets.items():
            r, p, dt, _ = match(cands, switches, a.tol)
            f1 = 2*r*p/(r+p) if (r+p) else 0
            print(f"{name:>26} | {len(cands):>4} {r:>6.3f} {p:>6.3f} "
                  f"{(f'{dt:.0f}' if dt is not None else 'n/a'):>9}  F1={f1:.3f}")
        print()

    if a.emb:
        from fast_sweep import replay
        emb = json.load(open(a.emb))
        for cfg in [
            dict(newSpeakerThreshold=0.70, veryFarThreshold=0.95, minSeedUtteranceMs=1500, newClusterCooldownMs=2000),
            dict(newSpeakerThreshold=0.75, veryFarThreshold=0.90, minSeedUtteranceMs=1500, newClusterCooldownMs=2000),
        ]:
            commits = replay(emb["records"], cfg)
            sw = [commits[i][1] for i in range(1, len(commits)) if commits[i][0] != commits[i-1][0]]
            r, p, dt, _ = match(sw, switches, a.tol)
            f1 = 2*r*p/(r+p) if (r+p) else 0
            print(f"cluster-change nst={cfg['newSpeakerThreshold']} vft={cfg['veryFarThreshold']}: "
                  f"{len(sw)} switches  recall={r:.3f} prec={p:.3f} "
                  f"med_dt={f'{dt:.0f}ms' if dt else 'n/a'}  F1={f1:.3f}")


if __name__ == "__main__":
    main()
