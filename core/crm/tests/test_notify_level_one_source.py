"""ONE WRITER FOR THE NOTIFICATION LEVEL (PRD 0.13.4 S6, founder follow-up 2026-10-17).

A room's or workspace's notification level lives in ONE place: the person's identity notification
policy (`GET`/`PUT /user/notifications`, `rooms: {"ws:<slug>" | "room:<id>": level}`), which flows'
delivery gate reads. crm's `member_of.notify` is retired: nothing writes it, nothing answers it, and a
set that names it is refused with the door to use instead. Two stores for one value is the silent
failure the one-writer rule exists to stop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crm import member_settings as M

ROOT = Path(__file__).resolve().parents[3]


def test_crm_writes_no_notify_level():
    assert "notify" not in M.KEYS and "notify" not in M.DEFAULTS
    for value in ("all", "mentions", "none"):
        with pytest.raises(M.Invalid) as e:
            M.check_settings({"notify": value})
        assert "/user/notifications" in str(e.value)


def test_crm_answers_no_notify_level():
    row = {"pinned": False, "hidden": False, "muted": False, "notify": "mentions", "room_order": [],
           "level": None, "collapsed": [], "updated_at": None}
    assert "notify" not in M._view(row)
    schema = json.loads((ROOT / "core/crm/contracts/crm.v1/crm.schema.json").read_text())
    ws = schema["$defs"]["WorkspaceSettings"]
    assert "notify" not in ws["required"] and ws["properties"]["notify"].get("deprecated") is True


def test_the_one_source_is_identitys_policy_and_the_gate_reads_it():
    policy = (ROOT / "core/identity/services/admin-api/src/admin_api/app/notification_policy.py").read_text()
    assert '"rooms"' in policy and "ws:" in policy
    gate = (ROOT / "core/flows/src/flows_delivery/policy.py").read_text()
    assert 'f"ws:{workspace}"' in gate and "member_settings" not in gate
    client = (ROOT / "clients/terminal/src/minutes/desktop/home2/settingsApi.ts").read_text()
    assert "notify" not in client.split("export type WorkspaceSettings", 1)[1].split("};", 1)[0]
