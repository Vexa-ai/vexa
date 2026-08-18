#!/usr/bin/env python3
"""Measure the judge+scorer against meetings whose true binding is known.

Ground truth is one line per virtual track — the pseudonym that track's audio
actually belongs to:

    {"tracks": {"csrc-201": "P2", "csrc-840": "P1"}}

Everything else follows: a segment is truly mislabeled iff its rendered label
differs from its track's true owner; a track is truly mislabeled iff its
dominant label differs from its true owner. That one field expresses both the
whole-track swap (26424) and the per-segment label churn (26298).

Reported metrics:
  flagged_precision  of the contradictions the scorer raised, the share that
                     land on genuinely mislabeled segments. This is the number
                     the gate is on: it must be 1.0.
  track_precision/recall   whole-track verdicts vs truth.
  segment_recall     share of truly mislabeled segments the two signals reach.
                     Expected to be low by construction — a backchannel with no
                     name in it is invisible to linguistic self-incrimination.
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


def calibrate(segments: list[dict], verdicts: dict, truth: dict, nm: NameMap) -> dict:
    true_owner = truth["tracks"]

    seg_label, per_track = {}, defaultdict(Counter)
    for s in segments:
        sid = s.get("segment_id")
        if not sid:
            continue
        lab = pseudonym_for((s.get("speaker") or "").strip(), nm)
        seg_label[sid] = lab
        per_track[track_of(s)][lab] += 1

    truly_bad_segments = {
        sid for sid, lab in seg_label.items()
        if track_of({"segment_id": sid}) in true_owner
        and lab != true_owner[track_of({"segment_id": sid})]
    }
    truly_bad_tracks = {
        t for t, c in per_track.items()
        if t in true_owner and c.most_common(1)[0][0] != true_owner[t]
    }

    flagged, flagged_tracks = [], set()
    for v in verdicts["tracks"]:
        for c in v["contradictions"]:
            flagged.append(c["segment_id"])
        if v["verdict"] == "MISLABELED":
            flagged_tracks.add(v["track"])

    tp = [s for s in flagged if s in truly_bad_segments]
    fp = [s for s in flagged if s not in truly_bad_segments]

    tt_tp = flagged_tracks & truly_bad_tracks
    tt_fp = flagged_tracks - truly_bad_tracks

    def ratio(a: int, b: int):
        return round(a / b, 4) if b else None

    return {
        "flagged_segments": len(flagged),
        "flagged_true_positive": len(tp),
        "flagged_false_positive": len(fp),
        "false_positive_ids": sorted(fp),
        "flagged_precision": ratio(len(tp), len(flagged)),
        "truly_mislabeled_segments": len(truly_bad_segments),
        "segment_recall": ratio(len(set(tp)), len(truly_bad_segments)),
        "truly_mislabeled_tracks": sorted(truly_bad_tracks),
        "flagged_tracks": sorted(flagged_tracks),
        "track_precision": ratio(len(tt_tp), len(flagged_tracks)),
        "track_recall": ratio(len(tt_tp), len(truly_bad_tracks)),
        "track_false_positives": sorted(tt_fp),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--name-map", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()

    res = calibrate(
        load_transcript(a.transcript),
        json.loads(pathlib.Path(a.verdicts).read_text()),
        json.loads(pathlib.Path(a.truth).read_text()),
        NameMap.load(a.name_map),
    )
    print(json.dumps(res, indent=1))
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
