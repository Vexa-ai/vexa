"""int-s1 seams (0.13.4 S1 integration): the workspace principal is W's writer and nothing else;
a room reads its OWN history; room-call bots are keyed to the payer.

Offline: the in-process world of `test_r1_rooms` (no containers)."""
from __future__ import annotations

import pytest

from control_plane import room_principal as rp
from tests.test_r1_rooms import _h, _room, world  # noqa: F401  (fixture)


def _principal(ws="team", unit="", pid="1042", speaker="bob"):
    """What the gateway stamps from a workspace principal's signed delegation (D2)."""
    return {"X-User-Id": pid, "x-user-regime": "human", "x-user-delegation-shared": "1",
            "x-user-delegation-workspaces": ws, "x-user-delegation-unit": unit,
            "x-user-principal-kind": "workspace", "x-user-principal-workspace": ws,
            "x-user-delegation-on-behalf-of": speaker, "x-user-bills-to": "alice"}


def test_the_principal_writes_and_reads_its_own_workspace(world):
    client, store, calls, spawned, root, _d = world
    hdr = _principal(unit="agent-ws-team-room-s1")
    r = client.put("/api/workspace/file", headers=hdr,
                   json={"slug": "team", "path": "notes/room.md", "content": "# from the room\n"})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert (root / "team" / "notes" / "room.md").read_text().startswith("# from the room")
    r = client.get("/api/workspace/tree", headers=hdr, params={"slug": "team"})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    r = client.get("/api/workspace/file", headers=hdr, params={"slug": "team", "path": "notes/room.md"})
    assert r.status_code == 200 and "from the room" in r.text


def test_the_principal_reaches_no_desk_and_no_other_workspace(world):
    client, store, calls, spawned, root, _d = world
    hdr = _principal()
    # no slug = a desk: the principal has none
    r = client.get("/api/workspace/tree", headers=hdr)
    assert r.status_code == 403, r.text[:200]
    for slug in ("alice", "bob"):
        r = client.put("/api/workspace/file", headers=hdr,
                       json={"slug": slug, "path": "x.md", "content": "x"})
        assert r.status_code == 403, f"{slug}: {r.status_code}"


def test_a_principal_for_another_workspace_cannot_write_this_one(world):
    client, store, calls, spawned, root, _d = world
    hdr = _principal(ws="other")
    hdr["x-user-delegation-workspaces"] = "team"          # a forged ceiling still names W != team
    r = client.put("/api/workspace/file", headers=hdr,
                   json={"slug": "team", "path": "x.md", "content": "x"})
    assert r.status_code == 403


def test_the_principal_records_an_entity_in_its_workspace(world):
    client, store, calls, spawned, root, _d = world
    r = client.post("/api/workspace/entity", headers=_principal(unit="agent-ws-team-room-s1"),
                    json={"slug": "team", "kind": "company", "name": "Globex", "facts": ["Met on Friday."], "source": "the room call"})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"


def test_a_principal_write_keeps_the_privacy_hold(world):
    """S1-D still applies: a principal's write whose mark cannot be checked (no unit) is held."""
    client, store, calls, spawned, root, _d = world
    r = client.put("/api/workspace/file", headers=_principal(unit=""),
                   json={"slug": "team", "path": "held.md", "content": "x"})
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "private_sources_to_shared"
    assert not (root / "team" / "held.md").exists()
