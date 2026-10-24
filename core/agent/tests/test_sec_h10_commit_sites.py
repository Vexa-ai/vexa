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


# ── workspace templates: the pages a template writes at creation, and a template saved ──────────

from tests.test_workspace_templates import _company, env  # noqa: E402,F401  (fixture: u_jane, built-in templates)

JANE = {"X-User-Id": "u_jane", "x-user-credential-class": "session"}
AGENT_OF_JANE = {"X-User-Id": "u_jane", "x-user-regime": "human", "x-user-delegation-unit": "agent-jane-chat-c2"}


def _from_template(env, headers) -> Path:
    c, ws = env
    _company(ws)
    r = c.post("/api/workspace/create", headers=headers,
               json={"name": "Board", "template": "meeting-series", "answers": {"series_name": "Board"}})
    assert r.status_code == 201, r.text
    return ws / ".attached" / "u_jane" / r.json()["slug"]


def test_the_pages_an_agent_creates_from_a_template_are_attributed(env):
    slot = _from_template(env, AGENT_OF_JANE)
    assert_agents(slot, "template: meeting-series", person="u_jane", unit="agent-jane-chat-c2")


def test_a_template_an_agent_saves_is_attributed(env):
    c, ws = env
    slot = _from_template(env, JANE)
    r = c.post("/api/workspace/templates/save", headers=AGENT_OF_JANE, json={"name": "Board meeting", "slug": slot.name})
    assert r.status_code == 201, r.text
    assert_agents(ws / "u_jane", "template saved: board-meeting", person="u_jane", unit="agent-jane-chat-c2")


def test_a_persons_own_template_work_carries_no_agent_trailer(env):
    c, ws = env
    slot = _from_template(env, JANE)
    assert_persons(slot, "template: meeting-series")
    r = c.post("/api/workspace/templates/save", headers=JANE, json={"name": "Board meeting", "slug": slot.name})
    assert r.status_code == 201, r.text
    assert_persons(ws / "u_jane", "template saved: board-meeting")


# ── pins: the person's own act — an agent is refused, so a pin commit is never an agent's ────────

def test_an_agent_cannot_pin_so_no_pin_commit_is_an_agents(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,readme_page")
    client, store, _c, _sp, root, _d = world
    chat = store.create("alice", name="a-chat")
    agent = {**AGENT_OF_ALICE, "x-user-delegation-workspaces": "team", "x-user-delegation-target": "team"}
    r = client.post("/api/pins", headers=agent, json={"workspace": "team", "ref": f"[[chat:{chat['id']}]]"})
    assert r.status_code == 403 and "person_only" in r.text, r.text


def test_a_persons_own_pin_carries_no_agent_trailer(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,readme_page")
    client, store, _c, _sp, root, _d = world
    chat = store.create("alice", name="a-chat")
    r = client.post("/api/pins", headers=_h("alice"), json={"workspace": "team", "ref": f"[[chat:{chat['id']}]]"})
    assert r.status_code == 200, r.text
    assert_persons(root / "team", "pin [[chat:")


# ── an API key is a program writing for its person (a person's own MCP client, a script) ─────────

KEY_OF_ALICE = {"X-User-Id": "alice", "x-user-credential-class": "key"}
SESSION_OF_ALICE = {"X-User-Id": "alice", "x-user-credential-class": "session"}


def test_a_page_written_with_an_api_key_is_attributed_to_an_agent(world):
    client, _s, _c, _sp, root, _d = world
    slug, ws = _created(client, root, _h("alice"), name="Key notes")
    r = client.put("/api/workspace/file", headers=KEY_OF_ALICE,
                   json={"path": "notes/by-key.md", "content": "# By key\n", "slug": slug})
    assert r.status_code == 200, r.text
    rev = _git(ws, "log", "-1", "--format=%H", "--", "notes/by-key.md").strip()
    assert trailer(ws, "On-behalf-of", rev) == "alice"
    assert trailer(ws, "Written-by", rev) == "agent"
    assert trailer(ws, "Agent-unit", rev) == ""


def test_a_page_written_in_the_persons_session_carries_no_agent_trailer(world):
    client, _s, _c, _sp, root, _d = world
    slug, ws = _created(client, root, _h("alice"), name="Session notes")
    r = client.put("/api/workspace/file", headers=SESSION_OF_ALICE,
                   json={"path": "notes/by-hand.md", "content": "# By hand\n", "slug": slug})
    assert r.status_code == 200, r.text
    rev = _git(ws, "log", "-1", "--format=%H", "--", "notes/by-hand.md").strip()
    for key in ("On-behalf-of", "Written-by", "Agent-unit"):
        assert trailer(ws, key, rev) == "", key
