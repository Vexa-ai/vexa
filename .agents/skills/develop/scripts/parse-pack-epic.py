#!/usr/bin/env python3
"""Parse a pack epic issue/body into a machine-readable contract."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = [
    "CEO outcome",
    "CTO outcome",
    "User outcome",
    "Included raw issues / PRs",
    "Explicitly out of scope",
    "Blast radius",
    "Data / schema / API / public-contract decisions",
    "Isolation requirements",
    "Compose validation gate",
    "Lite validation gate",
    "Synthetic validation gate",
    "Live / human validation gate",
    "PR readiness checklist",
    "Stitching risk notes",
    "Pack metadata",
]


def key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def run_gh(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def issue_number(value: str) -> str:
    match = re.search(r"/issues/(\d+)", value)
    if match:
        return match.group(1)
    match = re.fullmatch(r"#?(\d+)", value.strip())
    if match:
        return match.group(1)
    raise SystemExit(f"cannot parse issue number from {value!r}")


def read_issue(repo: str, issue: str) -> dict[str, Any]:
    number = issue_number(issue)
    data = run_gh(
        [
            "issue",
            "view",
            number,
            "-R",
            repo,
            "--json",
            "number,title,url,body,state,labels,milestone",
        ]
    )
    return data


def label_names(source: dict[str, Any]) -> list[str]:
    labels = source.get("labels") or []
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = str(label)
        if name:
            names.append(name)
    return names


def lifecycle_status(labels: list[str]) -> str:
    statuses = lifecycle_statuses(labels)
    if len(statuses) == 1:
        return statuses[0].removeprefix("status:")
    return ""


def lifecycle_statuses(labels: list[str]) -> list[str]:
    return [label for label in labels if label.startswith("status:")]


def parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def parse_metadata(text: str) -> dict[str, str]:
    mapping = {
        "pack id": "pack_id",
        "release": "release_id",
        "base branch": "base_branch",
        "integration branch": "integration_branch",
        "runtime namespace": "runtime_namespace",
        "evidence root": "evidence_root",
    }
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^-\s*([^:]+):\s*(.+?)\s*$", line.strip())
        if not match:
            continue
        label = match.group(1).strip().lower()
        value = match.group(2).strip().strip("`")
        if label in mapping:
            metadata[mapping[label]] = value
    return metadata


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "pack"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Vexa-ai/vexa")
    parser.add_argument("--issue")
    parser.add_argument("--body")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if bool(args.issue) == bool(args.body):
        raise SystemExit("provide exactly one of --issue or --body")

    source: dict[str, Any]
    if args.issue:
        source = read_issue(args.repo, args.issue)
        body = source.get("body") or ""
    else:
        path = Path(args.body)
        body = path.read_text(encoding="utf-8")
        source = {
            "number": None,
            "title": path.stem,
            "url": str(path),
            "state": "local",
            "labels": [],
            "milestone": None,
        }

    labels = label_names(source)
    sections = parse_sections(body)
    sections_by_key = {key(name): content for name, content in sections.items()}
    required_by_key = {key(name): name for name in REQUIRED_SECTIONS}
    missing = [name for norm, name in required_by_key.items() if norm not in sections_by_key]
    metadata = parse_metadata(sections_by_key.get("pack-metadata", ""))

    pack_id = metadata.get("pack_id") or slugify(str(source.get("title") or "pack"))
    evidence_root = metadata.get("evidence_root") or f".agents/packs/{pack_id}/"

    result = {
        "source": source,
        "pack_epic": source.get("url"),
        "title": source.get("title") or pack_id,
        "pack_id": pack_id,
        "label_names": labels,
        "lifecycle_status": lifecycle_status(labels),
        "lifecycle_statuses": lifecycle_statuses(labels),
        "is_pack_epic": "pack" in labels or str(source.get("state")) == "local",
        "release_id": metadata.get("release_id", ""),
        "base_branch": metadata.get("base_branch", "main"),
        "integration_branch": metadata.get("integration_branch", ""),
        "runtime_namespace": metadata.get("runtime_namespace", pack_id),
        "evidence_root": evidence_root,
        "metadata": metadata,
        "sections": sections,
        "sections_by_key": sections_by_key,
        "required_sections": REQUIRED_SECTIONS,
        "missing_sections": missing,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    if missing:
        print(f"missing sections: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
