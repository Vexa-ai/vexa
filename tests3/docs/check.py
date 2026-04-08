#!/usr/bin/env python3
"""Doc drift checks. Static — no infra needed, runs instantly.

Checks:
  PAGE_EXISTS          every page in registry.json has a .mdx file
  NAV_COMPLETE         every .mdx in docs.json nav, every nav entry has a file
  LINKS_RESOLVE        internal markdown links point to real pages
  OWNERSHIP_COMPLETE   every nav page is owned or explicitly unowned

Usage:
  python3 tests3/docs/check.py
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ROOT", Path(__file__).resolve().parent.parent.parent))
DOCS_DIR = ROOT / "docs"
REGISTRY = Path(__file__).resolve().parent / "registry.json"
DOCS_JSON = DOCS_DIR / "docs.json"


def red(s):   return f"\033[31m{s}\033[0m"
def green(s): return f"\033[32m{s}\033[0m"
def dim(s):   return f"\033[90m{s}\033[0m"


def load_registry():
    with open(REGISTRY) as f:
        return json.load(f)


def load_docs_json():
    with open(DOCS_JSON) as f:
        return json.load(f)


def all_mdx_files():
    """Return set of page slugs for all .mdx files under docs/."""
    slugs = set()
    for mdx in DOCS_DIR.rglob("*.mdx"):
        rel = mdx.relative_to(DOCS_DIR).with_suffix("")
        slugs.add(str(rel))
    return slugs


def nav_pages(docs_json):
    """Extract all page slugs from docs.json navigation."""
    pages = set()
    for tab in docs_json.get("navigation", {}).get("tabs", []):
        for group in tab.get("groups", []):
            for page in group.get("pages", []):
                if isinstance(page, str):
                    pages.add(page)
                elif isinstance(page, dict):
                    # nested group
                    for p in page.get("pages", []):
                        if isinstance(p, str):
                            pages.add(p)
    return pages


def extract_internal_links(mdx_path, docs_dir):
    """Extract internal links from an MDX file. Returns list of (resolved_slug, line_num)."""
    links = []
    text = mdx_path.read_text()
    parent_dir = mdx_path.relative_to(docs_dir).parent

    for i, line in enumerate(text.split("\n"), 1):
        # Markdown links: [text](slug) — skip external, anchors, mailto
        for m in re.finditer(r'\]\(([^)]+)\)', line):
            target = m.group(1).split("#")[0].strip()
            if not target:
                continue
            if target.startswith(("http://", "https://", "mailto:", "tel:")):
                continue
            resolved = _resolve_link(target, parent_dir)
            if resolved is not None:
                links.append((resolved, i))
        # MDX href: href="/slug" or href="slug"
        for m in re.finditer(r'href="([^"#]+?)(?:#[^"]*)?(?:\.mdx)?"', line):
            target = m.group(1).strip()
            if not target:
                continue
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = _resolve_link(target, parent_dir)
            if resolved is not None:
                links.append((resolved, i))
    return links


def _resolve_link(target, parent_dir):
    """Resolve a link target relative to the file's parent directory.
    Returns the normalized slug, or None if the target is empty."""
    target = target.removesuffix(".mdx")
    if target.startswith("/"):
        # Absolute from docs root
        stripped = target.lstrip("/")
        return stripped if stripped else "index"
    # Relative — resolve against parent dir
    resolved = (parent_dir / target).as_posix()
    # Normalize .. and .
    parts = []
    for p in resolved.split("/"):
        if p == "..":
            if parts:
                parts.pop()
        elif p != ".":
            parts.append(p)
    return "/".join(parts) if parts else None


# ─── Checks ─────────────────────────────────────────────────────

def check_page_exists(registry):
    """Every page in registry.json has a .mdx file in docs/."""
    errors = []
    for owner in registry["owners"]:
        for page in owner["pages"]:
            mdx_path = DOCS_DIR / f"{page}.mdx"
            if not mdx_path.is_file():
                errors.append(f"  {owner['service']} owns \"{page}\" but {page}.mdx does not exist")
    return errors


def check_nav_complete(docs_json):
    """Every .mdx file is in docs.json nav; every nav entry has a .mdx file."""
    errors = []
    mdx_slugs = all_mdx_files()
    nav_slugs = nav_pages(docs_json)

    # MDX files not in nav
    for slug in sorted(mdx_slugs - nav_slugs):
        errors.append(f"  {slug}.mdx exists but not in docs.json")

    # Nav entries without MDX files
    for slug in sorted(nav_slugs - mdx_slugs):
        errors.append(f"  docs.json references \"{slug}\" but {slug}.mdx does not exist")

    return errors


def check_links_resolve():
    """Internal markdown links point to existing pages."""
    errors = []
    mdx_slugs = all_mdx_files()

    for mdx in sorted(DOCS_DIR.rglob("*.mdx")):
        rel = str(mdx.relative_to(DOCS_DIR))
        links = extract_internal_links(mdx, DOCS_DIR)

        for target, line_num in links:
            if target in mdx_slugs:
                continue
            errors.append(f"  {rel}:{line_num} link to \"{target}\" — no matching page")

    return errors


def check_ownership_complete(registry, docs_json):
    """Every page in docs.json is either owned or explicitly unowned in registry."""
    errors = []
    nav_slugs = nav_pages(docs_json)

    owned_pages = set()
    for owner in registry["owners"]:
        owned_pages.update(owner["pages"])
    unowned = set(registry.get("unowned", []))
    covered = owned_pages | unowned

    for slug in sorted(nav_slugs - covered):
        errors.append(f"  \"{slug}\" is in docs.json but has no owner in registry.json")

    return errors


# ─── Main ────────────────────────────────────────────────────────

def main():
    registry = load_registry()
    docs_json = load_docs_json()

    checks = [
        ("PAGE_EXISTS",         lambda: check_page_exists(registry)),
        ("NAV_COMPLETE",        lambda: check_nav_complete(docs_json)),
        ("LINKS_RESOLVE",       lambda: check_links_resolve()),
        ("OWNERSHIP_COMPLETE",  lambda: check_ownership_complete(registry, docs_json)),
    ]

    print()
    print("  doc checks")
    print(f"  {'─' * 46}")

    all_errors = {}
    for name, fn in checks:
        errors = fn()
        if errors:
            all_errors[name] = errors
            print(f"  {red('FAIL')}  {name}")
        else:
            print(f"  {green(' ok ')}  {name}")

    print(f"  {'─' * 46}")

    if all_errors:
        print()
        for name, errors in all_errors.items():
            print(f"  {red(name)}")
            for e in errors:
                print(f"  {e}")
            print()
        total = len(checks)
        failed = len(all_errors)
        print(f"  {red(f'{failed} failed')} out of {total} checks")
        print()
        sys.exit(1)
    else:
        print(f"  {green(f'All {len(checks)} checks pass.')}")
        print()


if __name__ == "__main__":
    main()
