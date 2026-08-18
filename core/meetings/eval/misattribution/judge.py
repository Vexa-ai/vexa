#!/usr/bin/env python3
"""The two-signal misattribution judge.

Only two signal classes are admitted, because only these two are precise enough
to label an attribution error without a human who was in the room:

  VOCATIVE  — a segment that addresses someone by name ("thanks, P2",
              "P2, let me interrupt you") is NOT spoken by that person.
  SELF_ID   — a segment that identifies the speaker ("I'm P2", "this is P2")
              binds the speaking track TO that person.

Explicitly out of scope, by ruling: turn-taking logic, register / style
heuristics, roster-absence heuristics. They produce plausible flags, and a
plausible flag in a regression gate is worse than no flag.

The judge is **label-blind**: it never sees the rendered speaker label, only
segment ids and pseudonymized text. That is what makes the scorer's join an
independent test rather than a confirmation of what the pipeline already said.

Sub-commands
    prepare   transcript -> pseudonymized prompt bundles (+ local name map)
    run       prompt bundles -> raw model responses  (backends below)
    ingest    raw responses -> normalized judge output JSONL

Backends for `run`:
    anthropic  ANTHROPIC_API_KEY + the Messages API (default; --model)
    cli        `claude -p` headless
    prepared   read responses already produced for these exact prompts
               (the deterministic replay path: same prompts in, same file out)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pseudonymize import NameMap, build_name_map, redact  # noqa: E402

DEFAULT_MODEL = "claude-opus-5"
CHUNK = 120

SYSTEM = """You label speaker-attribution evidence in meeting transcripts.

You see numbered transcript segments and a roster of pseudonymous participants
(P1, P2, ...). You do NOT see who the transcript claims said each segment, and
you must not guess it. Your only job is to extract two kinds of evidence.

1. VOCATIVE — the segment addresses a roster participant by name: a greeting,
   a thank-you, a direct question, an interruption, a hand-off.
     "thanks, P2"  /  "P2, let me interrupt you"  /  "so nice to meet P2"
   Direction is always not_speaker: whoever spoke this segment is NOT P2.

2. SELF_ID — the speaker names themselves.
     "I'm P2"  /  "this is P2"  /  "P2 here"  /  "my name is P2"
   Direction is always is_speaker: whoever spoke this segment IS P2.

HARD RULES — precision is the only thing that matters here.
- Emit nothing unless the name appears literally in the segment text.
- A third-person mention is NOT evidence. "P2 said the API is down",
  "I'll ask P2 later", "P2's team" — skip all of these. Only a direct address
  (vocative) or a direct self-identification counts.
- "Thanks" or "yes" with no name is not evidence.
- A name inside a quotation of someone else's speech is not evidence.
- If a segment could be read either as an address or as a mention, SKIP IT.
- Never infer from turn order, topic, tone, register, or who is missing.
- quote_span must be copied verbatim from the segment text.

Return ONLY a JSON array, no prose, no markdown fence:
[{"segment_id":"...","signal":"vocative","named":"P2",
  "direction":"not_speaker","quote_span":"thanks, P2"}]
Return [] when a batch has no evidence."""


def load_transcript(path: str) -> list[dict]:
    """Accept transcript.jsonl (one segment per line) or a {"segments":[...]} doc."""
    txt = pathlib.Path(path).read_text().strip()
    if txt.startswith("{") and '"segments"' in txt[:400]:
        return json.loads(txt)["segments"]
    if txt.startswith("["):
        return json.loads(txt)
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def track_of(seg: dict) -> str:
    """Virtual-track key: 'csrc-201:1:...' -> 'csrc-201'; 'ch-0:...' -> 'ch-0'."""
    sid = str(seg.get("segment_id") or "")
    return sid.split(":", 1)[0] if sid else "?"


def roster_of(segments: list[dict], extra: list[str]) -> list[str]:
    """Roster = rendered labels + declared attendees, first-seen order."""
    out: list[str] = []
    for s in segments:
        lab = (s.get("speaker") or "").strip()
        if lab and lab not in out:
            out.append(lab)
    for e in extra:
        e = (e or "").strip()
        if e and e not in out:
            out.append(e)
    return out


def build_prompts(segments: list[dict], nm: NameMap) -> list[dict]:
    """One prompt per CHUNK of segments. Label-blind by construction."""
    bundles = []
    for i in range(0, len(segments), CHUNK):
        part = segments[i : i + CHUNK]
        lines = []
        for s in part:
            sid = s.get("segment_id")
            text = redact(s.get("text") or "", nm).strip()
            if not text:
                continue
            lines.append(f"[{sid}] {text}")
        if not lines:
            continue
        body = (
            f"Roster: {', '.join(nm.pseudonyms)}\n\n"
            "Segments:\n" + "\n".join(lines) + "\n\nJSON array only."
        )
        bundles.append({"chunk": i // CHUNK, "prompt": body})
    return bundles


def cmd_prepare(a: argparse.Namespace) -> None:
    segs = load_transcript(a.transcript)
    extra = json.loads(pathlib.Path(a.attendees).read_text()) if a.attendees else []
    nm = build_name_map(roster_of(segs, extra))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    nm.dump(a.name_map)  # MUST be outside the repo
    bundles = build_prompts(segs, nm)
    (out / "prompts.json").write_text(json.dumps(bundles, indent=1))
    (out / "system.txt").write_text(SYSTEM)
    print(f"segments={len(segs)} roster={len(nm.pseudonyms)} prompts={len(bundles)} -> {out}")


def _call_anthropic(system: str, prompt: str, model: str) -> str:
    import anthropic  # imported lazily: the prepared backend needs no SDK

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=8000, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _call_cli(system: str, prompt: str, model: str) -> str:
    p = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--append-system-prompt", system],
        capture_output=True, text=True, timeout=600,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[:400])
    return p.stdout


def cmd_run(a: argparse.Namespace) -> None:
    d = pathlib.Path(a.bundle)
    bundles = json.loads((d / "prompts.json").read_text())
    system = (d / "system.txt").read_text()
    resp_dir = d / "responses"
    resp_dir.mkdir(exist_ok=True)
    for b in bundles:
        f = resp_dir / f"chunk-{b['chunk']:03d}.txt"
        if f.exists():
            continue
        if a.backend == "prepared":
            raise SystemExit(f"prepared backend: missing response {f}")
        fn = _call_anthropic if a.backend == "anthropic" else _call_cli
        f.write_text(fn(system, b["prompt"], a.model))
        print(f"chunk {b['chunk']} ok")
    print(f"responses: {len(list(resp_dir.glob('chunk-*.txt')))}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_VALID_SIG = {"vocative", "self_id"}
_VALID_DIR = {"not_speaker", "is_speaker"}


def parse_response(txt: str) -> list[dict]:
    m = _FENCE.search(txt)
    if m:
        txt = m.group(1)
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j < i:
        return []
    try:
        return json.loads(txt[i : j + 1])
    except json.JSONDecodeError:
        return []


def cmd_ingest(a: argparse.Namespace) -> None:
    d = pathlib.Path(a.bundle)
    segs = {s["segment_id"]: s for s in load_transcript(a.transcript) if s.get("segment_id")}
    nm = NameMap.load(a.name_map)
    rows, dropped = [], 0
    for f in sorted((d / "responses").glob("chunk-*.txt")):
        for r in parse_response(f.read_text()):
            sid = r.get("segment_id")
            sig, named, direction = r.get("signal"), r.get("named"), r.get("direction")
            # Structural validation: the scorer downstream is deterministic and
            # must never see a malformed or hallucinated row.
            if sid not in segs or sig not in _VALID_SIG or direction not in _VALID_DIR:
                dropped += 1
                continue
            if named not in nm.pseudonyms:
                dropped += 1
                continue
            if sig == "vocative" and direction != "not_speaker":
                dropped += 1
                continue
            if sig == "self_id" and direction != "is_speaker":
                dropped += 1
                continue
            span = (r.get("quote_span") or "").strip()
            if span and span.casefold() not in redact(segs[sid].get("text") or "", nm).casefold():
                dropped += 1  # span not verbatim -> not trustworthy evidence
                continue
            rows.append({
                "segment_id": sid, "track": track_of(segs[sid]), "signal": sig,
                "named": named, "direction": direction, "quote_span": span,
                "t_start": segs[sid].get("start"),
            })
    seen, uniq = set(), []
    for r in rows:
        k = (r["segment_id"], r["signal"], r["named"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    pathlib.Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in uniq))
    print(f"flags={len(uniq)} dropped={dropped} -> {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--transcript", required=True)
    p.add_argument("--attendees", help="JSON array of declared attendee names")
    p.add_argument("--name-map", required=True, help="write path; keep OUTSIDE the repo")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("run")
    p.add_argument("--bundle", required=True)
    p.add_argument("--backend", choices=["anthropic", "cli", "prepared"], default="anthropic")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("ingest")
    p.add_argument("--bundle", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--name-map", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_ingest)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
