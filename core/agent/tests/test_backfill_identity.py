"""`python -m control_plane.backfill_identity --dry-run` lists, per workspace, the image proposal the
agent would make, and writes nothing (UI decision 15f)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane import backfill_identity as bf
from control_plane import workspace_icon as wi

MARK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24"/></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _ws(root: Path, rel: str, readme: str, members: int = 0) -> Path:
    ws = root / rel
    ws.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "README.md").write_text(readme)
    if members:
        (ws / "policy").mkdir()
        (ws / "policy/members.json").write_text(json.dumps([{"subject": f"u{i}", "role": "owner" if not i else "contributor"}
                                                            for i in range(members)]))
    return ws


def _snapshot(root: Path) -> dict:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git/" not in str(p)}


def test_dry_run_lists_one_line_per_workspace_and_writes_nothing(tmp_path, capsys):
    _ws(tmp_path, "u_jane", "# Jane's desk\n")
    _ws(tmp_path, "studio-c1", "---\ntitle: Studio\ndomain: studio.example.com\n---\n", members=2)
    done = _ws(tmp_path, "garden", "# Garden\n")
    wi.write_icon(done, MARK, "svg")
    _ws(tmp_path, ".attached/u_jane/reading", "---\ntitle: Reading list\n---\n")
    _ws(tmp_path, "_global", "# org\n")
    before = _snapshot(tmp_path)
    assert bf.main(["--dry-run", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert _snapshot(tmp_path) == before
    joined = "\n".join(out)
    assert "studio-c1: propose to the owner the logo of studio.example.com" in joined
    assert "u_jane: set a designed SVG mark for “Jane's desk”" in joined
    assert "garden: nothing" in joined
    assert "u_jane/reading: set a designed SVG mark for “Reading list”" in joined
    assert "_global" not in joined and len(out) == 4


def test_with_fetch_it_reports_the_logo_found(tmp_path):
    ws = _ws(tmp_path, "acme", "---\ndomain: acme.example.com\n---\n")
    r = bf.plan("acme", ws, fetch=lambda url: (PNG, "image/png", url))
    assert r["would"] == "company_logo" and r["found"].endswith("/apple-touch-icon.png")
    r = bf.plan("acme", ws, fetch=lambda url: (_ for _ in ()).throw(RuntimeError("down")))
    assert r["would"] == "design_svg"


def test_without_dry_run_it_refuses(tmp_path):
    assert bf.main(["--root", str(tmp_path)]) == 2
