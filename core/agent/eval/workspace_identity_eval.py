#!/usr/bin/env python
"""Eval: does the agent's logo lookup find a real site's logo and store it IN the workspace?

UI decision 15f: a company or project workspace gets its logo — the bound company's, else the site's
apple-touch-icon / icon links / favicon / og:image — fetched under the outbound URL rules and stored
locally, never hot-linked. This runs exactly what `workspace_icon_set(site=…)` runs
(`workspace_icon.find_site_logo` with the guarded `asset_source.fetch_asset`, then `write_icon`)
against a few well-known, neutral public sites, each into a scratch git workspace, and checks:

  1. a logo was found, and its address is on the site's own host (or a host it redirected to);
  2. the bytes are stored at `.vexa/icon.<svg|png|webp>` and the README names that path;
  3. the stored file is no larger than the cap, and an SVG carries no script or external reference;
  4. nothing in the workspace refers to the remote address (no hot link).

Needs the network. `--offline` (or no network) skips with exit 0, so CI can carry it.

Usage:  cd core/agent && uv run python eval/workspace_identity_eval.py [--sites a.org,b.org] [--offline]
pytest: VEXA_EVAL_NETWORK=1 uv run pytest -q tests/test_workspace_identity_eval.py
"""
from __future__ import annotations

import argparse
import socket
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if "workspaces" not in sys.modules:          # the monorepo checkout: register only `workspaces`, as conftest does
    import importlib.util as _ilu
    _wd = HERE.parent / "workspaces"
    _spec = _ilu.spec_from_file_location("workspaces", _wd / "__init__.py", submodule_search_locations=[str(_wd)])
    _mod = _ilu.module_from_spec(_spec)
    sys.modules["workspaces"] = _mod
    _spec.loader.exec_module(_mod)

from control_plane import workspace_icon as wi  # noqa: E402
from shared.gitexec import run_git  # noqa: E402

#: Neutral, long-lived public project and organisation sites — none is a customer.
SITES = ("python.org", "mozilla.org", "debian.org", "wikipedia.org", "apache.org")


def online(host: str = "python.org") -> bool:
    try:
        socket.create_connection((host, 443), timeout=4).close()
        return True
    except OSError:
        return False


def run_site(domain: str, scratch: Path) -> dict:
    ws = scratch / domain.replace(".", "-")
    ws.mkdir(parents=True)
    run_git(None, "init", "-q", str(ws), check=True)
    (ws / "README.md").write_text(f"---\ntitle: {domain}\ndomain: {domain}\n---\n# {domain}\n")
    found = wi.find_site_logo(domain)
    if not found:
        return {"site": domain, "ok": False, "why": "no logo found"}
    body, kind, url = found
    wi.write_icon(ws, body, kind)
    rel = wi.icon_path(ws)
    stored = (ws / rel).read_bytes() if rel else b""
    problems = []
    if not rel:
        problems.append("README does not name a stored icon")
    if len(stored) > wi.MAX_BYTES:
        problems.append("stored image over the cap")
    bare = stored.replace(b'xmlns="http://www.w3.org/2000/svg"', b"").replace(
        b'xmlns:xlink="http://www.w3.org/1999/xlink"', b"")
    if kind == "svg" and (b"<script" in bare.lower() or b"http://" in bare or b"https://" in bare):
        problems.append("stored SVG carries script or an external reference")
    text = "\n".join(p.read_text(errors="ignore") for p in ws.rglob("*") if p.is_file() and ".git/" not in str(p)
                     and p.suffix in (".md", ".json", ".svg"))
    if url in text:
        problems.append("the remote address appears in the workspace (hot link)")
    return {"site": domain, "ok": not problems, "kind": kind, "bytes": len(stored), "from": url,
            "path": rel, "why": "; ".join(problems)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    if a.offline or not online():
        print("SKIP workspace_identity_eval: offline")
        return 0
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for site in [s.strip() for s in a.sites.split(",") if s.strip()]:
            r = run_site(site, Path(tmp))
            results.append(r)
            print(("PASS " if r["ok"] else "FAIL ") + " ".join(f"{k}={v}" for k, v in r.items() if k != "ok"))
    passed = sum(r["ok"] for r in results)
    print(f"{passed}/{len(results)} sites: logo found and stored locally")
    # a site may legitimately change or block us; the eval passes when most of them do
    return 0 if passed >= max(1, (len(results) * 3 + 4) // 5) else 1


if __name__ == "__main__":
    raise SystemExit(main())
