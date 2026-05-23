#!/usr/bin/env python3
"""Render a pack epic issue body from a pack proposal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR.parent / "references" / "pack-epic-template.md"


KEYS = {
    "PACK_TITLE": "title",
    "CEO_OUTCOME": "ceo_outcome",
    "CTO_OUTCOME": "cto_outcome",
    "USER_OUTCOME": "user_outcome",
    "INCLUDED_ITEMS": "included_items_markdown",
    "OUT_OF_SCOPE": "out_of_scope",
    "BLAST_RADIUS": "blast_radius",
    "CONTRACT_DECISIONS": "contract_decisions",
    "ISOLATION_REQUIREMENTS": "isolation_requirements",
    "COMPOSE_GATE": "compose_gate",
    "LITE_GATE": "lite_gate",
    "SYNTHETIC_GATE": "synthetic_gate",
    "LIVE_HUMAN_GATE": "live_human_gate",
    "PR_CHECKLIST": "pr_checklist",
    "STITCHING_RISKS": "stitching_risks",
    "PACK_ID": "pack_id",
    "RELEASE_ID": "release_id",
    "BASE_BRANCH": "base_branch",
    "INTEGRATION_BRANCH": "integration_branch",
    "RUNTIME_NAMESPACE": "runtime_namespace",
}


def select_pack(data: dict[str, Any], pack_id: str | None) -> dict[str, Any]:
    packs = data.get("packs") if "packs" in data else [data]
    if not packs:
        raise SystemExit("no packs in proposal file")
    if pack_id is None:
        return packs[0]
    for pack in packs:
        if pack.get("pack_id") == pack_id or pack.get("slug") == pack_id:
            return pack
    raise SystemExit(f"pack not found: {pack_id}")


def render(template: str, pack: dict[str, Any]) -> str:
    text = template
    for token, key in KEYS.items():
        text = text.replace("{{" + token + "}}", str(pack.get(key) or "TBD"))
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--pack-id")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    pack = select_pack(data, args.pack_id)
    body = render(Path(args.template).read_text(encoding="utf-8"), pack)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
