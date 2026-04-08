#!/usr/bin/env python3
"""Score transcription-replay output against ground truth.

Reads the parsed GT (replay-gt.json) and captured pipeline output (replay-output.json),
fuzzy-matches each GT utterance to the best output segment, then reports:
  - completeness:     fraction of GT lines matched (SequenceMatcher >= 0.5)
  - speaker_accuracy: fraction of matched lines with correct speaker attribution
  - avg_similarity:   mean fuzzy score across matched pairs
  - persistence:      total segment count from REST API

Exit 0 if completeness >= 0.7 and speaker_accuracy >= 0.6, else exit 1.

Usage:
    python3 replay-score.py --gt .state/replay-gt.json \
                            --output .state/replay-output.json \
                            --results .state/replay-results.json
"""

import argparse
import json
import re
import sys
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.5
COMPLETENESS_PASS = 0.7
SPEAKER_ACCURACY_PASS = 0.6


def normalize(text):
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def similarity(a, b):
    """SequenceMatcher ratio between normalized strings (0-1)."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def speaker_match(gt_speaker, seg_speaker):
    """Check if GT speaker name matches segment speaker.

    Handles partial matches: "Chris Davis" in "Chris Davis (Guest)",
    or "Raj" matching "Raj".
    """
    gt = gt_speaker.lower().strip()
    seg = seg_speaker.lower().strip()
    # Exact match
    if gt == seg:
        return True
    # Substring match (either direction)
    if gt in seg or seg in gt:
        return True
    # Check first name match (for short names like "Raj")
    gt_first = gt.split()[0] if gt else ''
    seg_first = seg.split()[0] if seg else ''
    if gt_first and seg_first and (gt_first == seg_first):
        return True
    return False


def load_gt(path):
    """Load ground truth from replay-gt.json."""
    with open(path) as f:
        data = json.load(f)
    return data.get('utterances', [])


def load_output(path):
    """Load pipeline output segments. Handles {segments:[]} wrapper or raw array."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and 'segments' in data:
        return data['segments']
    return []


def score(gt_utterances, segments):
    """Score segments against ground truth utterances."""
    gt_count = len(gt_utterances)
    seg_count = len(segments)

    matched_seg_indices = set()
    details = []
    matched_count = 0
    speaker_correct = 0
    total_similarity = 0.0

    for gt in gt_utterances:
        gt_text = gt['text']
        gt_speaker = gt['speaker']

        best_sim = 0.0
        best_idx = -1
        best_seg = None

        for i, seg in enumerate(segments):
            if i in matched_seg_indices:
                continue
            sim = similarity(gt_text, seg.get('text', ''))
            if sim > best_sim:
                best_sim = sim
                best_idx = i
                best_seg = seg

        if best_sim >= MATCH_THRESHOLD and best_seg is not None:
            matched_seg_indices.add(best_idx)
            matched_count += 1
            total_similarity += best_sim

            seg_speaker = best_seg.get('speaker', 'Unknown')
            spk_ok = speaker_match(gt_speaker, seg_speaker)
            if spk_ok:
                speaker_correct += 1

            detail = {
                'gt_speaker': gt_speaker,
                'gt_text': gt_text[:80],
                'seg_speaker': seg_speaker,
                'seg_text': best_seg.get('text', '')[:80],
                'similarity': round(best_sim, 4),
                'speaker_match': spk_ok,
                'matched': True,
            }

            # Latency: if both have timestamps, compute delta
            gt_send_ts = gt.get('send_ts')
            seg_ts = best_seg.get('timestamp') or best_seg.get('start_time')
            if gt_send_ts and seg_ts:
                detail['gt_send_ts'] = gt_send_ts
                detail['seg_ts'] = seg_ts

            details.append(detail)
        else:
            details.append({
                'gt_speaker': gt_speaker,
                'gt_text': gt_text[:80],
                'seg_speaker': None,
                'seg_text': None,
                'similarity': round(best_sim, 4),
                'speaker_match': False,
                'matched': False,
            })

    completeness = matched_count / gt_count if gt_count else 0.0
    speaker_accuracy = speaker_correct / matched_count if matched_count else 0.0
    avg_similarity = total_similarity / matched_count if matched_count else 0.0

    return {
        'gt_count': gt_count,
        'seg_count': seg_count,
        'matched': matched_count,
        'missed': gt_count - matched_count,
        'speaker_correct': speaker_correct,
        'completeness': round(completeness, 4),
        'speaker_accuracy': round(speaker_accuracy, 4),
        'avg_similarity': round(avg_similarity, 4),
        'persistence_segments': seg_count,
        'details': details,
    }


def main():
    parser = argparse.ArgumentParser(description='Score transcription-replay output')
    parser.add_argument('--gt', required=True, help='Ground truth JSON (replay-gt.json)')
    parser.add_argument('--output', required=True, help='Pipeline output JSON (replay-output.json)')
    parser.add_argument('--results', required=True, help='Output results JSON path')
    args = parser.parse_args()

    gt_utterances = load_gt(args.gt)
    segments = load_output(args.output)
    results = score(gt_utterances, segments)

    # Write results
    with open(args.results, 'w') as f:
        json.dump(results, f, indent=2)

    # Print human summary to stderr
    print(
        f"  gt={results['gt_count']} seg={results['seg_count']} "
        f"matched={results['matched']} missed={results['missed']}",
        file=sys.stderr,
    )
    print(
        f"  completeness={results['completeness']:.0%} "
        f"speaker_accuracy={results['speaker_accuracy']:.0%} "
        f"avg_similarity={results['avg_similarity']:.1%}",
        file=sys.stderr,
    )
    print(
        f"  persistence={results['persistence_segments']} segments",
        file=sys.stderr,
    )

    # Exit code: pass/fail based on thresholds
    if results['completeness'] >= COMPLETENESS_PASS and results['speaker_accuracy'] >= SPEAKER_ACCURACY_PASS:
        print('  PASS: thresholds met', file=sys.stderr)
        sys.exit(0)
    else:
        reasons = []
        if results['completeness'] < COMPLETENESS_PASS:
            reasons.append(f"completeness {results['completeness']:.0%} < {COMPLETENESS_PASS:.0%}")
        if results['speaker_accuracy'] < SPEAKER_ACCURACY_PASS:
            reasons.append(f"speaker_accuracy {results['speaker_accuracy']:.0%} < {SPEAKER_ACCURACY_PASS:.0%}")
        print(f"  FAIL: {', '.join(reasons)}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
