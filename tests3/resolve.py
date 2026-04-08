#!/usr/bin/env python3
"""Resolve changed files to tests3 make targets.

Usage:
    git diff --name-only main | python3 tests3/resolve.py
    echo "services/meeting-api/foo.py" | python3 tests3/resolve.py
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files under these prefixes map to the named service.
# Scanned dynamically from services/ directory.
SERVICE_DIRS = {
    d.name for d in (REPO_ROOT / "services").iterdir() if d.is_dir()
}

# Infrastructure paths — changes here affect everything.
INFRA_PREFIXES = [
    "deploy/",
    "docker-compose",
    ".env",
    "Makefile",
]

# tests3 changes — run the modified target directly.
TESTS3_PREFIX = "tests3/"

# Doc-adjacent paths — trigger doc drift checks.
DOC_PREFIXES = [
    "docs/",
    "tests3/docs/",
]


def parse_frontmatter(readme_path):
    """Extract YAML frontmatter from a feature README.

    Returns dict with 'services' list and 'tests3' dict, or None.
    Parses the simple YAML subset we use (no full YAML library needed).
    """
    try:
        text = readme_path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    end = text.find("\n---", 3)
    if end == -1:
        return None

    block = text[4:end]
    result = {"services": [], "tests3": {"targets": [], "checks": []}}

    # Parse services: [a, b, c]
    m = re.search(r"^services:\s*\[([^\]]*)\]", block, re.MULTILINE)
    if m:
        result["services"] = [s.strip() for s in m.group(1).split(",") if s.strip()]

    # Parse tests3.targets: [a, b]
    m = re.search(r"^\s+targets:\s*\[([^\]]*)\]", block, re.MULTILINE)
    if m:
        result["tests3"]["targets"] = [s.strip() for s in m.group(1).split(",") if s.strip()]

    # Parse tests3.checks: [A, B]
    m = re.search(r"^\s+checks:\s*\[([^\]]*)\]", block, re.MULTILINE)
    if m:
        result["tests3"]["checks"] = [s.strip() for s in m.group(1).split(",") if s.strip()]

    if not result["services"]:
        return None

    return result


def file_to_services(path):
    """Map a changed file path to a set of service names."""
    services = set()

    # Direct service match: services/meeting-api/** → meeting-api
    if path.startswith("services/"):
        parts = path.split("/")
        if len(parts) >= 2 and parts[1] in SERVICE_DIRS:
            services.add(parts[1])

    return services


def resolve(changed_files):
    """Given a list of changed file paths, return (targets, reasons)."""
    # Collect affected services
    affected_services = set()
    infra_change = False
    tests3_change = False

    docs_change = False

    for f in changed_files:
        f = f.strip()
        if not f:
            continue

        # Infrastructure change → run everything
        if any(f.startswith(p) for p in INFRA_PREFIXES):
            infra_change = True

        # tests3 change → flag it
        if f.startswith(TESTS3_PREFIX):
            tests3_change = True

        # Doc-adjacent change → run doc drift checks
        if any(f.startswith(p) for p in DOC_PREFIXES):
            docs_change = True

        # Service README change → run doc drift checks
        if "/README.md" in f and (f.startswith("services/") or f.startswith("deploy/") or f.startswith("libs/")):
            docs_change = True

        # Map to services
        affected_services.update(file_to_services(f))

    # If infrastructure changed, run smoke at minimum
    targets = set()
    reasons = {}

    if infra_change:
        targets.add("smoke")
        reasons["smoke"] = "infrastructure files changed"

    if tests3_change:
        targets.add("smoke")
        reasons["smoke"] = "tests3 files changed"

    if docs_change:
        targets.add("docs")
        reasons["docs"] = "doc-adjacent files changed"

    # Walk feature READMEs, find features whose services overlap
    features_dir = REPO_ROOT / "features"
    for readme in sorted(features_dir.rglob("README.md")):
        fm = parse_frontmatter(readme)
        if not fm:
            continue

        overlap = affected_services & set(fm["services"])
        if not overlap:
            continue

        feature_name = str(readme.parent.relative_to(features_dir))
        for t in fm["tests3"]["targets"]:
            targets.add(t)
            reasons[t] = f"feature:{feature_name} (via {', '.join(sorted(overlap))})"

    # Fallback: if services changed but no features matched (missing frontmatter),
    # still run smoke
    if affected_services and not targets:
        targets.add("smoke")
        reasons["smoke"] = f"services changed ({', '.join(sorted(affected_services))}) but no feature frontmatter found"

    return targets, reasons


def main():
    changed_files = sys.stdin.read().strip().split("\n")
    changed_files = [f for f in changed_files if f.strip()]

    if not changed_files:
        print("# no changed files", file=sys.stderr)
        sys.exit(0)

    targets, reasons = resolve(changed_files)

    if not targets:
        print("# no tests3 targets affected", file=sys.stderr)
        sys.exit(0)

    # Print reasons to stderr
    for target, reason in sorted(reasons.items()):
        print(f"  {target} ← {reason}", file=sys.stderr)

    # Print targets to stdout (for make)
    print(" ".join(sorted(targets)))


if __name__ == "__main__":
    main()
