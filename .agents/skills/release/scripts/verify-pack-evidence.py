#!/usr/bin/env python3
"""Verify pack PR/evidence readiness before release stitching."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


MINIMUM_EVIDENCE = [
    "pack.json",
    "runtime.json",
    "ops/ops.jsonl",
    "tests",
    "compose",
    "lite",
    "hardenloop",
    "review.md",
    "pr.md",
]


def check_pack(repo_root: Path, pack: dict[str, Any]) -> dict[str, Any]:
    pack_id = pack.get("pack_id") or "unknown"
    root = repo_root / (pack.get("evidence_root") or f".agents/packs/{pack_id}/")
    blockers: list[str] = []
    if not pack.get("accepted"):
        blockers.append("pack epic is not accepted")
    if not pack.get("pr_refs"):
        blockers.append("pack has no PR reference")
    if pack.get("review_blockers"):
        blockers.append("pack has unresolved review blockers")
    missing = [rel for rel in MINIMUM_EVIDENCE if not (root / rel).exists()]
    blockers.extend(f"missing evidence: {rel}" for rel in missing)
    evidence_check = root / "evidence-check.json"
    if evidence_check.exists():
        try:
            data = json.loads(evidence_check.read_text(encoding="utf-8"))
            if data.get("status") != "pass":
                blockers.append("evidence-check.json is not pass")
        except json.JSONDecodeError:
            blockers.append("evidence-check.json is invalid JSON")
    else:
        blockers.append("missing evidence-check.json")
    return {
        "pack_id": pack_id,
        "evidence_root": str(root),
        "accepted": bool(pack.get("accepted")),
        "pr_refs": pack.get("pr_refs") or [],
        "status": "pass" if not blockers else "fail",
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-prs", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.pack_prs).read_text(encoding="utf-8"))
    repo_root = Path(args.repo_root).resolve()
    checks = [check_pack(repo_root, pack) for pack in data.get("packs", [])]
    blockers = [blocker for check in checks for blocker in check["blockers"]]
    if not checks:
        blockers.append("no pack PRs found")
    result = {
        "release_id": data.get("release_id"),
        "status": "pass" if not blockers else "fail",
        "checks": checks,
        "blockers": blockers,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({result['status']})")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
