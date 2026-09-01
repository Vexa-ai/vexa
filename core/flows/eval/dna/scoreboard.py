#!/usr/bin/env python
"""One row per revolution, in the run root, refusing a revolution that changed nothing.

The fingerprint is ``hash(fixture set) + line SHA + preset/prompt hashes``. Same fingerprint as the
last row means nothing under test moved: the loop writes nothing and says so (loop-safe). Every row
names the LAYER that changed -- meta-software (data/text, hot) or software (an image) -- because a
score that only ever moves when software changes is itself a finding: the meta-software is not
liquid enough.

    python scoreboard.py --run ~/dna-runs/r1 --fixtures ~/dna-fixtures \
                         --layer software --changed "ports.py commit seam"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess

HEADER = ("| rev | when | fingerprint | layer | what changed | fixtures | mean | note_shape | "
          "depth | prep mail | minutes mail | open prep | open min | compounding | judge |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def fingerprint(fixtures: pathlib.Path, line_sha: str, presets: dict) -> str:
    h = hashlib.sha256()
    for f in sorted(fixtures.glob("*.transcript.json")):
        h.update(f.name.encode()); h.update(str(f.stat().st_size).encode())
    h.update(line_sha.encode())
    h.update(json.dumps(presets, sort_keys=True).encode())
    return h.hexdigest()[:12]


def line_sha(repo: str) -> str:
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:                                             # noqa: BLE001
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--layer", choices=["meta-software", "software", "none"], default="none")
    ap.add_argument("--changed", default="")
    ap.add_argument("--force", action="store_true", help="write even on an unchanged fingerprint")
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    scores = json.loads((run / "scores.json").read_text())
    replay = json.loads((run / "replay.json").read_text())
    board = run.parent / "SCOREBOARD.md"

    fp = fingerprint(pathlib.Path(a.fixtures), line_sha(a.repo), replay.get("preset_hashes", {}))
    prior = board.read_text() if board.exists() else ""
    if fp in prior and not a.force:
        print(f"refused: fingerprint {fp} is already on the board — nothing under test changed")
        return 2

    d = scores["dim_means"]
    judges = [r.get("judge_unvalidated", {}).get("overall") for r in scores["rows"]]
    judges = [j for j in judges if isinstance(j, (int, float))]
    jcol = f"{sum(judges) / len(judges):.0f} (unvalidated)" if judges else "—"
    row = (f"| r{scores.get('rev')} | {replay.get('started', '')} | `{fp}` | {a.layer} | "
           f"{a.changed or '—'} | {scores['fixtures_scored']} | **{scores['mean_score']:.3f}** | "
           + " | ".join(f"{d[k]:.2f}" for k in
                        ["note_shape", "transcript_depth", "prepare_mail", "minutes_mail",
                         "opening_prep", "opening_minutes", "compounding"])
           + f" | {jcol} |")

    if not prior:
        board.write_text("# DNA fixture replay — the scoreboard\n\n"
                         "One row per revolution. `layer` names what changed: **meta-software** "
                         "(presets, prompts, flow params — hot) or **software** (an image). The "
                         "judge column stays marked *unvalidated* until a human removes the tag "
                         "from the truth sidecars.\n\n" + HEADER + "\n" + row + "\n")
    else:
        board.write_text(prior.rstrip("\n") + "\n" + row + "\n")
    print(f"wrote {board}\n{row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
