#!/usr/bin/env python3
"""Deterministic scorer: judge flags + rendered labels -> per-track verdicts.

The judge is label-blind, so this join is where an error is actually declared.
No model runs here; given the same flags and the same transcript this produces
the same verdicts byte-for-byte, which is what makes it usable as a gate.

Evidence algebra
    vocative(named=X)  on a segment whose track is labeled X   -> CONTRADICTION
                       on a segment whose track is labeled !=X -> consistent
    self_id(named=X)   on a segment whose track is labeled X   -> SUPPORT
                       on a segment whose track is labeled !=X -> CONTRADICTION
                                                                  (implies X)

A track is MISLABELED when it carries at least `--min-evidence` contradictions
and strictly more contradictions than supports. Everything else is CLEAN or
INSUFFICIENT. Segment-level contradictions are what the fixture manifest pins;
the track verdict is what the fleet metric counts.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from judge import load_transcript, track_of  # noqa: E402
from pseudonymize import NameMap, pseudonym_for  # noqa: E402


def score(segments: list[dict], flags: list[dict], nm: NameMap, min_evidence: int = 1) -> dict:
    seg_by_id = {s["segment_id"]: s for s in segments if s.get("segment_id")}

    tracks: dict[str, Counter] = defaultdict(Counter)
    for s in segments:
        lab = pseudonym_for((s.get("speaker") or "").strip(), nm)
        tracks[track_of(s)][lab] += 1

    label_of = {t: (c.most_common(1)[0][0] if c else None) for t, c in tracks.items()}
    size_of = {t: sum(c.values()) for t, c in tracks.items()}

    per_track: dict[str, dict] = {
        t: {
            "track": t, "label": label_of[t], "segments": size_of[t],
            "contradictions": [], "supports": [],
        }
        for t in tracks
    }

    for f in flags:
        seg = seg_by_id.get(f["segment_id"])
        if seg is None:
            continue
        t = track_of(seg)
        seg_label = pseudonym_for((seg.get("speaker") or "").strip(), nm)
        rec = {
            "segment_id": f["segment_id"], "signal": f["signal"], "named": f["named"],
            "label": seg_label, "t_start": f.get("t_start"), "quote_span": f.get("quote_span"),
        }
        if f["signal"] == "vocative":
            if seg_label is not None and seg_label == f["named"]:
                rec["implies"] = None  # only "not X"
                per_track[t]["contradictions"].append(rec)
        else:  # self_id
            if seg_label == f["named"]:
                per_track[t]["supports"].append(rec)
            else:
                rec["implies"] = f["named"]
                per_track[t]["contradictions"].append(rec)

    for t, v in per_track.items():
        nc, ns = len(v["contradictions"]), len(v["supports"])
        if nc >= min_evidence and nc > ns:
            v["verdict"] = "MISLABELED"
        elif ns > 0 and nc == 0:
            v["verdict"] = "CLEAN"
        else:
            v["verdict"] = "INSUFFICIENT"
        implied = [c["implies"] for c in v["contradictions"] if c.get("implies")]
        v["implied_label"] = Counter(implied).most_common(1)[0][0] if implied else None
        # Two-participant fallback: a vocative only proves "not X", but when the
        # roster holds exactly two people that names the other one uniquely.
        if v["implied_label"] is None and v["verdict"] == "MISLABELED":
            others = [p for p in nm.pseudonyms if p != v["label"]]
            if len(others) == 1:
                v["implied_label"] = others[0]

    return {
        "tracks": [per_track[t] for t in sorted(per_track)],
        "totals": {
            "tracks": len(per_track),
            "mislabeled": sum(1 for v in per_track.values() if v["verdict"] == "MISLABELED"),
            "flags": len(flags),
            "contradictions": sum(len(v["contradictions"]) for v in per_track.values()),
            "supports": sum(len(v["supports"]) for v in per_track.values()),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--flags", required=True)
    ap.add_argument("--name-map", required=True)
    ap.add_argument("--min-evidence", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    segs = load_transcript(a.transcript)
    flags = [json.loads(ln) for ln in pathlib.Path(a.flags).read_text().splitlines() if ln.strip()]
    res = score(segs, flags, NameMap.load(a.name_map), a.min_evidence)
    pathlib.Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res["totals"]))


if __name__ == "__main__":
    main()
