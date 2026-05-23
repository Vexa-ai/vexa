#!/usr/bin/env python3
"""Read accepted pack epics and PR references for a release stitch."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: list[str]) -> Any:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{proc.stderr}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def repo_parts(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise SystemExit("--repo must look like OWNER/REPO")
    return tuple(repo.split("/", 1))  # type: ignore[return-value]


def key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


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
    return {key(name): "\n".join(lines).strip() for name, lines in sections.items()}


def parse_metadata(sections: dict[str, str]) -> dict[str, str]:
    text = sections.get("pack-metadata", "")
    fields = {
        "pack id": "pack_id",
        "release": "release_id",
        "base branch": "base_branch",
        "integration branch": "integration_branch",
        "runtime namespace": "runtime_namespace",
        "evidence root": "evidence_root",
    }
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^-\s*([^:]+):\s*(.+?)\s*$", line.strip())
        if not match:
            continue
        label = match.group(1).strip().lower()
        value = match.group(2).strip().strip("`")
        if label in fields:
            result[fields[label]] = value
    return result


def parse_pr_refs(body: str) -> list[str]:
    refs = set()
    for match in re.finditer(r"(?:PR|Pull request|pack PR)\s*:?\s*#(\d+)", body, re.I):
        refs.add(f"#{match.group(1)}")
    for match in re.finditer(r"/pull/(\d+)", body):
        refs.add(f"#{match.group(1)}")
    return sorted(refs, key=lambda item: int(item.lstrip("#")))


def accepted(labels: list[Any], body: str) -> bool:
    label_names = [label.get("name", label) if isinstance(label, dict) else label for label in labels]
    lowered = {str(name).lower() for name in label_names}
    if "pack:accepted" in lowered or "accepted-pack" in lowered:
        return True
    return bool(re.search(r"status\s*:\s*accepted", body, re.I))


def pack_from_issue(repo: str, issue: str) -> dict[str, Any]:
    number = re.search(r"(\d+)$", issue)
    if not number:
        number = re.search(r"/issues/(\d+)", issue)
    if not number:
        raise SystemExit(f"cannot parse issue number from {issue!r}")
    data = run(
        [
            "gh",
            "issue",
            "view",
            number.group(1),
            "-R",
            repo,
            "--json",
            "number,title,url,body,state,labels,milestone",
        ]
    )
    body = data.get("body") or ""
    sections = parse_sections(body)
    metadata = parse_metadata(sections)
    pack_id = metadata.get("pack_id") or re.sub(r"[^a-z0-9]+", "-", data["title"].lower()).strip("-")
    return {
        "pack_id": pack_id,
        "title": data.get("title"),
        "issue_number": data.get("number"),
        "issue_url": data.get("url"),
        "state": data.get("state"),
        "accepted": accepted(data.get("labels") or [], body),
        "pr_refs": parse_pr_refs(body),
        "metadata": metadata,
        "evidence_root": metadata.get("evidence_root") or f".agents/packs/{pack_id}/",
        "sections": sections,
    }


def pack_from_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pack_id = data.get("pack_id") or data.get("metadata", {}).get("pack_id") or path.stem
    return {
        "pack_id": pack_id,
        "title": data.get("title") or pack_id,
        "issue_number": data.get("source", {}).get("number"),
        "issue_url": data.get("pack_epic"),
        "state": data.get("source", {}).get("state", "local"),
        "accepted": data.get("accepted", False),
        "pr_refs": data.get("pr_refs", []),
        "metadata": data.get("metadata", {}),
        "evidence_root": data.get("evidence_root") or f".agents/packs/{pack_id}/",
        "sections": data.get("sections_by_key", {}),
    }


def milestone_issue_numbers(repo: str, milestone_title: str) -> list[str]:
    owner, name = repo_parts(repo)
    milestones = run(["gh", "api", "--method", "GET", f"repos/{owner}/{name}/milestones", "-f", "state=all", "-f", "per_page=100"])
    milestone = next((item for item in milestones or [] if item.get("title") == milestone_title), None)
    if not milestone:
        raise SystemExit(f"milestone not found: {milestone_title}")
    issues = run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{owner}/{name}/issues",
            "-f",
            "state=all",
            "-f",
            f"milestone={milestone['number']}",
            "-f",
            "per_page=100",
        ]
    )
    return [str(item["number"]) for item in issues or [] if str(item.get("title", "")).startswith("[Pack]")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Vexa-ai/vexa")
    parser.add_argument("--release", required=True)
    parser.add_argument("--milestone")
    parser.add_argument("--pack-issue", action="append", default=[])
    parser.add_argument("--pack-file", action="append", default=[])
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    issue_refs = list(args.pack_issue)
    if args.milestone:
        issue_refs.extend(milestone_issue_numbers(args.repo, args.milestone))

    packs = [pack_from_issue(args.repo, issue) for issue in issue_refs]
    packs.extend(pack_from_file(Path(path)) for path in args.pack_file)

    result = {
        "release_id": args.release,
        "repo": args.repo,
        "milestone": args.milestone,
        "base_branch": args.base_branch,
        "integration_branch": args.integration_branch,
        "packs": packs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(packs)} packs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
