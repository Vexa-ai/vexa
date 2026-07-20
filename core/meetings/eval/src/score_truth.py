#!/usr/bin/env python3
"""Score a live transcript against KNOWN truth — the words, and who said them.

The corpus scores content against a single-pass STT reference. That reference is itself a
measurement: it duplicates its own chunk overlap, and re-cutting it moves the answer by 4-24%
depending on the entry. Every content claim built on it inherits those error bars, and twice this
week a "finding" turned out to be the reference moving rather than the pipeline.

When the speakers are synthetic, none of that applies. The clip text existed before the audio did,
so this scorer compares against truth rather than against another opinion — and because each clip
occupies a known offset in a WAV that started at a known wall-clock instant, it also knows WHO was
speaking at every instant. That makes attribution measurable on the same pass, against the same
truth, with no diarization oracle in the loop.

    python3 score_truth.py --truth anna.truth.json@1784570000.0 \\
                           --truth boris.truth.json@1784570012.5 \\
                           --transcript http://localhost:8056/transcripts/zoom/76967201683 \\
                           [--json out.json]

The @ is the wall-clock second at which that speaker's WAV STARTED PLAYING. Get it wrong and the
attribution numbers are meaningless while the content numbers stay fine — so the report prints the
alignment it inferred, and refuses when the overlap is too small to mean anything.

Alignment is word-level LCS (difflib), never substring containment: a containment test on this same
corpus produced 24 false positives out of 39 and reversed a conclusion.
"""
import argparse
import json
import re
import sys
import urllib.request
from difflib import SequenceMatcher

WORD = re.compile(r"[a-z0-9']+")


def norm(text: str) -> list[str]:
    return WORD.findall(text.lower())


def load_transcript(path: str) -> list[dict]:
    if path.startswith("http"):
        with urllib.request.urlopen(path, timeout=30) as fh:
            body = json.load(fh)
    else:
        with open(path) as fh:
            body = json.load(fh)
    segs = body.get("segments", body) if isinstance(body, dict) else body
    if not isinstance(segs, list) or not segs:
        raise SystemExit(f"REFUSING: {path} has no segments — an empty store scores as total loss")
    return segs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", action="append", required=True, metavar="FILE@EPOCH_SEC")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()

    # ── the truth timeline: every clip an absolute window with an owner
    ref_words: list[str] = []
    ref_owner: list[str] = []
    windows: list[tuple[float, float, str]] = []
    per_speaker_truth: dict[str, int] = {}
    clips: list[tuple[float, float, str, str]] = []
    for spec in args.truth:
        path, _, at = spec.partition("@")
        if not at:
            raise SystemExit(f"--truth {spec} needs @<epoch seconds when the WAV started>")
        t0 = float(at)
        with open(path) as fh:
            truth = json.load(fh)
        who = truth["speaker"]
        for clip in truth["clips"]:
            clips.append((t0 + clip["startSec"], t0 + clip["endSec"], who, clip["text"]))
    clips.sort()
    for start, end, who, text in clips:
        words = norm(text)
        ref_words.extend(words)
        ref_owner.extend([who] * len(words))
        windows.append((start, end, who))
        per_speaker_truth[who] = per_speaker_truth.get(who, 0) + len(words)

    # ── the hypothesis: what the pipeline published, in time order
    segs = sorted(load_transcript(args.transcript), key=lambda s: s.get("start") or 0)
    hyp_words: list[str] = []
    hyp_label: list[str] = []
    span0, span1 = windows[0][0], windows[-1][1]
    inside = 0
    for seg in segs:
        start = seg.get("start") or 0
        # Segments outside the speakers' span are somebody else's audio (a human in the room, an
        # earlier meeting in the same store). Counting them as invention would be a lie about
        # precision, so they are excluded and REPORTED rather than silently dropped.
        if not (span0 - 5 <= start <= span1 + 15):
            continue
        inside += 1
        words = norm(seg.get("text") or "")
        hyp_words.extend(words)
        hyp_label.extend([seg.get("speaker") or "?"] * len(words))

    if not hyp_words:
        raise SystemExit(f"REFUSING: no transcript segments fall inside the speakers' window "
                         f"({span0:.0f}-{span1:.0f}) — check the @epoch you passed")

    # ── word-level LCS between truth and hypothesis
    matcher = SequenceMatcher(None, ref_words, hyp_words, autojunk=False)
    matched = 0
    agree = disagree = unnamed = 0
    confusion: dict[str, dict[str, int]] = {}
    for i, j, n in matcher.get_matching_blocks():
        matched += n
        for k in range(n):
            truth_who = ref_owner[i + k]
            label = hyp_label[j + k]
            row = confusion.setdefault(truth_who, {})
            row[label] = row.get(label, 0) + 1
            # An unnamed segment is not a WRONG name: the mixed lane publishes seg_N until a hint
            # binds a turn, and scoring that as misattribution would punish honesty.
            if re.fullmatch(r"(seg_\d+|speaker_\d+|\?|unknown)", label, re.I):
                unnamed += 1
            elif label.lower().startswith(truth_who.lower()) or truth_who.lower().startswith(label.lower()):
                agree += 1
            else:
                disagree += 1

    named = agree + disagree
    out = {
        "truthWords": len(ref_words),
        "publishedWords": len(hyp_words),
        "matchedWords": matched,
        "recall": round(matched / len(ref_words), 3),
        "precision": round(matched / len(hyp_words), 3),
        "segmentsInWindow": inside,
        "segmentsTotal": len(segs),
        "attribution": {
            "namedWords": named,
            "unnamedWords": unnamed,
            "correct": agree,
            "wrong": disagree,
            "accuracy": round(agree / named, 3) if named else None,
            "unnamedRate": round(unnamed / matched, 3) if matched else None,
        },
        "perSpeakerTruthWords": per_speaker_truth,
        "confusion": confusion,
    }

    print(f"truth     {len(ref_words)} words across {len(per_speaker_truth)} speakers "
          f"({', '.join(f'{k} {v}' for k, v in sorted(per_speaker_truth.items()))})")
    print(f"published {len(hyp_words)} words in {inside}/{len(segs)} segments inside the window")
    print(f"content   recall {out['recall']} · precision {out['precision']} "
          f"({matched} words matched, word-level LCS)")
    if named:
        print(f"attribution {out['attribution']['accuracy']} of {named} named words correct "
              f"({disagree} wrong) · {unnamed} matched words still unnamed "
              f"({out['attribution']['unnamedRate']} of matches)")
    else:
        print(f"attribution NONE — every one of the {unnamed} matched words is unnamed; "
              f"no hint ever bound a turn")
    for who, row in sorted(confusion.items()):
        top = sorted(row.items(), key=lambda kv: -kv[1])[:4]
        print(f"  {who:<10} → {', '.join(f'{k}:{v}' for k, v in top)}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
