#!/usr/bin/env python
"""One-shot: import the RETIRED agent-api FrictionStore's Redis records into the flows carrier.

#1510's C1/C5 delete `core/agent/control_plane/friction.py` (the Redis-backed `FrictionStore`) and
every reader of it. Its records do not migrate themselves -- this script is the migration, run BY
HAND, ONCE, against a live deployment's redis and flows database, never automatically. As of this
writing dogfood carries 13 such records (the tracking issue's own count, from the 2026-09-03 live
call -- several of them with `session=""`, which is exactly the gap PRD 40.9 exists to close and
why the new carrier refuses that shape going forward; this importer does NOT invent a session for
them, it carries the empty string through, because inventing one would misattribute history nobody
can verify any more).

WHAT IT DOES, per record read from `agent:friction:<id>` (the ids from `agent:friction:all`, the
zset the old store scored by report time):

  1. Admits ONE `friction.reported` event, keyed `friction-<original-id>` -- the ORIGINAL id, not a
     freshly minted one, so re-running this script is a no-op (`admit()` dedupes on
     `(source_event_id, flow)`; see `flows/admission.py`).
  2. If the record's `status` was `fixed` or `recurring` (the old store's status machine,
     `shared/friction.py`, module now trimmed of that machinery), ALSO admits a matching
     `friction.fixed` event (`fix_ref` from the old record, or `"(migrated, no fix_ref recorded)"`
     when the old record somehow lacked one) -- both are needed for the read-model fold
     (`flows_timeline.friction_for_subject`) to show `status: fixed` afterwards. `recurring` loses
     its regression story in this migration (the old record's `regressed_at`/second occurrence is
     not itself a distinct row on the new carrier) -- the fixed-then-regressed shape does not exist
     on the new carrier at all yet (see the carrier's own census entry), and inventing an extra
     synthetic occurrence to represent a regression that already happened would be recording a fact
     that did not occur on the new carrier's own terms. Migrated as `fixed` with the recorded
     `fix_ref`; the regression itself is legible only in this script's own printed report.

WHAT IT REFUSES TO DO: run without `--apply`. The default is `--dry-run` (the implicit default,
`--apply` opts out of it) -- it connects read-only to redis, prints every record it WOULD import,
and touches no database. `--apply` requires also passing `--yes` (a second, explicit confirmation)
because this writes into a shared flows database once and cannot be un-admitted.

Usage:
    python3 import_legacy_friction_redis.py --redis-url redis://localhost:6379/0
    python3 import_legacy_friction_redis.py --redis-url redis://... --apply --yes
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REDIS_KEY_PREFIX = "agent:friction:"
REDIS_INDEX = "agent:friction:all"


def _legacy_records(redis_url: str) -> list[dict]:
    """Every record the old store held, oldest first (`at` ascending) -- read-only: `GET`/`ZRANGE`
    only, never a write, regardless of `--apply`."""
    import redis

    r = redis.from_url(redis_url, decode_responses=True)
    ids = r.zrange(REDIS_INDEX, 0, -1)
    out = []
    for rid in ids:
        raw = r.get(f"{REDIS_KEY_PREFIX}{rid}")
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (TypeError, ValueError):
            print(f"  ! {rid}: unreadable JSON in redis, skipped", file=sys.stderr)
            continue
        rec.setdefault("id", rid)
        out.append(rec)
    return out


def _refs_for_reported(rec: dict) -> dict:
    """The `friction.reported` carrier's refs, off one legacy record (`shared/friction.py`'s
    pre-#1510 shape: `at, reporter, subject, session, kind, tried, happened, would_help, severity,
    context, log_refs, id, status, recurrence, first_at, fix_ref`)."""
    ctx = rec.get("context") or {}
    refs = {
        "uid": str(rec.get("subject") or ""),
        "session": str(rec.get("session") or ""),   # NOT invented -- carried through as-is, even ""
        "friction_id": str(rec.get("id") or ""),
        "what_i_tried": str(rec.get("tried") or ""),
        "what_happened": str(rec.get("happened") or ""),
        "severity": str(rec.get("severity") or "annoyance"),
    }
    if ctx.get("meeting_id"):
        refs["meeting_id"] = str(ctx["meeting_id"])
    if ctx.get("tool"):
        refs["tool"] = str(ctx["tool"])
    return refs


def _refs_for_fixed(rec: dict) -> dict:
    return {
        "friction_id": str(rec.get("id") or ""),
        "fix_ref": str(rec.get("fix_ref") or "") or "(migrated, no fix_ref recorded)",
    }


def _plan(records: list[dict]) -> list[dict]:
    """[{friction_id, reported_refs, fixed_refs|None}] -- the admissions this run would make."""
    plan = []
    for rec in records:
        fid = str(rec.get("id") or "")
        if not fid:
            continue
        status = str(rec.get("status") or "open")
        plan.append({
            "friction_id": fid,
            "reported_refs": _refs_for_reported(rec),
            "fixed_refs": _refs_for_fixed(rec) if status in ("fixed", "recurring") else None,
            "legacy_status": status,
        })
    return plan


def _print_plan(plan: list[dict]) -> None:
    print(f"{len(plan)} legacy record(s) found:")
    for p in plan:
        r = p["reported_refs"]
        line = (f"  fr={p['friction_id']} uid={r['uid'] or '(none)'} "
               f"session={r['session'] or '(EMPTY -- pre-#1510 gap)'} "
               f"status={p['legacy_status']} tried={r['what_i_tried'][:40]!r}")
        print(line)
        if p["fixed_refs"]:
            print(f"    -> also admits friction.fixed fix_ref={p['fixed_refs']['fix_ref']!r}")


def _apply(plan: list[dict], flows_db_url: str) -> None:
    from flows import Registry, SystemClock, admit, db_from_url
    from flows_defs import production

    db = db_from_url(flows_db_url)
    vocab = Registry()
    production.build(vocab, db)
    clock = SystemClock()

    admitted_reported = admitted_fixed = 0
    for p in plan:
        fid = p["friction_id"]
        n = admit(db, vocab, clock, source_event_id=f"friction-{fid}",
                 event_type="friction.reported", subject_refs=p["reported_refs"])
        admitted_reported += n
        print(f"  friction.reported fr={fid}: {'admitted' if n else 'already present (no-op)'}")
        if p["fixed_refs"]:
            n = admit(db, vocab, clock, source_event_id=f"friction-fix-{fid}",
                     event_type="friction.fixed", subject_refs=p["fixed_refs"])
            admitted_fixed += n
            print(f"  friction.fixed    fr={fid}: {'admitted' if n else 'already present (no-op)'}")
    print(f"\n{admitted_reported} friction.reported + {admitted_fixed} friction.fixed newly admitted "
         f"(re-running this script is a no-op for anything already imported).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--redis-url", required=True,
                    help="the OLD agent-api redis (REDIS_URL from its deployment config)")
    ap.add_argument("--flows-db-url", default="",
                    help="the flows database URL to admit into; required with --apply, ignored "
                         "in the (default) dry run")
    ap.add_argument("--apply", action="store_true",
                    help="actually admit the records. Without this: read-only, prints the plan.")
    ap.add_argument("--yes", action="store_true",
                    help="the second, explicit confirmation --apply also requires")
    args = ap.parse_args()

    records = _legacy_records(args.redis_url)
    plan = _plan(records)
    _print_plan(plan)

    if not args.apply:
        print("\nDRY RUN (default) -- nothing was written. Pass --apply --yes to import for real.")
        return 0
    if not args.yes:
        print("\n--apply given without --yes -- refusing. This writes into a shared flows "
             "database once and cannot be un-admitted; --yes is the explicit second confirmation.",
             file=sys.stderr)
        return 1
    if not args.flows_db_url:
        print("\n--apply needs --flows-db-url (the flows database to admit into).", file=sys.stderr)
        return 1

    _apply(plan, args.flows_db_url)
    return 0


if __name__ == "__main__":          # pragma: no cover -- the runnable artefact
    sys.exit(main())
