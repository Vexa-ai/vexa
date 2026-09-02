#!/usr/bin/env python
"""Offline replay: does the rough-edges loop actually produce something a fixing agent can work off?

PRD decision 33's proof obligation — *"dump it in a way that we can just dump that to an agent that
would just fix that (like you are here)"*. This is that end to end with the cost taken out: no
stack, no docker, no redis, no model. A real FastAPI agent-api over the store's in-memory fallback,
the real worker-side detectors, the real record module, and the real routes — under a second, and it
runs in CI (`tests/test_friction_replay.py` imports and asserts it).

WHAT IT PROVES, in the order it does it:

  1. **Three edges arrive by three different doors and land in one shape.** A missing toolbelt filed
     by the HARNESS at spawn (the model never ran — ledger F70), a no-page filed by a PERSON from
     the terminal, and a wrong-workspace filed by an AGENT through the rig's own legacy argument
     names. Nothing here reformats them by hand; each posts what its real caller posts.
  2. **The dump is fixer-ready.** Symptom, exact context, likely cause, log pointers you can paste,
     and a repro line — grouped, counted, and carrying the call that closes each one.
  3. **The loop closes, and a fix that does not hold says so.** One finding is fixed against a
     reference; then the same edge is filed again and comes back `recurring` with the fix it
     outlived still named.

Usage:  python core/agent/eval/friction_loop_replay.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # core/agent — the path pytest uses


def _client(root: Path):
    """agent-api over fakes — the same L2 shape the unit tests use, no redis and no runtime."""
    from fastapi.testclient import TestClient

    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings

    class _Runtime:
        def spawn(self, workload_id, profile, env):
            return workload_id

        def await_done(self, workload_id, timeout_sec=0.0):
            return "completed"

    class _Identity:
        def mint(self, subject, launcher, workspaces, tools):
            return "tok"

    (root / "_global" / "asks").mkdir(parents=True, exist_ok=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             redis_url="")
    return TestClient(create_app(Dispatcher(settings, _Runtime(), _Identity()),
                                 reader=WorkspaceReader(str(root))))


HDR = {"X-User-Id": "126"}


def run(out=sys.stdout) -> dict:
    from worker import friction as wfr

    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td) / "workspaces")
        filed: list[dict] = []

        # ── EDGE 1 · missing-tool, filed by the HARNESS at spawn ────────────────────────────────
        # The model never ran. This is ledger F70: a session with no toolbelt cannot call
        # report_friction, so if the harness does not file it, nothing ever does.
        rec = wfr.spawn_gap(url="https://rig.example/mcp", token="", config_written=False,
                            session="meet-104", subject="126")
        assert rec is not None, "the spawn gap must file when a toolbelt was intended and absent"
        filed.append(c.post("/api/friction", json=rec, headers=HDR).json())

        # ── EDGE 2 · no-page, filed by a PERSON from the terminal ───────────────────────────────
        # Exactly the body `clients/terminal/src/surfaces/frictionApi.ts` builds: one line, plus the
        # surface the person was never asked to describe.
        filed.append(c.post("/api/friction", json={
            "reporter": "person", "session": "meet-104",
            "happened": "clicked the DNA link and the panel says there is no page here yet",
            "tried": "opened kg/entities/companies/ASWF.md",
            "context": {"workspace": "dna", "path": "kg/entities/companies/ASWF.md",
                        "meeting_id": "104",
                        "surface": {"chat": "meet-104", "chat_kind": "meeting", "at": "page"}},
        }, headers=HDR).json())

        # ── EDGE 3 · wrong-workspace, filed by an AGENT through the rig's LEGACY argument names ──
        # `report_friction(what_i_was_doing=…, what_went_wrong=…)` — the signature the live
        # machinery note still names. Backwards compatibility is proven by using it, not by a
        # comment saying it is kept.
        for _ in range(2):          # the same edge, twice: dedup must fold it into one row
            filed.append(c.post("/api/friction", json={
                "what_i_was_doing": "write the meeting note to the group desk",
                "what_went_wrong": "workspace_write landed in the wrong workspace — it wrote to "
                                   "personal although the chat mounts the dna group",
                "what_would_have_helped": "workspace_write defaulting to the chat's mounted group",
                "tool": "mcp__vexa__workspace_write", "workspace": "personal",
                "severity": "blocker", "kind": "wrong-workspace",
            }, headers=HDR).json())

        dump = c.get("/api/friction/dump?status=open", headers=HDR).text

        # ── the loop closes, and a fix that does not hold says so ────────────────────────────────
        target = filed[1]["id"]                      # the no-page finding
        c.post(f"/api/friction/{target}/fix", json={"fix_ref": "PR #1410 · a3742c4"}, headers=HDR)
        c.post("/api/friction", json={
            "reporter": "person", "happened": "clicked the DNA link and the panel says there is no "
                                              "page here yet",
            "context": {"workspace": "dna", "path": "kg/entities/companies/ASWF.md"},
        }, headers=HDR)
        after = c.get("/api/friction/dump?status=open&format=json", headers=HDR).json()

        by_id = {r["id"]: r for r in after["records"]}
        result = {
            "filed": len(filed),
            "rows": after["count"],
            "findings": len(after["findings"]),
            "deduped": [f["id"] for f in filed].count(filed[2]["id"]),
            "recurring": [f["kind"] for f in after["findings"] if f["status"] == "recurring"],
            "fix_that_did_not_hold": by_id.get(target, {}).get("fix_ref", ""),
            "dump": dump,
        }

        # The assertions ARE the proof; a replay that prints a dump nobody checked proves nothing.
        assert result["deduped"] == 2, "two reports of one edge must be ONE row"
        assert result["findings"] == 3, f"three edges → three findings, got {result['findings']}"
        assert result["recurring"] == ["no-page"], "a fix that did not hold must say so"
        assert result["fix_that_did_not_hold"] == "PR #1410 · a3742c4"
        for label in ("**Symptom**", "**Exact context**", "**Likely cause**", "**Logs**",
                      "**Repro**", "docker logs --since", "friction_fixed("):
            assert label in dump, f"the dump is not fixer-ready: {label} missing"
        for kind in ("missing-tool", "no-page", "wrong-workspace"):
            assert kind in dump, f"{kind} never reached the dump"
        print(f"friction replay: {result['filed']} reports → {result['rows']} rows → "
              f"{result['findings']} findings · recurring={result['recurring']}", file=out)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="print the result object, not the brief")
    a = ap.parse_args()
    res = run(out=sys.stderr if not a.json else sys.stderr)
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "dump"}, indent=2))
    else:
        print(res["dump"])
    return 0


if __name__ == "__main__":          # pragma: no cover — the runnable artefact
    sys.exit(main())
