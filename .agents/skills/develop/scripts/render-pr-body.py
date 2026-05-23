#!/usr/bin/env python3
"""Render a PR body for a pack from parsed epic and evidence status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR.parent / "references" / "pack-pr-template.md"


def section(pack: dict[str, Any], key: str, default: str = "TBD") -> str:
    return pack.get("sections_by_key", {}).get(key) or default


def evidence_text(evidence: dict[str, Any] | None) -> tuple[str, str, str, str, str, str]:
    if not evidence:
        return ("TBD", "TBD", "TBD", "TBD", "TBD", "- Evidence check not run yet.")
    checks = evidence.get("checks") or []
    checklist = "\n".join(
        f"- [{'x' if item.get('exists') else ' '}] `{item.get('path')}`"
        for item in checks
    )
    status = evidence.get("status", "unknown")
    return (status, status, status, "TBD", status, checklist or "- No evidence checks recorded.")


def render(template: str, pack: dict[str, Any], evidence: dict[str, Any] | None) -> str:
    synthetic, compose, lite, live, hardenloop, checklist = evidence_text(evidence)
    values = {
        "PACK_TITLE": pack.get("title") or pack.get("pack_id") or "Pack",
        "PACK_EPIC": pack.get("pack_epic") or "TBD",
        "PACK_ID": pack.get("pack_id") or "TBD",
        "RELEASE_ID": pack.get("release_id") or "TBD",
        "BASE_BRANCH": pack.get("base_branch") or "TBD",
        "INTEGRATION_BRANCH": pack.get("integration_branch") or "TBD",
        "EVIDENCE_ROOT": pack.get("evidence_root") or f".agents/packs/{pack.get('pack_id','TBD')}/",
        "CEO_OUTCOME": section(pack, "ceo-outcome"),
        "CTO_OUTCOME": section(pack, "cto-outcome"),
        "USER_OUTCOME": section(pack, "user-outcome"),
        "INCLUDED_ITEMS": section(pack, "included-raw-issues-prs"),
        "OUT_OF_SCOPE": section(pack, "explicitly-out-of-scope"),
        "BLAST_RADIUS": section(pack, "blast-radius"),
        "SYNTHETIC_STATUS": synthetic,
        "COMPOSE_STATUS": compose,
        "LITE_STATUS": lite,
        "LIVE_HUMAN_STATUS": live,
        "HARDENLOOP_STATUS": hardenloop,
        "EVIDENCE_CHECKLIST": checklist,
        "STITCHING_RISKS": section(pack, "stitching-risk-notes"),
        "PR_CHECKLIST": section(pack, "pr-readiness-checklist"),
    }
    text = template
    for token, value in values.items():
        text = text.replace("{{" + token + "}}", str(value))
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-json", required=True)
    parser.add_argument("--evidence-check")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    pack = json.loads(Path(args.pack_json).read_text(encoding="utf-8"))
    evidence = None
    if args.evidence_check:
        evidence = json.loads(Path(args.evidence_check).read_text(encoding="utf-8"))
    body = render(Path(args.template).read_text(encoding="utf-8"), pack, evidence)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
