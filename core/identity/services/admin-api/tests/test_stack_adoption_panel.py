"""O-STACK-ADOPTION — the adoption panel's SQL, run against a real ephemeral Postgres.

WHY IT LIVES HERE. The dashboard reads three tables across two databases: `users` and `meetings`
(this package's own schema, `admin_api.schema.models`) and flows' `reaction`. This is the only
package on the line that already carries a Postgres fixture — `core/flows` runs its suite on a
stdlib sqlite double and states outright that "Postgres is the production dialect, never a test
one", and these queries are Postgres-only (`jsonb`, LATERAL, FILTER, `percentile_cont`), so
putting them there would mean either lying about the dialect or adding testcontainers to a
package that deliberately has none. `reaction` is created here from `core/flows/schema.sql`
itself, read at runtime, so the shape under test is the shipped shape and cannot drift.

BOTH DATABASES IN ONE. Postgres cannot join across databases and neither can the dashboard —
every panel names exactly one datasource. That makes a single test database sound: no query can
accidentally see a table it would not have in production, because no query names one.

WHAT IS PROVEN: each panel's SQL parses and executes on the real dialect, and returns the right
answer for a seeded adoption story with a known hand-computed result — including the degraded
case where an invite carries no attendee roster. WHAT IS NOT: that Grafana renders it. That is
a separate check, recorded in the receipt.
"""
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker

pytestmark = requires_docker

# core/identity/services/admin-api/tests/ -> repo root is five parents up.
ROOT = Path(__file__).resolve().parents[5]
DASH = ROOT / "deploy" / "helm" / "charts" / "vexa" / "dashboards"
SQL_DIR = DASH / "sql"
FLOWS_SCHEMA = ROOT / "core" / "flows" / "schema.sql"

#: The window every seeded row sits inside, and the values the dashboard defaults to.
DEFAULTS = {"window_days": "30", "min_meetings": "1"}


def render(stem: str, **vars_) -> str:
    """Substitute the dashboard's textbox variables the way Grafana does: literal text, before
    the statement reaches the server. Kept mechanical (one regex, whole-word) so the string the
    test runs differs from the shipped string in exactly the substitution and nothing else."""
    sql = (SQL_DIR / f"{stem}.sql").read_text()
    for k, v in {**DEFAULTS, **vars_}.items():
        sql = re.sub(rf"\${k}\b", str(v), sql)
    assert "$" not in sql.replace("$$", ""), f"{stem}: an unsubstituted variable is left in the SQL"
    return sql


def q(engine, stem: str, **vars_):
    with engine.connect() as c:
        rows = c.execute(text(render(stem, **vars_))).mappings().all()
    return [dict(r) for r in rows]


# ── the seeded story ──────────────────────────────────────────────────────────────────────────
#
# alice organizes m1 and m2 and invites bob and carol.
# bob, having only ever attended, later organizes m3 of his own  -> a SECOND INVITE.
# carol attends and never organizes                              -> in the cohort, not converted.
# dave organizes m4 from the very first row                      -> never in the cohort at all.
# m5 arrives with NO attendee roster                             -> the degraded shape.
#
# cohort = {bob, carol}; converted = {bob}  ->  second-invite rate = 50.0%
DAY = 86400.0
NOW = None  # filled per-run from the DB clock so the window arithmetic is the server's own

INVITES = [
    # (id, organizer, participants, group, meeting_id, days_ago)
    ("i1", "alice@bank.example", ["bob@bank.example", "carol@bank.example"], "treasury", "m1", 20),
    ("i2", "alice@bank.example", ["bob@bank.example"], "treasury", "m2", 14),
    ("i3", "bob@bank.example", ["alice@bank.example"], "treasury", "m3", 6),
    ("i4", "dave@bank.example", ["carol@bank.example"], "risk", "m4", 10),
    ("i5", "alice@bank.example", None, None, "m5", 3),          # no roster at all
]
# every invite above also completed, except m5's, which the bot never captured
COMPLETED = [i for i in INVITES if i[4] != "m5"]


@pytest.fixture()
def engine(pg_url):
    eng = create_engine(pg_url)
    Base.metadata.drop_all(eng)
    ensure_schema_sync(eng, Base)
    with eng.begin() as c:
        # `reaction` comes from the flows engine's OWN generated schema, read at runtime — the
        # test cannot drift from the table it is asserting against.
        c.execute(text("DROP TABLE IF EXISTS reaction CASCADE"))
        for stmt in _reaction_ddl():
            c.execute(text(stmt))
        now = float(c.execute(text("SELECT extract(epoch FROM now())")).scalar())
        for rid, org, parts, grp, mid, days in INVITES:
            _admit(c, rid, "invite.received", now - days * DAY,
                   _refs(org, parts, grp, mid))
        for rid, org, parts, grp, mid, days in COMPLETED:
            _admit(c, f"c-{rid}", "meeting.completed", now - days * DAY + 3600,
                   _refs(org, parts, grp, mid))
        # the platform-side rows the two app-DB panels read
        for i, (_, _, _, _, mid, days) in enumerate(COMPLETED, start=1):
            c.execute(text(
                "INSERT INTO users (id, email, created_at) VALUES (:i, :e, now() - :d * interval '1 day')"
                " ON CONFLICT DO NOTHING"), {"i": i, "e": f"u{i}@bank.example", "d": days})
            c.execute(text(
                "INSERT INTO meetings (id, user_id, platform, platform_specific_id, status, created_at)"
                " VALUES (:i, :i, 'teams', :m, 'completed', now() - :d * interval '1 day')"),
                {"i": i, "m": mid, "d": days})
    yield eng
    Base.metadata.drop_all(eng)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS reaction CASCADE"))
    eng.dispose()


def _reaction_ddl() -> list[str]:
    """Just the `reaction` statements out of the flows engine's generated schema — the table and
    its index, nothing else.

    COMMENTS COME OUT BEFORE THE SPLIT, not after. The file is plain DDL with no dollar-quoting,
    so splitting on `;` is sound for the statements — but that generated header contains the
    word "stdlib-pure; the drift gate keeps it honest", and a semicolon inside a `--` comment
    cuts the comment in half and hands Postgres its second half as a statement. Stripping the
    comment lines first removes the only place a `;` can hide."""
    body = "\n".join(l for l in FLOWS_SCHEMA.read_text().splitlines()
                     if not l.lstrip().startswith("--"))
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    keep = [s for s in stmts if re.search(r"(TABLE IF NOT EXISTS reaction|INDEX .*ON reaction)", s)]
    assert keep, f"no `reaction` DDL found in {FLOWS_SCHEMA} — has the flows schema moved?"
    return keep


def _refs(organizer, participants, group, meeting_id) -> dict:
    r = {"organizer": organizer, "meeting_id": meeting_id, "title": f"Standup {meeting_id}"}
    if participants is not None:
        r["participants"] = participants
    if group is not None:
        r["group"] = group
    return r


def _admit(conn, rid, event_type, created_at, refs):
    conn.execute(text(
        "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow,"
        " flow_version, step, status, attempt, next_run_at, created_at, updated_at)"
        " VALUES (:rid, :rid, :et, :refs, 'post_meeting', 1, 'start', 'done', 0, 0, :ts, :ts)"),
        {"rid": rid, "et": event_type, "refs": json.dumps(refs), "ts": created_at})


# ── the headline ──────────────────────────────────────────────────────────────────────────────

def test_second_invite_rate_is_the_share_of_attendees_who_later_organized(engine):
    """bob and carol were both attendees before ever organizing; only bob went on to organize.
    dave organized from his first row and must NOT dilute the denominator."""
    assert q(engine, "second-invite-rate") == [{"Second-invite rate %": 50.0}]


def test_second_invite_cohort_shows_the_numbers_behind_the_rate(engine):
    row = q(engine, "second-invite-cohort")[0]
    assert row["attendee-only cohort"] == 2          # bob, carol — not dave
    assert row["later organized"] == 1               # bob
    assert float(row["rate %"]) == 50.0
    # bob first attended at -20d and first organized at -6d
    assert float(row["median days to second invite"]) == pytest.approx(14.0, abs=0.1)


def test_second_invites_per_week_places_the_conversion_in_its_week(engine):
    rows = q(engine, "second-invites-per-week")
    assert len(rows) == 1 and rows[0]["second invites"] == 1


def test_an_organizer_from_the_start_is_never_in_the_cohort(engine):
    """The rule is 'attendee BEFORE ever organizing'. dave attends nothing and organizes at -10d;
    alice organizes at -20d and only later appears as an attendee on bob's m3 at -6d. Neither is
    a second invite, and the rate must not move when they are present."""
    with engine.begin() as c:
        cohort = c.execute(text(render("second-invite-cohort"))).mappings().first()
    assert cohort["attendee-only cohort"] == 2


# ── active users, and its two knobs ───────────────────────────────────────────────────────────

def test_active_users_counts_organizers_and_attendees_of_captured_meetings(engine):
    """m1..m4 completed. Present across them: alice, bob, carol, dave = 4. m5 has no completion,
    so alice is not counted twice for it."""
    assert q(engine, "active-users") == [{"Active users": 4}]


def test_active_user_threshold_is_a_real_knob(engine):
    """At >=2 captured meetings only alice (m1,m2,m4-as-nobody… m1,m2 + m3 as attendee) and bob
    (m1,m2 as attendee + m3 as organizer) qualify; carol (m1, m4) also has two. dave has one."""
    at2 = q(engine, "active-users", min_meetings=2)[0]["Active users"]
    at9 = q(engine, "active-users", min_meetings=9)[0]["Active users"]
    assert at2 == 3, "alice, bob and carol each appear in two captured meetings"
    assert at9 == 0


def test_active_user_window_is_a_real_knob(engine):
    """Everything seeded sits inside 30 days; a 7-day window keeps only m3 (-6d)."""
    assert q(engine, "active-users", window_days=7)[0]["Active users"] == 2   # bob + alice


def test_active_users_by_month_is_countable_and_carries_no_addresses(engine):
    rows = q(engine, "active-users-by-month")
    assert rows and all(set(r) == {"month", "active users"} for r in rows)
    assert not any("@" in str(v) for r in rows for v in r.values())


# ── coverage, teams, and the honest-degradation gauge ─────────────────────────────────────────

def test_meetings_captured_counts_meetings_not_rows(engine):
    assert q(engine, "meetings-captured") == [{"Meetings captured": 4}]


def test_teams_covered_counts_group_tags(engine):
    assert q(engine, "teams-covered") == [{"Teams covered": 2}]      # treasury, risk


def test_coverage_by_team_keeps_untagged_meetings_visible(engine):
    rows = {r["team"]: r for r in q(engine, "coverage-by-team")}
    assert rows["treasury"]["meetings captured"] == 3
    assert rows["risk"]["meetings captured"] == 1
    assert "(no team tag)" not in rows, "every completed seed row carries a group"


def test_roster_coverage_reports_the_share_of_invites_carrying_attendees(engine):
    """Four of the five seeded invites carry a roster; i5 does not."""
    assert q(engine, "roster-coverage") == [
        {"Invites carrying an attendee roster %": 80.0}]


def test_no_roster_anywhere_degrades_to_blank_not_to_zero(engine):
    """THE FAILURE THIS PANEL SET EXISTS TO MAKE VISIBLE. With `participants` stripped from every
    row — an older parser, or completions published by meeting-api, whose domain holds no invite
    — the queries must still RUN, the cohort must be empty, and the rate must come back NULL so
    Grafana shows 'No data'. A 0% here would be a lie: it would read as 'nobody forwarded an
    invite' when the truth is 'we cannot see'. Roster coverage is what tells them apart."""
    with engine.begin() as c:
        c.execute(text("UPDATE reaction SET subject_refs = (subject_refs::jsonb - 'participants')::text"))
    assert q(engine, "second-invite-rate") == [{"Second-invite rate %": None}]
    assert q(engine, "second-invite-cohort")[0]["attendee-only cohort"] == 0
    assert q(engine, "roster-coverage") == [{"Invites carrying an attendee roster %": 0.0}]
    # organizer-only still works, and that is the point of degrading rather than failing
    assert q(engine, "active-users")[0]["Active users"] == 3          # alice, bob, dave


# ── the platform cross-check ──────────────────────────────────────────────────────────────────

def test_platform_panels_read_the_application_database(engine):
    assert q(engine, "meetings-platform") == [{"Meetings completed (platform)": 4}]
    row = q(engine, "users-platform")[0]
    assert row["Accounts"] == 4 and row["New in window"] == 4


def test_people_per_week_runs_and_returns_a_time_column(engine):
    rows = q(engine, "people-per-week")
    assert rows and "time" in rows[0] and "people in captured meetings" in rows[0]


# ── the bucket belongs to the event, not to the reader ────────────────────────────────────────

def test_calendar_buckets_do_not_move_with_the_readers_timezone(engine):
    """REGRESSION, and it was found by rendering the dashboard rather than by running this file.

    The same seeded database showed the month as 2026-09 in one browser and 2026-08 in another.
    `to_timestamp()` returns a timestamptz; `date_trunc` on a timestamptz resolves in the
    SESSION's timezone; Grafana sets that session timezone from the dashboard's
    `timezone: browser`. So the month a person was counted in depended on where the person
    READING the dashboard was sitting — on the one table that becomes an invoice.

    A single-session test cannot see this, which is why it asserts across two sessions
    explicitly. Every calendar bucket is now pinned with `AT TIME ZONE 'UTC'`.
    """
    def in_zone(tz, stem):
        with engine.connect() as c:
            c.execute(text(f"SET TIME ZONE '{tz}'"))
            return [dict(r) for r in c.execute(text(render(stem))).mappings().all()]

    for stem in ("active-users-by-month", "people-per-week", "second-invites-per-week"):
        assert in_zone("UTC", stem) == in_zone("Pacific/Kiritimati", stem) \
            == in_zone("Pacific/Midway", stem), \
            f"{stem}: the bucket moved with the session timezone (+14 vs UTC vs -11)"


# ── the generated artifact ────────────────────────────────────────────────────────────────────

def test_dashboard_json_matches_sql_files():
    """The committed dashboard is exactly what `sql/` plus the layout generate. A hand-edit of
    the JSON — the one way these queries could silently diverge from the reviewed SQL — fails
    here. Runs without docker on purpose: it is a drift check, not a database test."""
    import subprocess
    r = subprocess.run(["python3", str(DASH / "gen_dashboard.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_panel_names_exactly_one_datasource_and_no_query_joins_two_databases():
    """Postgres cannot join across databases, so a panel mixing `reaction` with `users` or
    `meetings` would fail only at render time, in front of the customer."""
    dash = json.loads((DASH / "adoption-panel.json").read_text())
    app_tables, flows_tables = {"users", "meetings"}, {"reaction"}
    for panel in dash["panels"]:
        uids = {t["datasource"]["uid"] for t in panel["targets"]} | {panel["datasource"]["uid"]}
        assert len(uids) == 1, f"{panel['title']}: more than one datasource"
        sql = panel["targets"][0]["rawSql"]
        named = {t for t in app_tables | flows_tables
                 if re.search(rf"\bFROM\s+{t}\b", sql, re.I)}
        assert not (named & app_tables and named & flows_tables), \
            f"{panel['title']}: joins across two databases"
        expect = "vexa-flows-db" if named & flows_tables else "vexa-app-db"
        assert uids == {expect}, f"{panel['title']}: reads {named} but points at {uids}"


def test_no_panel_can_write():
    dash = json.loads((DASH / "adoption-panel.json").read_text())
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|COPY)\b", re.I)
    for panel in dash["panels"]:
        sql = panel["targets"][0]["rawSql"]
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        assert not forbidden.search(body), f"{panel['title']}: writes"
