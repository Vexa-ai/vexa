"""A CHANGE TO HOW CREDENTIALS ARE USED IS ANNOUNCED, AND NEVER MADE WITHOUT A PERSON.

`_global/POLICIES.md` carries the connection keys (`connection_*`, `calendar_*`, `draft_*`) that
decide what an agent may do with a person's mailbox and calendar. The file is written by an org
admin, and an admin's agent writes it too (the policies wizard). So a write that changes one of
those keys:

  W1  is refused to a worker dispatched without a person in the loop (a background routine never
      decides how credentials are used), with nothing written;
  W2  from anyone else, lands with a commit in `_global` that names the changed keys and the writer,
      and one structured `connection_policy_changed` log line — the audit notice;
  W3  a write that changes no connection key (the meeting keys, the prose) is untouched by this.
Removing or moving the file away is a change too: it returns every key to its default.
"""
from __future__ import annotations

import json
import logging
import subprocess

import pytest

from tests.test_global_root import ADMIN, _client, _repo, _settings

ADMIN_H = {"X-User-Id": ADMIN}
ROUTINE = {"X-User-Id": ADMIN, "X-User-Regime": "autonomous", "X-User-Delegation-Workspaces": "*"}
WATCHED = {"X-User-Id": ADMIN, "X-User-Regime": "human", "X-User-Delegation-Workspaces": "*"}

#: `_global` is written by the admin account alone; ADMIN is named as it (tests/admin_accounts.py).
pytestmark = pytest.mark.usefixtures("fake_admin_accounts")


def _policies(**keys) -> str:
    lines = "".join(f"{k}: {v}\n" for k, v in keys.items())
    return f"---\nkind: policies\n{lines}---\n\n# Policies\n"


def _world(tmp_path):
    store = tmp_path / "workspaces"
    store.mkdir()
    glob = _repo(store / "_global")
    (glob / "POLICIES.md").write_text(_policies(connection_actions_unattended="none", attendee_domains="any"))
    subprocess.run(["git", "-C", str(glob), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(glob), "commit", "-qm", "seed"], check=True)
    return _client(store, _settings(store, "")), glob


def _put(c, h, content):
    return c.put("/api/workspace/file", headers=h, json={"path": "POLICIES.md", "content": content, "slug": "_global"})


def _last_commit(glob) -> str:
    return subprocess.run(["git", "-C", str(glob), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout


def test_a_routine_cannot_change_a_connection_key(tmp_path):
    c, glob = _world(tmp_path)
    before = (glob / "POLICIES.md").read_text()
    r = _put(c, ROUTINE, _policies(connection_actions_unattended="gmail.search", attendee_domains="any"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["reason"] == "connection_policy_needs_person"
    assert (glob / "POLICIES.md").read_text() == before
    r = c.post("/api/workspace/remove", headers=ROUTINE, json={"path": "POLICIES.md", "slug": "_global"})
    assert r.status_code == 403 and (glob / "POLICIES.md").read_text() == before


def test_a_change_is_announced_in_the_history_and_the_log(tmp_path, caplog):
    c, glob = _world(tmp_path)
    with caplog.at_level(logging.WARNING):
        r = _put(c, WATCHED, _policies(connection_actions_unattended="gmail.search", attendee_domains="any",
                                       draft_attachments="on"))
    assert r.status_code == 200, r.text
    assert r.json()["connection_policy_changed"] == ["connection_actions_unattended", "draft_attachments"]
    msg = _last_commit(glob)
    assert "connection policy changed: connection_actions_unattended, draft_attachments" in msg
    assert f"by {ADMIN} (an agent, person in the loop)" in msg
    lines = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")
             and "connection_policy_changed" in r.message]
    assert lines and lines[-1]["keys"] == ["connection_actions_unattended", "draft_attachments"]
    assert lines[-1]["subject"] == ADMIN and lines[-1]["delegated"] is True


def test_a_person_s_own_edit_is_announced_too(tmp_path):
    c, glob = _world(tmp_path)
    assert _put(c, ADMIN_H, _policies(connection_actions_unattended="none", attendee_domains="any",
                                      calendar_ids="any")).status_code == 200
    assert f"by {ADMIN} (the person)" in _last_commit(glob)


def test_a_write_that_changes_no_connection_key_is_untouched(tmp_path):
    c, glob = _world(tmp_path)
    r = _put(c, ROUTINE, _policies(connection_actions_unattended="none", attendee_domains="example.test"))
    assert r.status_code == 200, r.text
    assert "connection_policy_changed" not in r.json()
    assert "connection policy" not in _last_commit(glob)
