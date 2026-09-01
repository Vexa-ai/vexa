#!/usr/bin/env python
"""Score a replay: the mechanical dimensions first, the judge second.

Mechanical means *from the server's own rules* -- what the run DID, before what it SAID. Every
dimension is 0..1 with the evidence that produced it, so a number can always be argued with.

The judge (``claude -p --model sonnet``, fixed schema, against the truth sidecar) lands in
``judge_unvalidated`` while the sidecar still carries ``unvalidated: true``. Only a human removes
that tag; until then the judge is reported beside the score and never folded into it.

    python score.py --run ~/dna-runs/r1 --fixtures ~/dna-fixtures
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess

MECHANICAL = ["note_shape", "transcript_depth", "prepare_mail", "minutes_mail",
              "opening_prep", "opening_minutes", "compounding"]


def frac(hits: list[bool]) -> float:
    return round(sum(1 for h in hits if h) / len(hits), 3) if hits else 0.0


def words(s: str) -> int:
    return len((s or "").split())


# ── dimensions ───────────────────────────────────────────────────────────────────────────────────

def d_note_shape(rec: dict) -> tuple[float, dict]:
    note = rec.get("note") or ""
    if not note:
        return 0.0, {"why": "no committed note"}
    body = note
    fm = re.match(r"^---\n([\s\S]*?)\n---\n", note)
    if fm:
        body = note[fm.end():]
    sections = {s: bool(re.search(rf"^#+\s*{s}\b", body, re.M | re.I))
                for s in ("Decided", "Committed", "Open")}
    items = re.findall(r"^\s*[-*]\s+(.+)$", body, re.M)
    attributed = [bool(re.search(r"\[\[[^\]]+\]\]", i)) for i in items]
    links = re.findall(r"\[\[([^\]]+)\]\]", note)
    meta = bool(re.search(r"\b(I (will|have|'ll|'ve)|Let me|as an AI|I created|I'll now)\b", body, re.I))
    hits = [bool(fm), *sections.values(),
            bool(items) and frac(attributed) >= 0.6,
            bool(links), not meta]
    return frac(hits), {"frontmatter": bool(fm), "sections": sections, "items": len(items),
                        "attributed_frac": frac(attributed), "wikilinks": len(set(links)),
                        "meta_commentary": meta}


def d_transcript_depth(rec: dict, fx: dict) -> tuple[float, dict]:
    """THE COPY-CAP TEST. Build the terms that appear ONLY past the delivered prefix, and ask
    whether the note used any. A note that cannot cite the back of the meeting was not shown it."""
    note = (rec.get("note") or "") + " " + str((rec.get("opening_minutes") or {}).get("reply") or "")
    if not note.strip():
        return 0.0, {"why": "nothing to check"}
    segs = fx["segments"]
    cap = rec.get("transcript_chars_delivered") or 0
    seen, cut = 0, len(segs)
    for i, s in enumerate(segs):
        seen += len(s.get("speaker", "")) + len(s.get("text", "")) + 2
        if seen > cap:
            cut = i
            break
    head = " ".join(s.get("text", "") for s in segs[:cut]).lower()
    tail = " ".join(s.get("text", "") for s in segs[cut:]).lower()
    tok = re.compile(r"[a-z][a-z'\-]{5,}")
    only_tail = {w for w in tok.findall(tail)} - {w for w in tok.findall(head)}
    nl = note.lower()
    used = sorted(w for w in only_tail if w in nl)
    ev = {"delivered_chars": cap, "full_chars": rec.get("transcript_chars_full"),
          "segments_delivered": cut, "segments_total": len(segs),
          "tail_only_terms": len(only_tail), "tail_terms_used": used[:12]}
    if cut >= len(segs):
        return 1.0, {**ev, "why": "the whole transcript was delivered"}
    if not only_tail:
        return 0.0, {**ev, "why": "tail has no distinctive vocabulary — inconclusive"}
    return (1.0 if used else 0.0), ev


def d_prepare_mail(rec: dict) -> tuple[float, dict]:
    mail = rec.get("prepare_mail")
    if not mail:
        return 0.0, {"why": "no prepare mail"}
    body = mail["body"]
    lines = [l for l in body.strip().splitlines() if l.strip()]
    links = re.findall(r"https?://\S+", body)
    composes = [l for l in links if "ask=prep" in l and "meeting=" in l]
    hits = [len(lines) <= 5, len(links) == 1, bool(composes)]
    return frac(hits), {"lines": len(lines), "links": len(links),
                        "composes_prep_chat": bool(composes), "link": links[:1]}


def d_minutes_mail(rec: dict) -> tuple[float, dict]:
    mail, note = rec.get("minutes_mail"), (rec.get("note") or "")
    if not mail:
        return 0.0, {"why": "no minutes mail"}
    body = mail["body"]
    links = re.findall(r"https?://\S+", body)
    verbatim = False
    if note:
        core = [l.strip() for l in note.splitlines() if len(l.strip()) > 25][:6]
        verbatim = bool(core) and frac([c in body for c in core]) >= 0.8
    hits = [verbatim, len(links) == 1,
            any("ask=minutes-review" in l and "meeting=" in l for l in links)]
    return frac(hits), {"note_verbatim": verbatim, "links": len(links)}


def _opening(rec: dict, key: str, word_cap: int | None) -> tuple[float, dict]:
    o = rec.get(key) or {}
    reply = o.get("reply") or ""
    if not reply:
        return 0.0, {"why": "no opening turn"}
    qs = reply.count("?")
    first = reply.strip().split("\n")[0]
    tells_first = "?" not in first
    hits = [tells_first, qs == 1, "paste" not in reply.lower()]
    ev = {"words": words(reply), "questions": qs, "tells_before_asking": tells_first,
          "says_paste": "paste" in reply.lower()}
    if word_cap:
        hits.append(words(reply) < word_cap)
        ev["under_word_cap"] = words(reply) < word_cap
    return frac(hits), ev


def d_compounding(rec: dict, earlier: list[dict]) -> tuple[float, dict]:
    """Prep for meeting N must name something from a meeting < N. Nothing earlier ⇒ not scored."""
    if not earlier:
        return -1.0, {"why": "first meeting — nothing to compound from"}
    reply = ((rec.get("opening_prep") or {}).get("reply") or "").lower()
    if not reply:
        return 0.0, {"why": "no prep opening"}
    tok = re.compile(r"[a-z][a-z'\-]{5,}")
    prior = set()
    for e in earlier:
        prior |= set(tok.findall(((e.get("note") or "") + " " + (e.get("title") or "")).lower()))
    generic = set(tok.findall(reply[:0]))          # placeholder; prior-only is the signal
    hit = sorted(w for w in prior if w in reply)
    named = [e["date"] for e in earlier if e["date"] in reply]
    return (1.0 if (named or len(hit) >= 3) else 0.0), {
        "prior_meetings": len(earlier), "named_earlier_dates": named, "shared_terms": hit[:10]}


# ── the judge ────────────────────────────────────────────────────────────────────────────────────

SCHEMA = ("Reply with ONLY a JSON object, no prose, no code fence, with exactly these integer keys "
          "(0-100 unless stated): decisions_recalled, decisions_invented (count), decisions_missed "
          "(count), owners_correct, open_items_correct, attributions_wrong (count), overall.")


def judge(rec: dict, truth: str, model: str) -> dict:
    note = rec.get("note")
    if not note:
        return {"skipped": "no note"}
    prompt = (
        "You are scoring a meeting note against a truth sidecar. Be strict and literal: an item is "
        "recalled only if the truth contains it, invented only if the truth contradicts or omits it.\n\n"
        f"{SCHEMA}\n\n=== TRUTH SIDECAR ===\n{truth}\n\n=== THE NOTE UNDER TEST ===\n{note[:12000]}\n")
    try:
        p = subprocess.run(["claude", "-p", "--model", model, prompt],
                           capture_output=True, text=True, timeout=300)
    except Exception as e:                                        # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    out = (p.stdout or "").strip()
    m = re.search(r"\{[\s\S]*\}", out)
    if not m:
        return {"error": "judge returned no json", "raw": out[:300]}
    try:
        return json.loads(m.group(0))
    except Exception:                                             # noqa: BLE001
        return {"error": "judge json did not parse", "raw": out[:300]}


# ── main ─────────────────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    replay = json.loads((run / "replay.json").read_text())
    fixdir = pathlib.Path(a.fixtures)

    rows, earlier = [], []
    for rec in replay["records"]:
        date = rec["date"]
        fx_path = fixdir / f"{date}.transcript.json"
        fx = json.loads(fx_path.read_text()) if fx_path.exists() else {"segments": []}
        truth_path = fixdir / f"{date}.truth.yaml"
        truth = truth_path.read_text() if truth_path.exists() else ""
        unvalidated = "unvalidated: true" in truth

        dims, ev = {}, {}
        dims["note_shape"], ev["note_shape"] = d_note_shape(rec)
        dims["transcript_depth"], ev["transcript_depth"] = d_transcript_depth(rec, fx)
        dims["prepare_mail"], ev["prepare_mail"] = d_prepare_mail(rec)
        dims["minutes_mail"], ev["minutes_mail"] = d_minutes_mail(rec)
        dims["opening_prep"], ev["opening_prep"] = _opening(rec, "opening_prep", None)
        dims["opening_minutes"], ev["opening_minutes"] = _opening(rec, "opening_minutes", 100)
        dims["compounding"], ev["compounding"] = d_compounding(rec, earlier)

        counted = [v for k, v in dims.items() if v >= 0]
        row = {"date": date, "title": rec.get("title"), "dims": dims, "evidence": ev,
               "score": round(sum(counted) / len(counted), 3) if counted else 0.0,
               "latency_s": rec.get("latency_s"), "note_sha": rec.get("note_sha"),
               "error": rec.get("error")}
        if not a.no_judge:
            j = judge(rec, truth, a.judge_model)
            row["judge_unvalidated" if unvalidated else "judge_validated"] = j
        rows.append(row)
        earlier.append(rec)
        print(f"{date}  score={row['score']:.3f}  " +
              " ".join(f"{k}={v:g}" for k, v in dims.items()), flush=True)

    scored = [r["score"] for r in rows if not r.get("error")]
    out = {"rev": replay.get("rev"), "run": str(run), "rows": rows,
           "mean_score": round(sum(scored) / len(scored), 3) if scored else 0.0,
           "fixtures_scored": len(scored),
           "dim_means": {d: round(sum(r["dims"][d] for r in rows if r["dims"][d] >= 0)
                                  / max(1, sum(1 for r in rows if r["dims"][d] >= 0)), 3)
                         for d in MECHANICAL}}
    (run / "scores.json").write_text(json.dumps(out, indent=1))
    print("\nmean", out["mean_score"], "over", out["fixtures_scored"], "fixtures")
    print("dims", json.dumps(out["dim_means"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
