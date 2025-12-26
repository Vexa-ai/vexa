#!/usr/bin/env python3
"""
Update services/transcription-service/.env with DEVICE and COMPUTE_TYPE.

This replaces the previous huge `python -c` one-liners that were prone to
IndentationError in Makefile execution.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _update_kv_lines(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    """
    Replace KEY=... if present; otherwise insert after a matching comment header if found;
    otherwise append at end.
    """
    key_re = re.compile(rf"^{re.escape(key)}=")
    updated: list[str] = []
    found = False

    for line in lines:
        if key_re.match(line):
            updated.append(f"{key}={value}\n")
            found = True
        else:
            updated.append(line)

    if found:
        return updated, True

    # Prefer inserting after section comments if present.
    insert_after_comments = {
        "DEVICE": "# Device configuration",
        "COMPUTE_TYPE": "# Compute type (optimization)",
    }
    marker = insert_after_comments.get(key)
    if marker:
        for idx, line in enumerate(updated):
            if marker in line:
                updated.insert(idx + 1, f"{key}={value}\n")
                return updated, True

    updated.append(f"{key}={value}\n")
    return updated, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to the transcription-service .env file")
    parser.add_argument("--device", required=True, choices=["cpu", "cuda"])
    parser.add_argument("--compute-type", required=True, dest="compute_type")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Env file does not exist: {path}")

    lines = path.read_text().splitlines(keepends=True)
    lines, _ = _update_kv_lines(lines, "DEVICE", args.device)
    lines, _ = _update_kv_lines(lines, "COMPUTE_TYPE", args.compute_type)
    path.write_text("".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())










