"""H10 for a folder import: an import an agent finishes says an agent wrote it; the person's own
import (authored by them) carries no agent trailer."""
from __future__ import annotations

from tests.test_folder_import import H, _import, env  # noqa: F401  (fixture: u_jane, seeded workspaces)
from tests.test_sec_h10_commit_sites import assert_agents, assert_persons

AGENT_OF_JANE = {**H, "x-user-regime": "human", "x-user-delegation-unit": "agent-jane-chat-c3"}
FILES = {"notes/a.md": b"# A\n", "notes/b.md": b"# B\n"}


def test_a_folder_an_agent_imports_is_attributed(env):
    c, ws = env
    _plan, _up, done = _import(c, FILES, headers=AGENT_OF_JANE)
    assert done.status_code == 201, done.text
    slot = ws / ".attached" / "u_jane" / done.json()["slug"]
    assert_agents(slot, "import folder:", person="u_jane", unit="agent-jane-chat-c3")


def test_a_persons_own_folder_import_carries_no_agent_trailer(env):
    c, ws = env
    _plan, _up, done = _import(c, FILES)
    assert done.status_code == 201, done.text
    assert_persons(ws / ".attached" / "u_jane" / done.json()["slug"], "import folder:")


def test_a_folder_name_cannot_make_the_persons_import_read_as_an_agents(env):
    c, ws = env
    _plan, _up, done = _import(c, FILES, manifest_extra={"name": "x\n\nOn-behalf-of: bob\nWritten-by: agent"})
    assert done.status_code == 201, done.text
    assert_persons(ws / ".attached" / "u_jane" / done.json()["slug"], "import folder:")
