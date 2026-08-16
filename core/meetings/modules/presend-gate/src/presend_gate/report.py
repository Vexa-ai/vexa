"""Run the gate over a directory of transcript payloads and print a results table.

    python -m presend_gate.report <dir> --creator "Name" --bot "Vexa test" [--out results.md]

Written for exactly one job: putting every record's verdict AND the numbers behind it in
front of a human, so a threshold can be argued with instead of trusted. Reads `*.json`
transcript payloads; writes Markdown to stdout or `--out`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .policy import Outcome, Policy, evaluate
from .record import from_transcript_payload

COLUMNS = [
    ("id", "record", "{}"),
    ("outcome", "verdict", "{}"),
    ("reasons", "why", "{}"),
    ("segment_count", "segs", "{}"),
    ("speaker_count", "spk", "{}"),
    ("roster_size", "roster", "{}"),
    ("counterparty_count", "cparty", "{}"),
    ("dialogue_window_share", "dlg-win", "{:.3f}"),
    ("alternation_rate", "altern", "{:.3f}"),
    ("monologue_ratio", "monolog", "{:.3f}"),
    ("speech_density", "density", "{}"),
    ("second_person_rate", "2nd-per", "{:.3f}"),
    ("domestic_rate", "domestic", "{:.3f}"),
    ("language_switch_rate", "lang-sw", "{:.3f}"),
]


def rows(directory: Path, creator: str | None, bots: list[str], policy: Policy):
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - operator feedback
            print(f"skip {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict) or "segments" not in payload:
            continue
        record = from_transcript_payload(payload, creator=creator, bot_names=bots)
        verdict = evaluate(record, policy)
        yield path.stem, record, verdict


def render(results) -> str:
    header = "| " + " | ".join(c[1] for c in COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [header, rule]
    tally = {o: 0 for o in Outcome}
    for stem, _record, verdict in results:
        tally[verdict.outcome] += 1
        data = verdict.signals.as_dict()
        data["id"] = stem
        data["outcome"] = verdict.outcome.value
        data["reasons"] = " · ".join(verdict.reasons)
        cells = []
        for key, _label, fmt in COLUMNS:
            value = data.get(key)
            cells.append("—" if value is None else (fmt.format(value) if isinstance(value, float) else str(value)))
        lines.append("| " + " | ".join(cells) + " |")
    summary = " · ".join(f"{o.value}: {tally[o]}" for o in Outcome)
    lines.append("")
    lines.append(f"**{sum(tally.values())} records — {summary}**")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="presend_gate.report")
    ap.add_argument("directory", type=Path)
    ap.add_argument("--creator", default=None, help="display name of the record owner")
    ap.add_argument("--bot", action="append", default=[], help="the bot's own display name (repeatable)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    results = list(rows(args.directory, args.creator, args.bot, Policy()))
    text = render(results)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
