#!/usr/bin/env python3
"""Collect raw GitHub issues/PRs for pack scoping.

Dry, read-only helper. Writes normalized JSON that later pack scripts consume.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_gh(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{proc.stderr}")
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def repo_parts(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise SystemExit("--repo must look like OWNER/REPO")
    owner, name = repo.split("/", 1)
    return owner, name


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    labels = item.get("labels") or []
    if labels and isinstance(labels[0], dict):
        labels = [label.get("name") for label in labels if label.get("name")]
    milestone = item.get("milestone")
    if isinstance(milestone, dict):
        milestone = milestone.get("title")
    return {
        "number": item.get("number"),
        "title": item.get("title") or "",
        "state": item.get("state") or "",
        "url": item.get("html_url") or item.get("url") or "",
        "kind": "pull_request" if item.get("pull_request") else "issue",
        "labels": labels,
        "milestone": milestone,
        "body": item.get("body") or "",
    }


def issue(repo: str, number: str) -> dict[str, Any]:
    owner, name = repo_parts(repo)
    return normalize(run_gh(["api", f"repos/{owner}/{name}/issues/{number}"]))


def milestone_number(repo: str, title: str) -> int:
    owner, name = repo_parts(repo)
    data = run_gh(["api", "--method", "GET", f"repos/{owner}/{name}/milestones", "-f", "state=all", "-f", "per_page=100"])
    for milestone in data or []:
        if milestone.get("title") == title:
            return int(milestone["number"])
    raise SystemExit(f"milestone not found: {title}")


def issues_for_milestone(repo: str, title: str) -> list[dict[str, Any]]:
    owner, name = repo_parts(repo)
    number = milestone_number(repo, title)
    data = run_gh(
        [
            "api",
            "--method",
            "GET",
            f"repos/{owner}/{name}/issues",
            "-f",
            "state=all",
            "-f",
            f"milestone={number}",
            "-f",
            "per_page=100",
        ]
    )
    return [normalize(item) for item in data or []]


def search(repo: str, query: str) -> list[dict[str, Any]]:
    q = query if "repo:" in query else f"{query} repo:{repo}"
    data = run_gh(["api", "search/issues", "-f", f"q={q}", "-f", "per_page=100"])
    return [normalize(item) for item in data.get("items", [])]


def parse_issue_args(values: list[str]) -> list[str]:
    numbers: list[str] = []
    for value in values:
        for part in value.replace(",", " ").split():
            cleaned = part.strip().lstrip("#")
            if cleaned:
                numbers.append(cleaned)
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Vexa-ai/vexa")
    parser.add_argument("--issue", action="append", default=[], help="Issue/PR number. May be repeated or comma-separated.")
    parser.add_argument("--milestone")
    parser.add_argument("--search")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    seen: dict[int, dict[str, Any]] = {}
    for number in parse_issue_args(args.issue):
        item = issue(args.repo, number)
        seen[int(item["number"])] = item
    if args.milestone:
        for item in issues_for_milestone(args.repo, args.milestone):
            seen[int(item["number"])] = item
    if args.search:
        for item in search(args.repo, args.search):
            seen[int(item["number"])] = item

    payload = {
        "repo": args.repo,
        "source": {
            "issues": parse_issue_args(args.issue),
            "milestone": args.milestone,
            "search": args.search,
        },
        "items": [seen[key] for key in sorted(seen)],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(payload['items'])} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
