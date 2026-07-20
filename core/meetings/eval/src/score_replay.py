#!/usr/bin/env python3
"""Score a REPLAY's transcript for content — the thing a corpus entry alone cannot do.

A corpus entry stores the transcript the ORIGINAL live session produced, so it can score that
session and nothing else. The moment a lane change is made, the question is "did this diff lose
words?" — and that needs the transcript the CHANGED code produces from the same audio, which no
stored artifact can supply.

`quality-mixed.test.ts TRANSCRIPT_OUT=<f>` writes exactly that. This scores it against either:

  * a corpus entry's single-pass reference — same model, one pass, on a real session, or
  * a synthetic fixture's truth sidecar — ABSOLUTE truth, since the clip text is known

    python3 src/score_replay.py <replay.json> --truth <fixture.truth.json>
    python3 src/score_replay.py <replay.json> --reference <entry/reference.txt>

Recall is what the pipeline kept of what was said; precision is how much of what it published was
never said. Both order-preserving (LCS), so a word recovered out of sequence is not a match.
"""
import argparse
import json

from single_pass_truth import lcs, words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("replay", help="TRANSCRIPT_OUT from a lane replay")
    ap.add_argument("--truth", help="a synthetic fixture's .truth.json (absolute truth)")
    ap.add_argument("--reference", help="a corpus entry's reference.txt (single-pass truth)")
    ap.add_argument("--json", help="write the score block here")
    args = ap.parse_args()
    if not args.truth and not args.reference:
        raise SystemExit("one of --truth or --reference is required")

    if args.truth:
        turns = json.load(open(args.truth))["turns"]
        ref_w = words(" ".join(t["text"] for t in turns))
        kind = "known text (absolute)"
    else:
        ref_w = words(open(args.reference).read())
        kind = "single-pass reference"

    segs = json.load(open(args.replay))["segments"]
    rt_w = words(" ".join(s.get("text", "") for s in segs))
    m = lcs(ref_w, rt_w)

    block = {
        "referenceKind": kind,
        "referenceWords": len(ref_w),
        "replayWords": len(rt_w),
        "matched": m,
        "recall": round(m / max(1, len(ref_w)), 3),
        "precision": round(m / max(1, len(rt_w)), 3),
    }
    print(f"REFERENCE ({kind}): {len(ref_w)} words")
    print(f"REPLAY             : {len(rt_w)} words")
    print(f"\n  recall    {block['recall']:.3f}   ({m}/{len(ref_w)} kept, in order)")
    print(f"  precision {block['precision']:.3f}   ({len(rt_w) - m} published words that were never said)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(block, f, indent=2)
        print(f"  written: {args.json}")


if __name__ == "__main__":
    main()
