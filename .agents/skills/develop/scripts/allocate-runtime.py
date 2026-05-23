#!/usr/bin/env python3
"""Allocate deterministic isolated runtime names and non-default ports."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


FORBIDDEN_PORTS = {3000, 8056, 8080}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "pack"


def stable_slot(pack_id: str) -> int:
    digest = hashlib.sha256(pack_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 300


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--release", default="")
    parser.add_argument("--base-port", type=int, default=41000)
    parser.add_argument("--index", type=int)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    pack_id = slugify(args.pack_id)
    slot = args.index if args.index is not None else stable_slot(pack_id)
    base = args.base_port + slot * 20
    ports = {
        "compose_dashboard": base,
        "compose_gateway": base + 1,
        "compose_browser_session": base + 2,
        "compose_vnc": base + 3,
        "lite_dashboard": base + 10,
        "lite_gateway": base + 11,
        "lite_vnc": base + 12,
    }
    collisions = sorted(port for port in ports.values() if port in FORBIDDEN_PORTS)
    if collisions:
        raise SystemExit(f"allocated forbidden default ports: {collisions}")

    namespace = f"pack-{pack_id}"
    result = {
        "pack_id": pack_id,
        "release_id": args.release,
        "slot": slot,
        "namespace": namespace,
        "compose_project": f"vexa_{pack_id}_compose",
        "lite_container": f"vexa-{pack_id}-lite",
        "network_prefix": namespace,
        "ports": ports,
        "forbidden_ports": sorted(FORBIDDEN_PORTS),
        "notes": [
            "Ports are pack-scoped and intentionally avoid popular/default Vexa service ports.",
            "Meeting API and other internal services should stay internal unless a deploy skill explicitly exposes a debug lane.",
        ],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
