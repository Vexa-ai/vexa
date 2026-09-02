"""OFFLINE PROOF of GET /timeline against a REAL lane database — read-only, no service running.

Tries the sim lane's `flows_sim` first (the instruction's preference) and falls back to the founder
lane's `flows`, saying which it used. Both are opened through the container's psql with the SERVER
enforcing read-only (`default_transaction_read_only=on`), so the proof cannot write even by mistake;
the only statements it issues are the two SELECTs `flows_timeline.service` issues in production.

The meetings half is read the same way, straight from the `vexa` database, instead of over the
gateway: the point of the check is the merge, the scoping and the order, and a live HTTP hop would
add a moving part that proves nothing about any of them.

Usage: python3 proof_timeline.py <uid> [YYYY-MM-DD]
"""
from __future__ import annotations

import json
import subprocess
import sys
import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from flows_timeline import build_timeline           # noqa: E402
from flows_timeline.model import event_from_meeting  # noqa: E402

CONTAINER = "vexa-dogfood-postgres-1"
UNIT, RECORD = "\x1f", "\x1e"


class PsqlDB:
    """A READ-ONLY `DB` — the same `execute(sql, params)` seam the engine speaks, over psql."""

    dialect = "postgres"

    def __init__(self, database: str) -> None:
        self.database = database

    def _quote(self, v) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return repr(float(v))
        return "'" + str(v).replace("'", "''") + "'"

    def execute(self, sql: str, params: dict | None = None) -> list[tuple]:
        for key in sorted((params or {}), key=len, reverse=True):
            sql = sql.replace(f":{key}", self._quote(params[key]))
        out = subprocess.run(
            ["docker", "exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on", CONTAINER,
             "psql", "-U", "postgres", "-d", self.database, "-At", "-F", UNIT, "-R", RECORD,
             "-c", sql],
            capture_output=True, text=True, check=True).stdout
        rows = [r for r in out.split(RECORD) if r.strip()]
        return [tuple(f if f != "" else None for f in r.split(UNIT)) for r in rows]

    def executescript(self, sql: str) -> None:  # pragma: no cover — never called by a read
        raise RuntimeError("read-only")


def meetings_for(uid: str) -> list[dict]:
    rows = PsqlDB("vexa").execute(
        "SELECT id, status, start_time, end_time, created_at, data::text FROM meetings "
        f"WHERE user_id = {int(uid)} ORDER BY id")
    out = []
    for mid, status, start, end, created, data in rows:
        try:
            blob = json.loads(data) if data else {}
        except ValueError:
            blob = {}
        out.append({"id": mid, "status": status, "start_time": start, "end_time": end,
                    "created_at": created, "data": blob})
    return out


def has_rows(database: str) -> bool:
    try:
        n = PsqlDB(database).execute("SELECT count(*) FROM reaction")[0][0]
        return int(n or 0) > 0
    except subprocess.CalledProcessError:
        return False


def main() -> int:
    uid = sys.argv[1] if len(sys.argv) > 1 else "126"
    day = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
    start = datetime.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp()

    lane = "flows_sim" if has_rows("flows_sim") else "flows"
    if lane == "flows":
        print("the sim lane's flows_sim holds no reactions — reading the FOUNDER lane `flows`, "
              "READ-ONLY through the container's psql (default_transaction_read_only=on)")
    db = PsqlDB(lane)

    # The identity: resolved from the same database the product resolves it from, read-only.
    email = ""
    for (addr,) in PsqlDB("vexa").execute(f"SELECT email FROM users WHERE id = {int(uid)}"):
        email = str(addr or "").lower()
    print(f"lane={lane}  uid={uid}  email={email or '(none)'}  day={day}\n")

    out = build_timeline(db, uid, since=start, until=start + 86400, limit=100,
                         now=start + 86400, meetings=meetings_for,
                         identity=lambda _s: (uid, email))
    for e in out["events"]:
        produced = " ".join(f"{k}={v}" for k, v in sorted(e["produced"].items()))
        print(f"  {e['at']}  {e['kind']:<20} {e['status']:<10} "
              f"m={e.get('meeting_id') or '-':<5} {e['title'][:38]:<38} {produced[:90]}")

    # The same payload, rendered the two ways the product renders it — the control-MCP tool
    # (`format=text`) and the per-dispatch block (`format=preamble`), both in the person's zone.
    from flows_timeline import render_preamble
    tz = "Europe/Lisbon"
    print("\n  --- format=preamble, tz=" + tz + " " + "-" * 40)
    for row in render_preamble(out, tz).splitlines():
        print("  " + row)

    kinds = [e["kind"] for e in out["events"]]
    want = ["invite.received", "mail.sent", "meeting.held", "report.delivered"]
    pos, at = [], -1
    for k in want:
        try:
            at = kinds.index(k, at + 1)
        except ValueError:
            at = -1
            break
        pos.append(at)
    ats = [e["at_epoch"] for e in out["events"]]
    print(f"\n  events: {len(kinds)}   ascending: {ats == sorted(ats)}")
    print(f"  the DNA sequence {want} in order: {'YES at ' + str(pos) if at >= 0 else 'NO'}")
    return 0 if at >= 0 and ats == sorted(ats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
