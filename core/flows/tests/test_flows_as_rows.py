"""Flows-as-rows: DB-submitted definitions hydrate against the image's step vocabulary and go
live without redeploys; validation is submission-time; params reach steps via ctx.flow.param."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Done, EventType, Registry, SqliteDB, admit, tick  # noqa: E402
from fixtures import rig, drain  # noqa: E402


def test_db_flow_hydrates_and_runs_with_params():
    db, reg, clock, world = rig()
    db.executescript("""CREATE TABLE IF NOT EXISTS flow_version (
        name text, version integer, on_event text, steps text, params text,
        status text DEFAULT 'active', created_by text, created_at REAL,
        PRIMARY KEY (name, version))""")
    db.execute("""INSERT INTO flow_version VALUES
        ('api_flow', 1, 'api.requested', '["commit_summary"]',
         '{"label": "from-the-api"}', 'active', 't', 0)""")
    assert reg.refresh_from_db(db) == 1
    f = reg.get("api_flow", 1)
    assert f.param("label") == "from-the-api" and f.param("absent", 7) == 7
    admit(db, reg, clock, source_event_id="api-1", event_type="api.requested",
          subject_refs={"meeting": "m-api"})
    drain(db, reg, clock)
    assert world.commits == ["sha-m-api"]


def test_unknown_step_rejected_at_hydration_and_row_stays_dormant():
    db, reg, clock, world = rig()
    db.executescript("""CREATE TABLE IF NOT EXISTS flow_version (
        name text, version integer, on_event text, steps text, params text,
        status text DEFAULT 'active', created_by text, created_at REAL,
        PRIMARY KEY (name, version))""")
    db.execute("""INSERT INTO flow_version VALUES
        ('bad_flow', 1, 'x.y', '["not_a_step"]', NULL, 'active', 't', 0)""")
    assert reg.refresh_from_db(db) == 0                 # dormant, never a runtime KeyError
    with pytest.raises(ValueError):
        reg.flow_by_names(name="bad", version=1, on_event="x.y", step_names=["not_a_step"])


def test_db_version_supersedes_code_version_for_new_events():
    db, reg, clock, world = rig()
    db.executescript("""CREATE TABLE IF NOT EXISTS flow_version (
        name text, version integer, on_event text, steps text, params text,
        status text DEFAULT 'active', created_by text, created_at REAL,
        PRIMARY KEY (name, version))""")
    # post_meeting@1 exists in code (fixtures); submit @2 dropping the email step
    db.execute("""INSERT INTO flow_version VALUES
        ('post_meeting', 2, 'meeting.completed',
         '["await_completion","process_transcript","commit_summary"]', NULL, 'active', 't', 0)""")
    assert reg.refresh_from_db(db) == 1
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    admit(db, reg, clock, source_event_id="c-2", event_type="meeting.completed",
          subject_refs={"meeting": "m-1", "inviter": "anna@bank.com",
                        "participants": ["anna@bank.com"]})
    drain(db, reg, clock)
    assert world.commits == ["sha-m-1"]
    assert not any(a == "sha-m-1" for _, a in world.emails)   # v2 (no email step) governed
