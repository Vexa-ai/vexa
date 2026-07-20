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
from itertools import permutations

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

    # ── word-level LCS, ONE SPEAKER AT A TIME
    #
    # The obvious implementation interleaves both speakers' clips into one reference by absolute
    # time and aligns that against the transcript. It is wrong, and expensively so: LCS is
    # order-sensitive, so an error in the @EPOCH scrambles the reference order and the resulting
    # mismatch is billed to the pipeline as lost words. Measured on a WORD-PERFECT transcript,
    # interleaved scoring: +5s → recall 0.871, +30s → 0.607, +120s → 0.587. A 2-second slip is
    # entirely ordinary — the speakers report AUDIO_START when the join completes, not when the
    # first sample leaves the fake device — so that version would have invented double-digit
    # "streaming loss" out of a clock.
    #
    # Scoring each speaker's own WAV against the whole transcript removes the clock from the
    # content numbers completely: the order inside one speaker's truth is fixed by their own file,
    # exactly, and no cross-speaker ordering is ever needed. The epoch survives only as a coarse
    # window filter, where seconds do not matter.
    # Speakers are aligned in sequence against the words still unclaimed, never independently
    # against the whole transcript: independent passes let two speakers claim the SAME published
    # word (both say "the"), which on a word-perfect transcript cost 25 words of precision and
    # misattributed those same 25. Disjoint assignment is what makes a perfect run score perfect.
    # Which speaker goes first is not neutral, so try every order and keep the best total — with a
    # handful of speakers this is trivial, and it removes the arbitrariness from the number.
    def assign(order: list[str]) -> tuple[dict[str, list[tuple[int, int]]], int]:
        """→ per-speaker [(hyp index, truth index)], total matched, for one speaker order."""
        remaining = list(range(len(hyp_words)))
        got: dict[str, list[tuple[int, int]]] = {}
        total = 0
        for who in order:
            own = [w for w, o in zip(ref_words, ref_owner) if o == who]
            pool = [hyp_words[x] for x in remaining]
            hits: list[tuple[int, int]] = []
            for i, j, n in SequenceMatcher(None, own, pool, autojunk=False).get_matching_blocks():
                hits.extend((remaining[j + k], i + k) for k in range(n))
            got[who] = hits
            total += len(hits)
            taken = {h for h, _ in hits}
            remaining = [x for x in remaining if x not in taken]
        return got, total

    best: dict[str, list[tuple[int, int]]] = {}
    matched = 0
    for order in permutations(sorted(per_speaker_truth)):
        got, total = assign(list(order))
        if total > matched:
            best, matched = got, total

    agree = disagree = unnamed = 0
    confusion: dict[str, dict[str, int]] = {}
    claimed: set[int] = set()
    per_speaker_matched: dict[str, int] = {}
    pairs: list[tuple[str, str]] = []  # (truth speaker, published label) per matched word
    for who, hits in best.items():
        per_speaker_matched[who] = len(hits)
        for hyp_i, _ in hits:
            claimed.add(hyp_i)
            pairs.append((who, hyp_label[hyp_i]))
    for truth_who, label in pairs:
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
        # Precision counts each published word ONCE even if two speakers' truth both claim it (a
        # shared "the" will), so it stays a real fraction of what was published.
        "precision": round(len(claimed) / len(hyp_words), 3),
        "perSpeakerRecall": {k: round(per_speaker_matched.get(k, 0) / v, 3) for k, v in per_speaker_truth.items()},
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
          f"({matched} words matched, word-level LCS, speakers assigned disjointly)")
    print(f"          per speaker: {', '.join(f'{k} {v}' for k, v in sorted(out['perSpeakerRecall'].items()))}")
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
