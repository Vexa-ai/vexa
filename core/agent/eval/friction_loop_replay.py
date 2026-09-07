#!/usr/bin/env python
"""Offline replay: does the rough-edges loop still get three different producers onto one carrier?

PRD decision 33's proof obligation — *"dump it in a way that we can just dump that to an agent that
would just fix that (like you are here)."* This is that end to end with the cost taken out: no
stack, no docker, no redis, no flows database, no model. A real FastAPI agent-api, the real
worker-side detectors, the real record module, and the real route — under a second, and it runs in
CI (`tests/test_friction_replay.py` imports and asserts it).

REDUCED FOR #1510. The carrier moved to flows (`friction-sink-in-flows`, `friction.reported`), and
agent-api's own store — the dedup, the `open → fixed → recurring` status machine, the markdown
dump — is deleted with it (`shared/friction.py`'s module docstring says why: those only made sense
with a stateful store, and this route is a thin forward now). So this replay no longer proves dedup
or recurrence; what it still proves, and the only thing left to prove offline without a flows
database, is:

  1. **Three edges arrive by three different doors and land in one shape.** A missing toolbelt
     filed by the HARNESS at spawn (the model never ran — ledger F70), a no-page filed by a PERSON
     from the terminal, and a wrong-workspace filed by an AGENT through the rig's own legacy
     argument names. Nothing here reformats them by hand; each posts what its real caller posts.
  2. **Every one of them is accepted and forwarded onto flows' own route** — `control_plane
     .publish.post_friction` is monkeypatched to capture the normalized record it was handed
     rather than to reach a real flows-api, so this stays offline; the assertions are against the
     CAPTURED record, which is the exact shape a real flows deployment would receive as query
     parameters.
  3. **A report the flows carrier would refuse (no session) is refused here too**, before it ever
     reaches the forward.

The live end-to-end proof — dedup, `friction.fixed`, the rig's `friction_dump` — is a rig-side
rehearsal against a real flows-api, per #1510's C2/C3; it needs a database this replay does not.

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
    from control_plane import publish as publish_mod
    from worker import friction as wfr

    published: list[dict] = []
    real_post = publish_mod.post_friction

    def _capture(rec, **kw):
        published.append(dict(rec))
        return True, {"id": f"fr_replay{len(published)}", "recorded": True}

    publish_mod.post_friction = _capture
    try:
        with tempfile.TemporaryDirectory() as td:
            c = _client(Path(td) / "workspaces")
            filed: list[dict] = []

            # ── EDGE 1 · missing-tool, filed by the HARNESS at spawn ────────────────────────────
            # The model never ran. This is ledger F70: a session with no toolbelt cannot call
            # report_friction, so if the harness does not file it, nothing ever does.
            rec = wfr.spawn_gap(url="https://rig.example/mcp", token="", config_written=False,
                                session="meet-104", subject="126")
            assert rec is not None, "the spawn gap must file when a toolbelt was intended and absent"
            filed.append(c.post("/api/friction", json=rec, headers=HDR).json())

            # ── EDGE 2 · no-page, filed by a PERSON from the terminal ───────────────────────────
            # Exactly the body `clients/terminal/src/surfaces/frictionApi.ts` builds.
            filed.append(c.post("/api/friction", json={
                "reporter": "person", "session": "meet-104",
                "happened": "clicked the DNA link and the panel says there is no page here yet",
                "tried": "opened kg/entities/companies/ASWF.md",
                "context": {"workspace": "dna", "path": "kg/entities/companies/ASWF.md",
                            "meeting_id": "104",
                            "surface": {"chat": "meet-104", "chat_kind": "meeting", "at": "page"}},
            }, headers=HDR).json())

            # ── EDGE 3 · wrong-workspace, filed by an AGENT through the rig's LEGACY arguments ──
            # `report_friction(what_i_was_doing=…, what_went_wrong=…)` — the signature the live
            # machinery note still names. Backwards compatibility is proven by using it.
            filed.append(c.post("/api/friction", json={
                "session": "meet-104",
                "what_i_was_doing": "write the meeting note to the group desk",
                "what_went_wrong": "workspace_write landed in the wrong workspace — it wrote to "
                                   "personal although the chat mounts the dna group",
                "what_would_have_helped": "workspace_write defaulting to the chat's mounted group",
                "tool": "mcp__vexa__workspace_write", "workspace": "personal",
                "severity": "blocker", "kind": "wrong-workspace",
            }, headers=HDR).json())

            # ── a report the carrier would refuse is refused BEFORE it reaches publish ──────────
            refused = c.post("/api/friction", json={"tried": "x", "happened": "no session at all"})

            result = {
                "filed": len(filed),
                "all_recorded": all(f.get("recorded") for f in filed),
                "publish_count": len(published),
                "sessions": sorted({p["session"] for p in published}),
                "refused_with_no_session_status": refused.status_code,
            }

        assert result["filed"] == 3
        assert result["all_recorded"]
        assert result["publish_count"] == 3, "three producers, three forwards — no dedup any more"
        assert result["sessions"] == ["meet-104"], "every edge here carried the same call's session"
        assert result["refused_with_no_session_status"] == 400
        for p in published:
            assert p["tried"] and p["happened"]
        print(f"friction replay: {result['filed']} reports → {result['publish_count']} published "
              f"onto friction.reported, session(s)={result['sessions']}", file=out)
        return result
    finally:
        publish_mod.post_friction = real_post


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="print the result object")
    a = ap.parse_args()
    res = run(out=sys.stderr)
    if a.json:
        print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":          # pragma: no cover — the runnable artefact
    sys.exit(main())
