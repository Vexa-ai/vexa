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

OLD_EVENT_CAP = 8000   # what meeting.completed used to carry; now the comparison boundary

def note_belongs_to(rec: dict, fx: dict) -> bool:
    """Is the note this run collected actually about THIS meeting?

    The replay takes the next new note committed to the workspace, which is right only while it is
    the sole writer. When it is not — another probe, a trial, a person in the terminal — the fixture
    in flight adopts a stranger's note and every dimension then scores it as though it were the
    right one. Nothing about that looks like a failure, which is what makes it worth a check.

    A note about this meeting shares SOME six-word run with this meeting's transcript, anywhere in
    it. A note about a different meeting shares none. This is not `transcript_depth` — that asks
    about the part of the transcript beyond the old cap, and a note could pass this while failing
    that (shallow but genuine) or fail this while passing that (impossible, and if it ever happens
    the check is broken)."""
    note = rec.get("note") or ""
    if not note.strip():
        return True                      # nothing collected: a separate failure, not a wrong note
    whole = "\n".join(f"{x.get('speaker','?')}: {x.get('text','')}" for x in fx["segments"])
    return bool(_phrases(note) & _phrases(whole))


MECHANICAL = ["note_shape", "transcript_depth", "prepare_mail", "minutes_mail",
              "opening_prep", "opening_minutes", "compounding",
              "entities_touched", "names_linked"]


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


def _phrases(text: str, n: int = 6) -> set:
    """Every n-word window that carries at least one long content word.

    Without the content-word filter this leaked: a six-word window of pure function words
    ("do you want to …") matches between any two English texts and certified a note as having read
    a part of the meeting it was never shown."""
    ws = re.findall(r"[a-z0-9']+", text.lower())
    out = set()
    for i in range(len(ws) - n + 1):
        win = ws[i:i + n]
        if any(len(w) >= 6 for w in win):
            out.add(" ".join(win))
    return out


def d_transcript_depth(rec: dict, fx: dict) -> tuple[float, dict]:
    """THE COPY-CAP TEST — did the product see past the prefix it was handed?

    Split the meeting at the last character actually DELIVERED, then ask whether the note (or the
    minutes opening) contains a six-word phrase that occurs only after that point.

    Getting this honest took three tries and every failure was the same mistake — treating a common
    word as evidence. "Any six-letter word absent from the head" scored a perfect 1 on a note that
    had seen 11% of the meeting: `before`, `decide`, `during` are simply not in the first twelve
    minutes of a call that opens with greetings. A repetition threshold did not save it (`question`,
    `start`, `technical` are what every meeting note says), and neither did proper nouns: the
    delivered prefix carries SPEAKER LABELS, so every participant name is already known, and in a
    `Speaker: text` rendering the first word of every line reads as capitalised.

    A six-word verbatim phrase carrying a real content word does not appear by accident. Four
    words was not enough: "do you want to" matched, and certified a note that had seen 15% of its
    meeting as having read the rest. It is deliberately CONSERVATIVE: a
    note that covers the tail entirely in paraphrase scores 0. That is the right way to be wrong
    here -- a false 0 costs a re-read, and the false 1 this check kept producing would have
    certified a fix that changed nothing. `names_used` is still reported, unscored, to read by eye."""
    note = (rec.get("note") or "") + "\n" + str((rec.get("opening_minutes") or {}).get("reply") or "")
    if not note.strip():
        return 0.0, {"why": "nothing to check"}
    segs = fx["segments"]
    # THE BOUNDARY IS FIXED AT 8,000 CHARACTERS — the size of the copy the event used to carry —
    # and NOT at whatever this run delivered. That keeps one question across the change: "did the
    # note use anything past the first 8,000 characters?" Before the fix it could not, because it
    # was never shown them; after, it can. Moving the boundary to `delivered` would make the check
    # unfailable the moment delivery became complete, and a dimension that cannot fail is
    # decoration — it would have reported a perfect score for a fix that changed nothing.
    cap = OLD_EVENT_CAP
    seen, cut = 0, len(segs)
    for i, sg in enumerate(segs):
        seen += len(sg.get("speaker", "")) + len(sg.get("text", "")) + 2
        if seen > cap:
            cut = i
            break

    def render(a, b):                       # exactly the shape the product delivers
        return "\n".join(f"{x.get('speaker','?')}: {x.get('text','')}" for x in segs[a:b])

    head, tail = render(0, cut), render(cut, len(segs))
    ev = {"boundary_chars": cap,
          "delivered_in_event": rec.get("transcript_chars_delivered"),
          "full_chars": rec.get("transcript_chars_full"),
          "segments_delivered": cut, "segments_total": len(segs),
          "delivered_frac": round(cut / max(1, len(segs)), 3)}
    if cut >= len(segs):
        return 1.0, {**ev, "why": "the whole transcript was delivered"}

    tail_only = _phrases(tail) - _phrases(head)
    hits = sorted(tail_only & _phrases(note))
    ev.update(tail_only_phrases=len(tail_only), phrases_used=hits[:5])
    if not tail_only:
        return 0.0, {**ev, "why": "the tail carries no distinct phrasing — inconclusive"}
    return (1.0 if hits else 0.0), ev


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
    # CONTENT PRESENT AND READABLE, not verbatim. #1390 made the mail carry a rendered version of
    # the note on purpose — frontmatter stripped, wikilinks flattened, links made absolute — so a
    # verbatim check was measuring the old intent and scoring the new one as a regression. What
    # matters is that the note's SUBSTANCE arrived and that it reads as prose to someone who will
    # never open the workspace.
    readable = present = False

    def flat(x):
        """Both sides through the same sieve. The first version of this flattened only the NOTE
        and compared it against the raw mail, so every line the note wrote as `**bold**` failed to
        match the identical bold line in the mail — and the check reported 'the mail carries none
        of the note' about a mail that carried all of it, on every fixture. A dimension that always
        answers the same thing is telling you about itself, not about the product."""
        x = re.sub(r"\[\[([^\]]+)\]\]", r"\1", x)
        return re.sub(r"[#*`>_]", "", x).strip().lower()

    if note:
        body_l = flat(body)
        fm = re.match(r"^---\n[\s\S]*?\n---\n", note)
        core = [l.strip() for l in (note[fm.end():] if fm else note).splitlines()
                if len(l.strip()) > 25]
        core = [flat(c) for c in core][:8]
        present = bool(core) and frac([c[:60] in body_l for c in core]) >= 0.5
        readable = ("---" not in body.split("\n")[0]) and "[[" not in body
    hits = [present, readable, len(links) == 1,
            any("ask=minutes-review" in l and "meeting=" in l for l in links)]
    return frac(hits), {"note_content_present": present, "renders_readable": readable,
                        "links": len(links)}


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


# ── the entity write-back (PRD decision 24.4) ────────────────────────────────────────────────────
#
# Two numbers, because decision 24 has two halves and they fail differently. A run can write plenty
# of pages and still leave every name in the note dead (`entities_touched` high, `names_linked`
# low), and it can write beautifully linked prose that created nothing (the reverse). Averaging them
# into one score would hide exactly the distinction the change is meant to move.

# A capitalised RUN of two or more words — "Sony Pictures Imageworks", "Cottalango Leon". Single
# capitalised words are deliberately not counted: at the start of a sentence every word is one, and
# a measure that fires on "The" and "Monday" tells you about English, not about the product.
# `and` and `the` are NOT intra-name particles, and the first version of this had them: it read
# "Blue Light Card and Kaar Tech" as ONE name, undercounting by exactly the amount a note listing
# several dead names does. A measure's own bugs bias in the direction that flatters the product.
_BARE_NAME = re.compile(r"\b[A-Z][a-zA-Z'’\-]+(?:\s+(?:of|de|del|van|von|da|di)\s+[A-Z][a-zA-Z'’\-]+"
                        r"|\s+[A-Z][a-zA-Z'’\-]+)+")
_FENCE = re.compile(r"```[\s\S]*?```")
# Headings and list labels are formatting, not mentions; a `## Decided` never wanted a wikilink.
_HEADING = re.compile(r"^#{1,6}\s.*$", re.M)
_NOT_A_NAME = {"Open Items", "Action Items", "Next Steps", "Open Questions", "Decided Committed"}

# ⚠ MEASURED FALSE POSITIVE, removed after the first baseline run. Over ten real DNA notes the
# measure flagged "Complete SSO", "Ask ASF", "Co-author TAC", "Lead TAC", "Attend SIGGRAPH",
# "Escalate GitHub", "Await PR" — every one of them a Committed-section bullet, which by convention
# opens with an imperative verb and is followed by an acronym. Those are not names anybody failed to
# link; counting them inflated the deficit by roughly a third and would have made the fix look
# better than it was. A measure's own bugs bias in whichever direction flatters the change, so they
# get found before the change is scored, not after.
_LEADING_VERB = {
    "add", "address", "agree", "answer", "ask", "assign", "attend", "await", "book", "bring",
    "build", "check", "circulate", "clarify", "close", "co-author", "complete", "confirm",
    "consider", "continue", "create", "decide", "define", "deliver", "deploy", "discuss", "draft",
    "escalate", "explore", "file", "finalize", "finalise", "find", "fix", "follow", "get", "give",
    "identify", "include", "investigate", "invite", "keep", "land", "lead", "let", "look", "make",
    "merge", "move", "open", "organise", "organize", "pick", "plan", "post", "prepare", "present",
    "propose", "provide", "publish", "raise", "reach", "read", "record", "report", "request",
    "resolve", "review", "revisit", "run", "schedule", "send", "set", "share", "should", "start",
    "submit", "support", "take", "test", "track", "update", "verify", "wait", "work", "write",
}
_POSSESSIVE = re.compile(r"[\u2019']s$")


def unlinked_names(note: str) -> list:
    """Capitalised multi-word names in the note that are NOT inside a `[[wikilink]]` or a markdown
    link. This is a PROXY and says so: it cannot know that "Technical Steering Committee" deserves
    a page. It is a good proxy because it is exactly the failure decision 24 names — a name went
    past and nothing was created — and because it can only be improved by writing the page."""
    if not note:
        return []
    fm = re.match(r"^---\n[\s\S]*?\n---\n", note)
    body = note[fm.end():] if fm else note
    body = _FENCE.sub(" ", body)
    body = _HEADING.sub(" ", body)
    body = re.sub(r"\[\[[^\]]+\]\]", " ", body)          # already a chip
    body = re.sub(r"\[[^\]]+\]\([^)]*\)", " ", body)      # already a link
    out = []
    for m in _BARE_NAME.finditer(body):
        n = _POSSESSIVE.sub("", " ".join(m.group(0).split())).strip()
        if not n or n in _NOT_A_NAME or n in out:
            continue
        if n.split()[0].lower() in _LEADING_VERB:
            continue
        out.append(n)
    return out


def d_entities_touched(rec: dict) -> tuple[float, dict]:
    """Entity pages written per agent turn. One page per turn scores 1.0.

    The target is deliberately modest. Decision 24 is about a floor — a turn that met a name and
    created nothing — not about volume, and a dimension that rewards volume would be gamed by an
    agent writing a page per sentence."""
    files = rec.get("entity_files")
    if files is None:
        return -1.0, {"why": "this run predates the entity measure — not scored"}
    turns = max(1, int(rec.get("entity_turns") or 1))
    per_turn = len(files) / turns
    return round(min(1.0, per_turn), 3), {"entity_files": files[:12], "pages": len(files),
                                          "turns": turns, "per_turn": round(per_turn, 3)}


def d_names_linked(rec: dict) -> tuple[float, dict]:
    """Bare capitalised names left unlinked in the note. Five of them scores zero."""
    note = rec.get("note") or ""
    if not note:
        return 0.0, {"why": "no committed note"}
    bare = unlinked_names(note)
    return round(max(0.0, 1.0 - len(bare) / 5.0), 3), {"unlinked": bare[:12], "count": len(bare)}


# ── the judge ────────────────────────────────────────────────────────────────────────────────────

def ask_json(prompt: str, model: str, timeout: int = 900) -> dict | None:
    """Run one `claude -p` and get a JSON object back — through a FILE, never through stdout.

    The CLI answers in its own voice: a long structured reply comes back as a TLDR summary and the
    object itself never reaches stdout at all, so a scraper reads 147 characters of prose and
    reports "extraction failed". This is the same lesson the flows engine already learned in
    `feedback_turn` — the agent WRITES its answer to an agreed path and the caller reads the file.
    Ask for the artifact, not for the transcript of producing it."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d) / "out.json"
        ask = (prompt + f"\n\nWRITE YOUR JSON OBJECT — and nothing else — to the file {out}. "
                        "Use the Write tool. Do not print it. Your reply text is ignored.")
        try:
            subprocess.run(["claude", "-p", "--model", model,
                            "--permission-mode", "acceptEdits", ask],
                           capture_output=True, text=True, timeout=timeout)
        except Exception:                                         # noqa: BLE001
            return None
        if not out.exists():
            return None
        raw = out.read_text()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:                                             # noqa: BLE001
        return None


SCHEMA = ("Reply with ONLY a JSON object, no prose, no code fence, with exactly these integer keys "
          "(0-100 unless stated): decisions_recalled, decisions_invented (count), decisions_missed "
          "(count), owners_correct, open_items_correct, attributions_wrong (count), overall, "
          # decision 24.4: the entity pages are scored for GROUNDING, never for volume.
          "and — only when an ENTITY PAGES section is present below — entities_supported (0-100: "
          "how much of what those pages assert is supported by the truth or the note) and "
          "entities_invented (count of claims on them that are neither).")


def truth_is_empty(truth: str) -> bool:
    """A sidecar whose decided/committed/open are all `[]` states nothing to be judged against."""
    return all(re.search(rf"^{k}:\s*\[\s*\]", truth, re.M) for k in ("decided", "committed", "open"))


def judge(rec: dict, truth: str, model: str) -> dict:
    note = rec.get("note")
    if not note:
        return {"skipped": "no note"}
    if truth_is_empty(truth):
        # Judging a note against an empty sidecar returns a uniformly near-zero column that READS
        # like a quality verdict and is actually a measurement of the sidecar. Every recalled item
        # scores as invented, because the truth contains nothing to recall.
        return {"skipped": "truth sidecar is an empty stub — nothing to judge against",
                "next": "fill decided/committed/open (a human, or the plan's first-pass LLM "
                        "extraction tagged unvalidated) before this column means anything"}
    # The entity pages are part of what this turn produced, and they are mailed to nobody — which
    # is exactly why they need a judge. A fabricated line on a person's page is invisible in a way
    # a fabricated line in a note is not: nobody reads it until it is quoted back as knowledge.
    pages = rec.get("entity_pages") or {}
    pages_block = ""
    if pages:
        pages_block = ("\n\n=== ENTITY PAGES THIS TURN WROTE ===\n"
                       + "\n\n".join(f"--- {p} ---\n{c}" for p, c in pages.items())[:12000])
    prompt = (
        "You are scoring a meeting note against a truth sidecar. Be strict and literal: an item is "
        "recalled only if the truth contains it, invented only if the truth contradicts or omits it.\n\n"
        f"{SCHEMA}\n\n=== TRUTH SIDECAR ===\n{truth}\n\n=== THE NOTE UNDER TEST ===\n{note[:12000]}\n"
        + pages_block)
    got = ask_json(prompt, model, timeout=600)
    return got if got is not None else {"error": "judge produced no json file"}


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
        dims["entities_touched"], ev["entities_touched"] = d_entities_touched(rec)
        dims["names_linked"], ev["names_linked"] = d_names_linked(rec)

        counted = [v for k, v in dims.items() if v >= 0]
        belongs = note_belongs_to(rec, fx)
        row = {"date": date, "title": rec.get("title"), "dims": dims, "evidence": ev,
               "note_belongs_to_this_meeting": belongs,
               "stamp_matched": rec.get("stamp_matched"),
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

    contaminated = [r["date"] for r in rows if not r.get("note_belongs_to_this_meeting", True)]
    if contaminated:
        print("\nCONTAMINATED — the note collected is not about this meeting; excluded from the "
              "means: " + ", ".join(contaminated), flush=True)
    ok = [r for r in rows if not r.get("error")
          and r.get("note_belongs_to_this_meeting", True)]
    scored = [r["score"] for r in ok]
    out = {"rev": replay.get("rev"), "run": str(run), "rows": rows,
           "mean_score": round(sum(scored) / len(scored), 3) if scored else 0.0,
           "fixtures_scored": len(scored), "contaminated": contaminated,
           "dim_means": {d: round(sum(r["dims"][d] for r in ok if r["dims"][d] >= 0)
                                  / max(1, sum(1 for r in ok if r["dims"][d] >= 0)), 3)
                         for d in MECHANICAL}}
    (run / "scores.json").write_text(json.dumps(out, indent=1))
    print("\nmean", out["mean_score"], "over", out["fixtures_scored"], "fixtures")
    print("dims", json.dumps(out["dim_means"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
