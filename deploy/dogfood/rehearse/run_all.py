#!/usr/bin/env python3
"""THE CATALOGUE IS THE TEST — PRD decision 38.5.

Runs every state in `states.yaml`, clicks nothing, verifies the artefacts each recipe declares,
and files every failure as friction (decision 33) so a fixing agent gets it in the shape it can
act on. Per state it reports: pass/fail, wall time, and the link produced.

    python -m rehearse.run_all --stub                    # offline, against the door stub
    python -m rehearse.run_all --as-domain rehearse.test # against the running stack
    python -m rehearse.run_all --only organizer-invited,reply-pending

WHY CLICKING NOTHING IS THE POINT. Every state ends at a touch — a mail with one link. Whether
that link works when a person clicks it is the founder's judgment and belongs in a walk. What a
suite can prove is everything up to the click: the touch exists, its link is a scaffold, the
record resolves, the desk holds what it should. A loop that also clicked would be measuring the
agent's answer, which is a different (slower, judged) thing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

from . import catalogue as cat
from .doors import Doors, LiveDoors
from .engine import DEFAULT_MEETING, DEFAULT_WHEN, Refused, rehearse

#: Where a finding goes when the intake cannot take it. Overridable, because a run on a host with
#: a read-only /tmp still has to be able to keep its findings.
FRICTION_FALLBACK = pathlib.Path(
    os.environ.get("VEXA_REHEARSE_FRICTION_LOG", "/tmp/rehearse-friction.jsonl"))


def file_friction(doors_kind: str, state: str, res, reporter) -> dict:
    """One failing state → one friction record, in the ledger's finding shape.

    `symptom · exact context · pointers · repro` — the four fields `friction_dump` groups on. The
    repro line is the whole value: a fixing agent must be able to re-enter the state without
    asking anybody how.
    """
    failed = [v for v in res.verify if not v["ok"]]
    stopped = next((s for s in res.steps if not s.get("ok", True)), None)
    symptom = (res.error or (failed[0]["detail"] if failed
                             else "the state ran but its verify block did not pass"))
    rec = {
        "tool": "rehearse",
        "kind": "error" if res.error else "unfulfilled",
        "severity": "blocker",
        "what_i_was_doing": f"entering the state `{state}` as {res.subject} through {doors_kind} "
                            f"doors, so a touch could be rehearsed without a rebuild",
        "what_went_wrong": f"{state}: {symptom}",
        "what_would_have_helped": (
            f"repro: python -m rehearse.run_all --only {state}"
            + (f"  (stopped at step `{stopped['do']}` on the {stopped['door']} door)"
               if stopped else "")
            + f"  ·  failed checks: {', '.join(v['check'] for v in failed) or 'none'}"),
        "error": symptom[:900],
    }
    # THE FILE FIRST, ALWAYS — the rig's own rule for `report_friction`, and it earned its place
    # here on the first live run: `POST /api/friction` is not on the deployed agent-api image yet
    # (#1412), so every record 404'd. A finding that exists only in a 404 is a finding nobody has.
    # The file is the fallback, never the store: the intake below is still the destination.
    try:
        FRICTION_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        with FRICTION_FALLBACK.open("a") as f:
            f.write(json.dumps({"at": time.time(), "state": state, **rec}) + "\n")
        rec["written_to"] = str(FRICTION_FALLBACK)
    except OSError as e:
        rec["written_to"] = f"FAILED: {e}"
    if reporter:
        try:
            reporter(rec)
            rec["filed"] = True
        except Exception as e:                                     # noqa: BLE001
            # A 404 here is NOT a pass and never becomes one: the record is on disk, the run is
            # still red, and the report says the intake refused it and where the record went.
            rec["filed"] = False
            rec["file_error"] = f"{type(e).__name__}: {e}"
    return rec


def run(doors: Doors, *, catalog: cat.Catalogue | None = None, only: list | None = None,
        domain: str | None = None, meeting: str = DEFAULT_MEETING, when: str = DEFAULT_WHEN,
        mailbox: str = "", reporter=None, env: dict | None = None,
        dry_run: bool = False) -> dict:
    catalog = catalog or cat.load()
    dom = domain or catalog.domain(env)
    names = [n for n in catalog.states if not only or n in only]
    rows, frictions = [], []
    t0 = time.time()
    for name in names:
        subject = f"rehearse-{name}@{dom}"
        started = time.time()
        try:
            res = rehearse(name, subject, meeting=meeting, when=when, doors=doors,
                           catalog=catalog, env=env, mailbox=mailbox, dry_run=dry_run)
        except (Refused, Exception) as e:                          # noqa: BLE001
            rows.append({"state": name, "as": subject, "ok": False,
                         "wall_s": round(time.time() - started, 1), "link": "",
                         "why": f"{type(e).__name__}: {e}"})
            continue
        link = next(iter(res.links.values()), "")
        rows.append({"state": name, "as": subject, "ok": res.ok,
                     "wall_s": round(res.wall_s, 1), "link": link,
                     "checks": [f"{v['check']}={'ok' if v['ok'] else 'FAIL'}" for v in res.verify],
                     **({"why": res.error} if res.error else {})})
        if not res.ok:
            frictions.append(file_friction(type(doors).__name__, name, res, reporter))
    return {"ran": len(rows), "passed": sum(1 for r in rows if r["ok"]),
            "failed": [r["state"] for r in rows if not r["ok"]],
            "wall_s": round(time.time() - t0, 1), "states": rows, "friction": frictions}


def render(report: dict) -> str:
    w = max([len(r["state"]) for r in report["states"]] + [5])
    lines = [f"{'state'.ljust(w)}  {'':4}  {'wall':>7}  link / why"]
    for r in report["states"]:
        mark = "PASS" if r["ok"] else "FAIL"
        tail = r["link"] or r.get("why", "")
        lines.append(f"{r['state'].ljust(w)}  {mark}  {r['wall_s']:>6.1f}s  {tail[:96]}")
    lines.append("")
    lines.append(f"{report['passed']}/{report['ran']} states green in {report['wall_s']}s"
                 + (f" · failed: {', '.join(report['failed'])}" if report["failed"] else ""))
    if report["friction"]:
        filed = sum(1 for f in report["friction"] if f.get("filed"))
        unfiled = [f for f in report["friction"] if not f.get("filed")]
        lines.append(f"{len(report['friction'])} friction record(s): {filed} filed to the intake "
                     f"(decision 33), {len(unfiled)} written to "
                     f"{report['friction'][0].get('written_to', FRICTION_FALLBACK)}")
        if unfiled:
            lines.append(f"  the intake refused them: {unfiled[0].get('file_error', '?')}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run every state in the catalogue")
    ap.add_argument("--stub", action="store_true",
                    help="run against the offline door stub — proves the recipes, touches nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and guard every step, execute none")
    ap.add_argument("--only", default="", help="comma-separated state names")
    ap.add_argument("--meeting", default=DEFAULT_MEETING)
    ap.add_argument("--when", default=DEFAULT_WHEN)
    ap.add_argument("--domain", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.stub:
        from .stub_doors import StubDoors
        doors: Doors = StubDoors()
        reporter = None
    else:
        doors = LiveDoors()
        reporter = _live_reporter()
    report = run(doors, only=[s for s in a.only.split(",") if s] or None,
                 domain=a.domain or None, meeting=a.meeting, when=a.when,
                 reporter=reporter, dry_run=a.dry_run)
    print(json.dumps(report, indent=1) if a.json else render(report))
    return 0 if not report["failed"] else 1


def _live_reporter():
    """File a friction record through decision 33's route — `POST /api/friction` on AGENT-API.

    Not the flows `friction` table: agent-api owns the store (the terminal's "Report this" posts
    there, the blank script deletes the flows table with the rest of the lane, and only agent-api's
    record has the context and status columns the dump groups on). The reasoning is written down at
    `core/agent/shared/friction.py`; this comment exists so the second writer does not appear here.

    Never fatal. A suite that cannot file its findings still has to report its findings.
    """
    from .doors import AGENT_API, _http

    def post(rec: dict) -> None:
        st, body = _http("POST", f"{AGENT_API}/api/friction", None, {
            "reporter": "rehearse", "kind": rec["kind"], "severity": rec["severity"],
            "tried": rec["what_i_was_doing"], "happened": rec["what_went_wrong"],
            "would_help": rec["what_would_have_helped"],
            "context": {"tool": "rehearse", "error": rec["error"]}})
        if not 200 <= st < 300:
            raise RuntimeError(f"friction intake answered {st}: {str(body)[:160]}")
    return post


if __name__ == "__main__":
    sys.exit(main())
