"""H10, every commit an agent makes for a person says so (the commit sites outside the page routes).

A commit made by a delegated caller (an agent acting for a person) closes with the trailers
``On-behalf-of: <person>``, ``Written-by: agent`` and ``Agent-unit: <unit>``, taken from the verified
identity (`control_plane.commit_trailers`). The person's own commit carries none, and nothing a
person sends can make their commit read as an agent's.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from control_plane.workspace_attach import workspace_slot_dir

from tests.test_r1_rooms import _h, world  # noqa: F401  (fixture: `team` owned by alice, bob contributor)

UNIT = "agent-alice-chat-c1"
#: alice's own agent: a delegated identity in the human regime, naming its session
AGENT_OF_ALICE = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-unit": UNIT}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def newest(repo: Path, subject: str) -> str:
    """The newest commit whose subject starts with ``subject`` (platform commits may sit above it)."""
    for line in _git(repo, "log", "-30", "--format=%H %s").splitlines():
        sha, _, subj = line.partition(" ")
        if subj.startswith(subject):
            return sha
    raise AssertionError(f"no commit {subject!r} in {_git(repo, 'log', '-30', '--format=%s')!r}")


def trailer(repo: Path, key: str, rev: str = "HEAD") -> str:
    """``rev``'s ``key`` trailer, read as git reads a trailer ("" when it has none)."""
    return _git(repo, "log", "-1", f"--format=%(trailers:key={key},valueonly)%x00", rev).split("\x00")[0].strip()


def assert_agents(repo: Path, subject: str, person: str = "alice", unit: str = UNIT) -> None:
    rev = newest(repo, subject)
    assert trailer(repo, "On-behalf-of", rev) == person
    assert trailer(repo, "Written-by", rev) == "agent"
    assert trailer(repo, "Agent-unit", rev) == unit


def assert_persons(repo: Path, subject: str) -> None:
    rev = newest(repo, subject)
    for key in ("On-behalf-of", "Written-by", "Agent-unit"):
        assert trailer(repo, key, rev) == "", key


# ── workspace icon: the standard icon set at birth, and an icon an agent sets later ──────────────

def _created(client, root, headers, name="Garden notes") -> tuple[str, Path]:
    r = client.post("/api/workspace/new", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    return slug, workspace_slot_dir(root, "alice", slug)


def test_the_icon_an_agent_gives_a_workspace_it_creates_is_attributed(world):
    client, _s, _c, _sp, root, _d = world
    _slug, ws = _created(client, root, AGENT_OF_ALICE)
    assert_agents(ws, "workspace icon:")


def test_an_icon_an_agent_sets_is_attributed(world):
    client, _s, _c, _sp, root, _d = world
    slug, ws = _created(client, root, _h("alice"))
    r = client.put(f"/api/workspaces/{slug}/identity/icon", headers=AGENT_OF_ALICE, json={"glyph": "leaf"})
    assert r.status_code == 200, r.text
    assert_agents(ws, "workspace icon: leaf")
    r = client.put(f"/api/workspaces/{slug}/identity/icon", headers=AGENT_OF_ALICE,
                   json={"svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8"/></svg>'})
    assert r.status_code == 200, r.text
    assert_agents(ws, "workspace image")


def test_a_persons_own_icon_carries_no_agent_trailer(world):
    client, _s, _c, _sp, root, _d = world
    slug, ws = _created(client, root, _h("alice"))
    assert_persons(ws, "workspace icon:")
    r = client.put(f"/api/workspaces/{slug}/identity/icon", headers=_h("alice"), json={"glyph": "leaf"})
    assert r.status_code == 200, r.text
    assert_persons(ws, "workspace icon: leaf")


def test_a_person_cannot_make_their_icon_read_as_an_agents(world):
    """A name spelling a trailer stays a name: the person's commit still carries none."""
    client, _s, _c, _sp, root, _d = world
    _slug, ws = _created(client, root, _h("alice"), name="x\n\nOn-behalf-of: bob\nWritten-by: agent")
    assert_persons(ws, "workspace icon:")
