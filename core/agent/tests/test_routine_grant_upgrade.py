"""The subject-ticket upgrade: routines already running get their grant only on agent-api's records.

The gateway door runs a routine's turns and bots only on a grant agent-api signed. Routines turned on
before grants existed hold none. A one-time pass (`adopt_grants`) re-issues a grant for each running
routine THIS service recorded a person turning on (`record_arm`, or a converted file a person stood
behind); every other running routine is paused with a reason and a note in its chat, so the person
turns it on again with one press. What flows lists decides which routines are looked at, never which
get a grant.
"""
from __future__ import annotations

import json

import pytest

from control_plane import routine_flows as rf
from control_plane import subject_ticket
from tests import _ticket_keys


class Flows:
    """flows-api as agent-api sees it; `listed` is whatever flows chooses to report."""

    def __init__(self, listed: dict):
        self.listed = listed
        self.grants: list = []
        self.statuses: list = []

    def routines(self, subject):
        return [dict(f) for f in self.listed.get(subject, [])]

    def running_owners(self):
        return {s for s, fs in self.listed.items() if any(f.get("status") == "active" for f in fs)}

    def attach_grant(self, subject, name, version, grant):
        self.grants.append((subject, name, version, grant))
        return {"granted": True}

    def set_status(self, subject, name, version, action, *, grant=""):
        self.statuses.append((subject, name, version, action))
        return {"status": "retired"}


def _active(name, version=1):
    return {"name": name, "version": version, "status": "active", "scope": "user:7"}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "workspaces"
    for person in ("7", "8"):
        (r / person).mkdir(parents=True)
    return r


@pytest.fixture
def key(tmp_path, monkeypatch):
    return _ticket_keys.install(tmp_path, monkeypatch)


@pytest.fixture
def notes(monkeypatch):
    said = []
    monkeypatch.setattr(rf, "post_to_routine_chat",
                        lambda store, subject, name, text, **kw: said.append((subject, name, text)))
    return said


def _grant_for(flows, name):
    return [g for (_s, n, _v, g) in flows.grants if n == name]


def test_a_routine_agent_api_recorded_a_session_turning_on_gets_its_grant(root, key, notes):
    rf.record_arm(root, "7", "daily")
    flows = Flows({"7": [_active("daily", 3)]})
    counts = rf.adopt_grants(client=flows, workspaces_dir=root)
    (grant,) = _grant_for(flows, "daily")
    claims = subject_ticket.read(grant, _ticket_keys.checks(key))
    assert claims["kind"] == "grant" and claims["sub"] == "7" and claims["evt"] == "user:7/daily"
    assert flows.grants[0][2] == 3                          # on the version flows runs
    assert flows.statuses == [] and notes == []             # nothing paused, nothing said
    assert counts.granted == 1 and counts.rearm == 0


def test_a_file_the_migration_converted_on_is_a_recorded_arm(root, key, notes):
    rf._write_marker(root, "7", {"files": {"brief": {"outcome": "converted", "flow": "brief"},
                                           "idle": {"outcome": "paused", "flow": "idle"}}})
    flows = Flows({"7": [_active("brief"), _active("idle")]})
    rf.adopt_grants(client=flows, workspaces_dir=root)
    assert _grant_for(flows, "brief") and not _grant_for(flows, "idle")
    assert ("7", "idle", 1, "pause") in flows.statuses


def test_an_unrecorded_running_routine_gets_no_grant_and_a_one_press_card(root, key, notes):
    flows = Flows({"7": [_active("old")]})
    counts = rf.adopt_grants(client=flows, workspaces_dir=root)
    assert flows.grants == []
    assert flows.statuses == [("7", "old", 1, "pause")]
    state = rf.load_flow_state(root, "7")["old"]
    assert state["paused_reason"] == rf.REARM_REASON            # the routine bar's Turn on card
    assert notes and notes[0][:2] == ("7", "old") and "turn it on again" in notes[0][2]
    card = rf.card_from_flow("7", {**_active("old"), "status": "retired"},
                             state=rf.load_flow_state(root, "7"))
    assert card["paused_reason"] == rf.REARM_REASON and card["enabled"] is False
    assert counts.rearm == 1 and counts.granted == 0


def test_what_flows_lists_never_earns_a_grant(root, key, notes):
    """flows tampers with its own active list: it adds routines nobody turned on (its own, and the
    name of another person's recorded routine), claims a routine the person switched off is
    running, and hides nothing. Grants go exactly where agent-api's records point."""
    rf.record_arm(root, "7", "daily")
    rf.record_arm(root, "8", "bobs")
    rf.record_arm(root, "7", "stopped")
    rf.set_paused_reason(root, "7", "stopped", "paused by you")
    flows = Flows({"7": [_active("daily"), _active("evil"), _active("bobs"), _active("stopped")]})
    rf.adopt_grants(client=flows, workspaces_dir=root)
    granted = {(s, n) for (s, n, _v, _g) in flows.grants}
    assert granted == {("7", "daily")}
    for (_s, _n, _v, g) in flows.grants:
        assert subject_ticket.read(g, _ticket_keys.checks(key))["evt"] == "user:7/daily"
    paused = {(s, n) for (s, n, _v, a) in flows.statuses if a == "pause"}
    assert paused == {("7", "evil"), ("7", "bobs"), ("7", "stopped")}


def test_a_person_with_no_desk_and_a_running_routine_is_paused_and_granted_nothing(root, key,
                                                                                  notes):
    """Person 9 has no desk directory: only flows' list names them. Their running routine is
    paused with the same reason (and the note, where they have a chat); no grant. A name flows
    lists that is not a person id is ignored."""
    flows = Flows({"9": [_active("nightly", 2)], "../x": [_active("evil")]})
    counts = rf.adopt_grants(client=flows, workspaces_dir=root)
    assert flows.grants == []
    assert flows.statuses == [("9", "nightly", 2, "pause")]
    assert rf.load_flow_state(root, "9")["nightly"]["paused_reason"] == rf.REARM_REASON
    assert notes and notes[0][:2] == ("9", "nightly")
    assert counts.rearm == 1 and counts.failures == 0


def test_a_recorded_routine_flows_does_not_list_is_left_alone(root, key, notes):
    rf.record_arm(root, "7", "daily")
    flows = Flows({"7": []})
    rf.adopt_grants(client=flows, workspaces_dir=root)
    assert flows.grants == [] and flows.statuses == []


def test_the_pass_runs_once_after_the_migration_and_never_again(root, key, notes, monkeypatch):
    rf.record_arm(root, "7", "daily")
    flows = Flows({"7": [_active("daily"), _active("old")]})
    monkeypatch.setattr(rf, "configured", lambda: True)
    # before the migration is done, nothing is touched
    early = rf.start_grant_adoption(workspaces_dir=root, client_factory=lambda: flows,
                                    interval_sec=3600)
    early.thread.join(timeout=2)
    early.stop()
    assert flows.grants == [] and flows.statuses == []
    assert not (root / rf.GRANTS_DONE_MARKER).exists()
    (root / rf.DONE_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / rf.DONE_MARKER).write_text("{}")
    rf.start_grant_adoption(workspaces_dir=root, client_factory=lambda: flows, background=False)
    assert len(flows.grants) == 1 and len(flows.statuses) == 1
    assert json.loads((root / rf.GRANTS_DONE_MARKER).read_text())["last_pass"]["granted"] == 1
    assert rf.start_grant_adoption(workspaces_dir=root, client_factory=lambda: flows) is None
    rf.adopt_grants(client=flows, workspaces_dir=root)       # even run by hand: already sorted
    assert len(flows.grants) == 1 and len(flows.statuses) == 1


def test_without_its_key_agent_api_grants_nothing_and_tries_again(root, notes, monkeypatch,
                                                                   tmp_path):
    monkeypatch.setenv("VEXA_SUBJECT_TICKET_KEY_FILE", str(tmp_path / "absent.pem"))
    from control_plane import ticket_mint
    monkeypatch.setattr(ticket_mint, "_signers", {})
    rf.record_arm(root, "7", "daily")
    flows = Flows({"7": [_active("daily")]})
    counts = rf.adopt_grants(client=flows, workspaces_dir=root)
    assert flows.grants == [] and flows.statuses == [] and counts.failures == 1
