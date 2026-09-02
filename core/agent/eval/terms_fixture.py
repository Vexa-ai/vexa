#!/usr/bin/env python3
"""terms_fixture.py — run decision 35's term extractor over a whole real transcript, offline.

    uv run python eval/terms_fixture.py ~/dna-fixtures/2026-03-02.transcript.json [<workspace dir>…]

WHY AN OFFLINE RUNNER AND NOT ONLY UNIT TESTS. The unit tests prove the rules on three-segment
inputs; they cannot tell you whether the thing is USABLE on a 677-segment meeting — whether it
returns forty terms or four hundred, whether it takes a millisecond or a second (it runs on every
Highlight press, inside a turn a person is waiting on), and how many of what it finds already have
pages. Those are product questions and only a real transcript answers them.

Reads a fixture and workspace directories; writes nothing, calls nothing, needs no stack. Fixtures
are private and live outside the repo, so the path is an argument and there is no default.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.terms import extract_terms, index_entries, match_known  # noqa: E402


def load(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    segs = raw.get("segments", raw) if isinstance(raw, dict) else raw
    return [{"id": i, "at": s.get("t"), "text": s.get("text") or ""} for i, s in enumerate(segs)]


def index_for(dirs: list[Path]) -> list[dict]:
    out: list[dict] = []
    for d in dirs:
        files = [str(f.relative_to(d)) for f in d.rglob("*.md")]
        out += index_entries(d.name, d.name, files)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    fixture = Path(argv[1]).expanduser()
    segments = load(fixture)
    index = index_for([Path(p).expanduser() for p in argv[2:]])

    t0 = time.perf_counter()
    rows = match_known(extract_terms(segments), index)
    ms = (time.perf_counter() - t0) * 1000

    known = [r for r in rows if r["known"]]
    print(f"fixture        {fixture.name}")
    print(f"segments       {len(segments)}")
    print(f"index          {len(index)} entity pages across {len(argv) - 2} workspace(s)")
    print(f"terms          {len(rows)}")
    print(f"  known        {len(known)}")
    print(f"  unknown      {len(rows) - len(known)}")
    print(f"time           {ms:.1f} ms")
    print()
    for r in rows[:25]:
        mark = "●" if r["known"] else "○"
        print(f"  {mark} {r['term']}  ({len(r['segments'])} segment(s), first at {r['first_at']})")
    if len(rows) > 25:
        print(f"  … {len(rows) - 25} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
