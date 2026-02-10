#!/usr/bin/env python3
"""
Sort environment variables in a .env file alphabetically by variable name.
Usage: python scripts/sort_env.py [path]
  If path is omitted, sorts .env in the repo root.
Lines that are comments (start with #) or blank are kept at the top in original order.
All KEY=VALUE lines are sorted by KEY (case-sensitive).
"""
import re
import sys
from pathlib import Path

def main():
    repo_root = Path(__file__).resolve().parent.parent
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / ".env"
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    preamble = []  # comment lines and blanks, in order
    vars_list = []  # (key, full_line)

    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            preamble.append(line)
        else:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
            if m:
                key = m.group(1)
                vars_list.append((key, line))

    vars_list.sort(key=lambda x: x[0])
    out = "\n".join(preamble)
    if preamble:
        out += "\n"
    out += "\n".join(l for _, l in vars_list)
    path.write_text(out + "\n")
    print(f"Sorted {len(vars_list)} variables in {path}")

if __name__ == "__main__":
    main()
