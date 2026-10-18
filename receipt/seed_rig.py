"""Seed the throwaway render rig with the SAME adoption story the pytest asserts on.

Not a product file — this lives in receipt/ and exists only so the screenshot shows numbers a
reader can check by hand against the test:

    alice organizes m1,m2 and invites bob + carol
    bob, having only ever attended, later organizes m3       -> the second invite
    carol attends and never organizes                        -> in the cohort, not converted
    dave organizes m4 from his first row                     -> never in the cohort
    m5 arrives with no attendee roster                       -> the degraded shape (80% coverage)

    cohort = {bob, carol} · converted = {bob} · second-invite rate = 50.0%
"""
import json
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(sys.argv[1])
DSN_APP = sys.argv[2]
DSN_FLOWS = sys.argv[3]
DAY = 86400.0

INVITES = [
    ("i1", "alice@bank.example", ["bob@bank.example", "carol@bank.example"], "treasury", "m1", 20),
    ("i2", "alice@bank.example", ["bob@bank.example"], "treasury", "m2", 14),
    ("i3", "bob@bank.example", ["alice@bank.example"], "treasury", "m3", 6),
    ("i4", "dave@bank.example", ["carol@bank.example"], "risk", "m4", 10),
    ("i5", "alice@bank.example", None, None, "m5", 3),
]
COMPLETED = [i for i in INVITES if i[4] != "m5"]


def reaction_ddl():
    body = "\n".join(l for l in (ROOT / "core/flows/schema.sql").read_text().splitlines()
                     if not l.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()
            and re.search(r"(TABLE IF NOT EXISTS reaction|INDEX .*ON reaction)", s)]


def refs(org, parts, grp, mid):
    r = {"organizer": org, "meeting_id": mid, "title": f"Standup {mid}"}
    if parts is not None:
        r["participants"] = parts
    if grp is not None:
        r["group"] = grp
    return r


with psycopg.connect(DSN_FLOWS, autocommit=True) as c:
    for stmt in reaction_ddl():
        c.execute(stmt)
    now = float(c.execute("SELECT extract(epoch FROM now())").fetchone()[0])
    rows = ([(r[0], "invite.received", now - r[5] * DAY, refs(r[1], r[2], r[3], r[4]))
             for r in INVITES]
            + [(f"c-{r[0]}", "meeting.completed", now - r[5] * DAY + 3600,
                refs(r[1], r[2], r[3], r[4])) for r in COMPLETED])
    for rid, et, ts, rf in rows:
        c.execute(
            "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow,"
            " flow_version, step, status, attempt, next_run_at, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,'post_meeting',1,'start','done',0,0,%s,%s)"
            " ON CONFLICT DO NOTHING",
            (rid, rid, et, json.dumps(rf), ts, ts))
    print("flows seeded:", c.execute("SELECT count(*) FROM reaction").fetchone()[0], "reactions")

with psycopg.connect(DSN_APP, autocommit=True) as c:
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, name VARCHAR(100),
        created_at TIMESTAMP DEFAULT now())""")
    c.execute("""CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, platform VARCHAR(100) NOT NULL,
        platform_specific_id VARCHAR(255), status VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT now())""")
    for i, r in enumerate(COMPLETED, start=1):
        c.execute("INSERT INTO users (id,email,created_at) VALUES (%s,%s, now() - %s * interval '1 day')"
                  " ON CONFLICT DO NOTHING", (i, f"u{i}@bank.example", r[5]))
        c.execute("INSERT INTO meetings (id,user_id,platform,platform_specific_id,status,created_at)"
                  " VALUES (%s,%s,'teams',%s,'completed', now() - %s * interval '1 day')"
                  " ON CONFLICT DO NOTHING", (i, i, r[4], r[5]))
    print("app seeded:", c.execute("SELECT count(*) FROM meetings").fetchone()[0], "meetings")
