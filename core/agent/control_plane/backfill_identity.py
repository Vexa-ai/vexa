"""backfill_identity — what each existing workspace's image WOULD become (UI decision 15f).

    python -m control_plane.backfill_identity --dry-run [--root /workspaces] [--fetch] [--json]

Lists, per workspace on this host, the proposal the agent would make: nothing (it already has an
image), the bound company's logo (README ``domain:``; with ``--fetch`` the site is actually asked,
under the outbound URL rules, and the logo address found is printed), or "design an SVG mark" with
the topic it would be about. A shared workspace's change is a proposal its owner accepts; a private
one's would be set.

DRY-RUN ONLY, by design: this command writes nothing. The agent applies each line through
``workspace_icon_set``, so a shared workspace's owner still accepts every change in one click, and
the per-workspace act stays where every other write lives. Without ``--dry-run`` it refuses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Iterator, Optional

from workspaces.shared import workspace_paths as wpaths

from control_plane import front_page
from control_plane import workspace_icon as wi

STORE = ".attached"


def workspaces(root: Path) -> Iterator[tuple[str, Path]]:
    """``(slug, dir)`` for every workspace repo under ``root``: desks and shared workspaces at the top
    level, private secondary ones in ``.attached/<subject>/<slug>``. The platform tiers are skipped.
    Every listing goes through ``workspace_paths`` (no link is followed)."""
    root = Path(root)
    for name in wpaths.list_dirs_inside(root):
        if name.startswith((".", "_")):
            continue
        if wpaths.is_dir_inside(root, f"{name}/.git", allow=(".git",)):
            yield name, root / name
    for subj in wpaths.list_dirs_inside(root, STORE):
        for name in wpaths.list_dirs_inside(root, f"{STORE}/{subj}"):
            if wpaths.is_dir_inside(root, f"{STORE}/{subj}/{name}/.git", allow=(".git",)):
                yield f"{subj}/{name}", root / STORE / subj / name


def _shared(ws: Path) -> bool:
    try:
        rows = json.loads(wpaths.read_text_inside(ws, "policy/members.json", max_bytes=1 << 20) or "[]")
    except ValueError:
        return False
    return isinstance(rows, list) and len(rows) >= 2


def plan(slug: str, ws: Path, *, fetch: Optional[Callable] = None) -> dict:
    """The one line for one workspace."""
    readme = wpaths.read_text_inside(ws, wi.README, max_bytes=512 * 1024) or ""
    title = front_page.page_title(readme, slug) if readme else slug
    shared = _shared(ws)
    row = {"slug": slug, "title": title, "shared": shared, "act": "proposal" if shared else "set"}
    if wi.icon_path(ws):
        return {**row, "would": "nothing", "why": f"has its own image ({wi.icon_path(ws)})", "act": "none"}
    domain = wi.company_domain(ws)
    if domain:
        row.update(would="company_logo", domain=domain)
        if fetch is not None:
            found = wi.find_site_logo(domain, fetch=fetch)
            row.update(found=found[2] if found else None,
                       would="company_logo" if found else "design_svg")
        return row
    return {**row, "would": "design_svg", "topic": title}


def _line(r: dict) -> str:
    if r["would"] == "nothing":
        return f"{r['slug']}: nothing — {r['why']}"
    who = "propose to the owner" if r["shared"] else "set"
    if r["would"] == "company_logo":
        where = f" (found {r['found']})" if r.get("found") else ""
        return f"{r['slug']}: {who} the logo of {r['domain']}{where}"
    if r.get("domain"):
        return f"{r['slug']}: {who} a designed SVG mark — {r['domain']} offered no logo"
    return f"{r['slug']}: {who} a designed SVG mark for “{r['topic']}”"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m control_plane.backfill_identity", description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="list what would be proposed (required)")
    ap.add_argument("--root", default="/workspaces", help="the workspaces root (default /workspaces)")
    ap.add_argument("--fetch", action="store_true", help="ask each company site for its logo (network)")
    ap.add_argument("--json", action="store_true", help="one JSON object per line")
    a = ap.parse_args(argv)
    if not a.dry_run:
        print("backfill_identity writes nothing: run it with --dry-run, and let the agent apply each "
              "line with workspace_icon_set", file=sys.stderr)
        return 2
    fetch = None
    root = Path(a.root)
    if a.fetch:
        from control_plane import home_media  # noqa: PLC0415
        from shared import asset_source  # noqa: PLC0415
        if home_media.enabled(root / "_global"):
            fetch = asset_source.fetch_asset
        else:
            print("external_images is off for this company: nothing is fetched", file=sys.stderr)
    if not root.is_dir():
        print(f"{root} is not a directory", file=sys.stderr)
        return 2
    for slug, ws in workspaces(root):
        r = plan(slug, ws, fetch=fetch)
        print(json.dumps(r, ensure_ascii=False) if a.json else _line(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
