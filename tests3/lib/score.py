#!/usr/bin/env python3
"""Score pipeline output against ground truth.

Usage:
    # From REST API response + ground truth file:
    python3 score.py --gt ground-truth.json --segments rest-segments.json

    # Pipe segments from curl:
    curl -s $GATEWAY/transcripts/google_meet/$ID -H "X-API-Key: $TOKEN" \
        | python3 score.py --gt ground-truth.json --segments -

    # Output: score.json to stdout, human summary to stderr
"""

import argparse
import json
import sys
from difflib import SequenceMatcher

SIMILARITY_THRESHOLD = 0.70


def normalize(text):
    """Lowercase, strip punctuation, collapse whitespace."""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def similarity(a, b):
    """Sequence similarity between two strings (0-1)."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def score(ground_truth, segments):
    """Score segments against ground truth.

    ground_truth: list of {speaker, text, ...}
    segments: list of {speaker, text, ...}

    Returns dict with all metrics.
    """
    gt_count = len(ground_truth)
    seg_count = len(segments)

    # For each GT line, find best matching segment
    matched_seg_indices = set()
    results = []

    for gt in ground_truth:
        gt_text = gt["text"]
        gt_speaker = gt["speaker"]
        best_sim = 0
        best_idx = -1
        best_seg = None

        for i, seg in enumerate(segments):
            if i in matched_seg_indices:
                continue
            sim = similarity(gt_text, seg.get("text", ""))
            if sim > best_sim:
                best_sim = sim
                best_idx = i
                best_seg = seg

        if best_sim >= SIMILARITY_THRESHOLD and best_seg is not None:
            matched_seg_indices.add(best_idx)
            seg_speaker = best_seg.get("speaker", "")
            # Speaker match: check if GT speaker name appears in segment speaker
            # (handles "Alice" matching "Alice (Guest)" or "Vexa Speaker Alice")
            speaker_ok = (
                gt_speaker.lower() in seg_speaker.lower()
                or seg_speaker.lower() in gt_speaker.lower()
            )
            results.append({
                "gt_text": gt_text,
                "seg_text": best_seg.get("text", ""),
                "gt_speaker": gt_speaker,
                "seg_speaker": seg_speaker,
                "similarity": best_sim,
                "speaker_match": speaker_ok,
            })
        else:
            results.append({
                "gt_text": gt_text,
                "seg_text": None,
                "gt_speaker": gt_speaker,
                "seg_speaker": None,
                "similarity": best_sim,
                "speaker_match": False,
            })

    passed = [r for r in results if r["seg_text"] is not None]
    missed = [r for r in results if r["seg_text"] is None]
    speaker_fail = [r for r in passed if not r["speaker_match"]]
    content_fail = [r for r in passed if r["similarity"] < SIMILARITY_THRESHOLD]
    hallucinations = seg_count - len(matched_seg_indices)

    avg_sim = (
        sum(r["similarity"] for r in passed) / len(passed) if passed else 0
    )

    return {
        "gt_count": gt_count,
        "seg_count": seg_count,
        "pass": len(passed),
        "missed": len(missed),
        "speaker_fail": len(speaker_fail),
        "content_fail": len(content_fail),
        "hallucinations": hallucinations,
        "speaker_accuracy": (
            (len(passed) - len(speaker_fail)) / gt_count if gt_count else 0
        ),
        "avg_similarity": round(avg_sim, 6),
        "completeness": len(passed) / gt_count if gt_count else 0,
        "details": results,
    }


def load_segments(path_or_stdin):
    """Load segments from file or stdin. Handles both raw array and {segments:[]} wrapper."""
    if path_or_stdin == "-":
        data = json.load(sys.stdin)
    else:
        with open(path_or_stdin) as f:
            data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "segments" in data:
        return data["segments"]
    return data if isinstance(data, list) else []


def load_ground_truth(path):
    """Load ground truth. Handles both flat array and conversation format."""
    with open(path) as f:
        data = json.load(f)

    # Flat format: [{speaker, text, delay_ms}]
    if isinstance(data, list):
        return data

    # Conversation format: {utterances: [{speaker, text, start_after_ms}]}
    if isinstance(data, dict) and "utterances" in data:
        return [
            {"speaker": u["speaker"], "text": u["text"]}
            for u in data["utterances"]
        ]

    return data


def main():
    parser = argparse.ArgumentParser(description="Score pipeline output against ground truth")
    parser.add_argument("--gt", required=True, help="Ground truth JSON file")
    parser.add_argument("--segments", required=True, help="Segments JSON file (or - for stdin)")
    parser.add_argument("--details", action="store_true", help="Include per-line match details")
    args = parser.parse_args()

    gt = load_ground_truth(args.gt)
    segs = load_segments(args.segments)
    result = score(gt, segs)

    if not args.details:
        del result["details"]

    # Human-readable summary to stderr
    print(
        f"  gt={result['gt_count']} seg={result['seg_count']} "
        f"pass={result['pass']} missed={result['missed']} "
        f"hallucinations={result['hallucinations']}",
        file=sys.stderr,
    )
    print(
        f"  speaker_accuracy={result['speaker_accuracy']:.0%} "
        f"similarity={result['avg_similarity']:.1%} "
        f"completeness={result['completeness']:.0%}",
        file=sys.stderr,
    )

    # Machine-readable JSON to stdout
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
