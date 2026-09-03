#!/usr/bin/env python
"""First-pass truth sidecars, from the transcript, tagged `unvalidated`.

The judge column needs something to judge against, and an EMPTY sidecar is worse than none: every
item in a note scores as invented, and the column reads like a quality verdict while measuring the
sidecar. This fills the stubs from the full transcript with an LLM pass.

**What comes out of here is not fact.** It is a first pass, it keeps `unvalidated: true`, and only
a human may remove that tag — the same discipline as the raise vault. `score.py` keeps an
LLM-extracted sidecar in the `judge_unvalidated` column, never in the validated one, so nothing
downstream can mistake a machine's reading of a meeting for what the meeting decided.

It reads the WHOLE transcript, which is the point: the product under test sees a truncated copy,
and a truth built from the same truncated copy could never expose that.

    python truth_extract.py --fixtures ~/dna-fixtures [--only 2026-03-02] [--model sonnet]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess

ASK = """You are building a TRUTH RECORD for a meeting, from its complete transcript. It will be
used to score summaries of this meeting, so it must contain what the meeting actually settled --
not what it discussed.

Reply with ONLY a JSON object, no prose and no code fence:

{"decided":   ["<what was settled> · <who settled it>", ...],
 "committed": ["<who> · <what they will do> · <by when, or 'no date'>", ...],
 "open":      ["<what was raised and deliberately left unresolved>", ...],
 "notes":     "<two sentences of context a reader would need>"}

Rules:
- A DECISION is a choice the group actually made. "We should look into X" is not a decision.
- A COMMITMENT has a named owner. No owner, no entry.
- OPEN is for what was raised and left unresolved on purpose, not for everything unfinished.
- Use the speaker names exactly as they appear in the transcript.
- Empty lists are correct answers. Invent nothing.

TRANSCRIPT:
"""


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


def extract(transcript: str, model: str) -> dict | None:
    return ask_json(ASK + transcript, model)


def yaml_list(items) -> str:
    if not items:
        return " []"
    return "\n" + "\n".join(f"  - {json.dumps(str(i))}" for i in items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    d = pathlib.Path(a.fixtures)

    for tx in sorted(d.glob("*.transcript.json")):
        date = tx.name.split(".")[0]
        if a.only and a.only != date:
            continue
        side = d / f"{date}.truth.yaml"
        cur = side.read_text() if side.exists() else ""
        if not all(re.search(rf"^{k}:\s*\[\s*\]", cur, re.M) for k in ("decided", "committed", "open")):
            print(f"{date}: already filled — left alone", flush=True)
            continue
        fx = json.loads(tx.read_text())
        body = "\n".join(f"{s.get('speaker','?')}: {s.get('text','')}" for s in fx["segments"])
        out = extract(body, a.model)
        if not out:
            print(f"{date}: extraction failed — left alone", flush=True)
            continue
        side.with_suffix(".yaml.bak").write_text(cur)
        new = cur
        # `lambda _: value`, never a replacement STRING: re.sub processes backslash escapes in a
        # replacement, and json.dumps emits \uXXXX for any non-ASCII — so a meeting whose minutes
        # contain an em dash crashed the writer with "bad escape \u" AFTER the extraction had
        # already succeeded. The most expensive part of the run, thrown away at the last step.
        for key in ("decided", "committed", "open"):
            body = f"{key}:{yaml_list(out.get(key) or [])}"
            new = re.sub(rf"(?m)^{key}:\s*\[\s*\].*$", lambda _m, b=body: b, new, count=1)
        note = "notes: " + json.dumps(str(out.get("notes") or "").replace("\n", " "))
        new = re.sub(r"(?m)^notes:.*$", lambda _m: note, new, count=1)
        if "source: llm-first-pass" not in new:
            new += ("\n# FIRST PASS, MACHINE-READ FROM THE FULL TRANSCRIPT. Not fact. `unvalidated`\n"
                    "# stays until a human who was in the room removes it, and only a human may.\n"
                    "source: llm-first-pass\n")
        side.write_text(new)
        print(f"{date}: decided={len(out.get('decided') or [])} "
              f"committed={len(out.get('committed') or [])} open={len(out.get('open') or [])}",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
