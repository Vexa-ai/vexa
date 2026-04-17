#!/usr/bin/env python3
"""
Append an issue to a release scope.yaml with schema enforcement.

Human-sourced issues (source=human) REQUIRE gap_analysis + new_checks —
this is the contract that says "every human-found bug becomes a regression
test before the release ships". The aggregator gate re-checks these fields.

Usage:
  release-issue-add.py --scope tests3/releases/<id>/scope.yaml \
      --id bug-slug \
      --source human \
      --problem "User reported X" \
      --gap "No test exercised Y" \
      --new-checks CHECK_ID_1,test:webhooks.step.name \
      [--hypothesis ...] [--modes compose,helm] [--human-verify-mode compose] \
      [--human-verify-do "..."] [--human-verify-expect "..."]
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from typing import List

try:
    import yaml
except ImportError:
    print("ERROR: needs PyYAML", file=sys.stderr)
    sys.exit(2)


def parse_list(s: str) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", required=True)
    ap.add_argument("--id", required=True, help="issue slug (kebab-case)")
    ap.add_argument("--source", required=True,
                    choices=["gh-issue", "human", "internal", "regression"])
    ap.add_argument("--ref", default="", help="GH issue URL or human report ref")
    ap.add_argument("--problem", required=True)
    ap.add_argument("--hypothesis", default="")
    ap.add_argument("--gap", dest="gap_analysis", default="",
                    help="Why the automated matrix missed this (REQUIRED when --source=human)")
    ap.add_argument("--new-checks", default="",
                    help="Comma-separated check IDs / test step names that must "
                         "exist+pass (REQUIRED when --source=human)")
    ap.add_argument("--modes", default="",
                    help="Comma-separated required_modes (defaults to scope.deployments.modes)")
    ap.add_argument("--human-verify-mode", default="")
    ap.add_argument("--human-verify-do", default="")
    ap.add_argument("--human-verify-expect", default="")
    args = ap.parse_args()

    # Schema enforcement — THE protocol rule:
    if args.source == "human":
        if not args.gap_analysis.strip():
            print("ERROR: --gap is REQUIRED for --source=human", file=sys.stderr)
            print("  Answer: why didn't the automated matrix catch this?", file=sys.stderr)
            return 2
        checks = parse_list(args.new_checks)
        if not checks:
            print("ERROR: --new-checks is REQUIRED for --source=human", file=sys.stderr)
            print("  List the check ID(s) / test step(s) that will catch this next time.", file=sys.stderr)
            return 2

    with open(args.scope) as f:
        scope = yaml.safe_load(f) or {}

    issues = scope.setdefault("issues", [])
    if any(i.get("id") == args.id for i in issues):
        print(f"ERROR: issue id '{args.id}' already exists in {args.scope}", file=sys.stderr)
        return 2

    default_modes = list((scope.get("deployments") or {}).get("modes") or [])
    modes = parse_list(args.modes) or default_modes

    # Build the proves: list from new_checks so the aggregator sees the binding.
    # Heuristic: `SOMETHING_UPPER` → check; `test:step` → test step.
    proves: List[dict] = []
    for c in parse_list(args.new_checks):
        if ":" in c:
            test, step = c.split(":", 1)
            proves.append({"test": test.strip(), "step": step.strip(), "modes": modes})
        else:
            proves.append({"check": c.strip(), "modes": modes})

    issue: dict = {
        "id": args.id,
        "source": args.source,
    }
    if args.ref:
        issue["ref"] = args.ref
    issue["problem"] = args.problem.strip() + "\n"
    if args.hypothesis.strip():
        issue["hypothesis"] = args.hypothesis.strip() + "\n"
    if args.source == "human":
        issue["gap_analysis"] = args.gap_analysis.strip() + "\n"
        issue["new_checks"] = parse_list(args.new_checks)
    issue["fix_commits"] = []
    issue["proves"] = proves
    issue["required_modes"] = modes
    issue["human_verify"] = []
    if args.human_verify_mode and args.human_verify_do and args.human_verify_expect:
        issue["human_verify"].append({
            "mode": args.human_verify_mode,
            "do": args.human_verify_do,
            "expect": args.human_verify_expect,
        })
    issue["added"] = datetime.date.today().isoformat()

    issues.append(issue)

    # Re-dump preserving key ordering close to original
    with open(args.scope, "w") as f:
        yaml.safe_dump(scope, f, default_flow_style=False, sort_keys=False, width=100)

    print(f"  appended issue '{args.id}' to {args.scope}", file=sys.stderr)
    if args.source == "human":
        print(f"  gap_analysis recorded; new_checks = {parse_list(args.new_checks)}", file=sys.stderr)
        print(f"  next: implement those checks, then `make release-iterate SCOPE={args.scope}`",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
