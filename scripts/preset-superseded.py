#!/usr/bin/env python3
"""Regenerate core/agent/control_plane/preset_superseded.json from git history.

Every version of every `behavior/asks/*.md` this repository ever committed, except the current one,
by sha256. `preset_library.top_up` refreshes a `_global/asks/` file whose bytes match one of these:
nobody edited it, it is simply an older copy of ours. Run after changing a preset:

    python3 scripts/preset-superseded.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "core" / "agent" / "control_plane" / "preset_superseded.json"


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def main() -> None:
    table: dict[str, set[str]] = {}
    revs = git("log", "--format=%H", "--", "behavior/asks").decode().split()
    for rev in revs:
        for name in git("ls-tree", "--name-only", rev, "behavior/asks/").decode().split("\n"):
            if not name.endswith(".md"):
                continue
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{name}"], capture_output=True)
            if blob.returncode == 0:
                table.setdefault(Path(name).name, set()).add(hashlib.sha256(blob.stdout).hexdigest())
    for f in (ROOT / "behavior" / "asks").glob("*.md"):
        table.get(f.name, set()).discard(hashlib.sha256(f.read_bytes()).hexdigest())
    out = {k: sorted(v) for k, v in sorted(table.items()) if v}
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {sum(len(v) for v in out.values())} superseded versions of {len(out)} presets")


if __name__ == "__main__":
    main()
