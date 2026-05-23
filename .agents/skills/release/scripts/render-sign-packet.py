#!/usr/bin/env python3
"""Render a release sign packet from pack/stage evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR.parent / "references" / "release-sign-template.md"


def pack_summary(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for pack in data.get("packs", []):
        refs = ", ".join(pack.get("pr_refs") or ["missing PR"])
        status = "accepted" if pack.get("accepted") else "not accepted"
        lines.append(f"- `{pack.get('pack_id')}` - {status}; PR: {refs}; evidence: `{pack.get('evidence_root')}`")
    return "\n".join(lines) or "- No packs recorded."


def evidence_summary(stage: dict[str, Any] | None) -> str:
    if not stage:
        return "- Stage evidence not attached."
    lines = []
    for key, value in sorted(stage.items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def render(template: str, values: dict[str, str]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--pack-prs", required=True)
    parser.add_argument("--stage-evidence")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    pack_data = json.loads(Path(args.pack_prs).read_text(encoding="utf-8"))
    stage = json.loads(Path(args.stage_evidence).read_text(encoding="utf-8")) if args.stage_evidence else None
    values = {
        "RELEASE_ID": args.release,
        "INTEGRATION_BRANCH": pack_data.get("integration_branch", "TBD"),
        "BASE_BRANCH": pack_data.get("base_branch", "TBD"),
        "GENERATED_AT": dt.datetime.now(dt.timezone.utc).isoformat(),
        "PACK_SUMMARY": pack_summary(pack_data),
        "LOCAL_COMPOSE_STATUS": "TBD",
        "LOCAL_LITE_STATUS": "TBD",
        "STAGE_COMPOSE_STATUS": "TBD",
        "STAGE_LITE_STATUS": "TBD",
        "STAGE_HELM_STATUS": "TBD",
        "LIVE_MEETING_STATUS": "TBD",
        "HARDENLOOP_STATUS": "TBD",
        "EVIDENCE_SUMMARY": evidence_summary(stage),
        "OPEN_BLOCKERS": "TBD",
        "RELEASE_DECISION": "Not signed until all hard gates pass.",
    }
    body = render(Path(args.template).read_text(encoding="utf-8"), values)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
