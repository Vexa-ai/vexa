#!/usr/bin/env python3
"""Check whether a pack evidence directory is PR-ready."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def gate_required(pack: dict[str, Any], section_key: str) -> bool:
    text = (pack.get("sections_by_key", {}).get(section_key) or "").lower()
    if not text:
        return True
    return not any(marker in text for marker in ["not required", "not in scope", "n/a"])


def check_path(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    return {"path": rel, "exists": path.exists(), "kind": "dir" if path.is_dir() else "file"}


def check_pass_file(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    item = check_path(root, rel)
    item["pass_status"] = False
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        item["pass_status"] = "status: pass" in text or "status: passed" in text
    return item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-json", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=["draft", "pr-ready"], default="draft")
    args = parser.parse_args()

    pack = json.loads(Path(args.pack_json).read_text(encoding="utf-8"))
    root = Path(args.evidence_root)

    required = ["pack.json", "runtime.json", "ops/ops.jsonl", "tests", "compose", "lite"]
    if args.mode == "pr-ready":
        required.extend(
            [
                "compose/meeting-deployment-test.md",
                "compose/human-eyeball.md",
                "lite/meeting-deployment-test.md",
                "lite/human-eyeball.md",
                "human/overall-functionality.md",
                "hardenloop",
                "review.md",
                "pr.md",
            ]
        )

    pass_files = {
        "compose/meeting-deployment-test.md",
        "compose/human-eyeball.md",
        "lite/meeting-deployment-test.md",
        "lite/human-eyeball.md",
        "human/overall-functionality.md",
    }
    checks = [
        check_pass_file(root, rel) if rel in pass_files else check_path(root, rel)
        for rel in required
    ]
    missing = [item["path"] for item in checks if not item["exists"]]
    invalid = [
        item["path"]
        for item in checks
        if item["path"] in pass_files and item["exists"] and not item.get("pass_status")
    ]
    result = {
        "status": "pass" if not missing and not invalid else "fail",
        "pack_id": pack.get("pack_id"),
        "evidence_root": str(root),
        "mode": args.mode,
        "required": required,
        "checks": checks,
        "missing": missing,
        "invalid": invalid,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({result['status']})")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
