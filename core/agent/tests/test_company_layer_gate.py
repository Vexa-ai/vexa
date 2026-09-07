"""The company-layer gate, on agent-api's side.

Founder ruling, 2026-09-02: a Vexa whose admin has not written the thin company layer serves
nobody. The first build of that checked at SIGN-IN, and a session minted before the gate existed
walked straight past it — observed live the same day: an old cookie got the whole terminal, a chat,
and an agent turn on an instance that could not say which company it worked for.

A door check is not a gate. These tests hold the shape that replaced it:

  * the refusal happens per REQUEST, so an already-issued credential is not a way through;
  * `/api/global/*` is deliberately open, because a gate that blocks the only way to open it is a
    deadlock rather than a gate;
  * a request with no subject is not judged (the internal tier is gated on the secret instead);
  * a VIRGIN instance refuses nobody — the next sign-in is the claim;
  * and a DEGRADED read is *unknown*, never *missing*. `instance_state` answers missing when it
    cannot reach admin-api, which is right for anything that SENDS and wrong here: the consequence
    would be locking every user out of a working instance because one probe timed out.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import global_layer
from control_plane.api import create_app
from shared.config import load_settings
from tests.test_api import _FakeIdentity, _FakeRuntime
from control_plane.dispatch import Dispatcher


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity())))


COMPLETED = {"admin_exists": True, "global_setup": "completed", "company": "Acme GmbH"}
GATED = {"admin_exists": True, "global_setup": "missing", "company": None}
VIRGIN = {"admin_exists": False, "global_setup": "missing", "company": None}
DEGRADED = {"admin_exists": True, "global_setup": "missing", "company": None, "degraded": True}


@pytest.fixture()
def gate(monkeypatch):
    """Drive the gate directly: these tests are about the DECISION, not about HTTP to admin-api."""
    state = {"value": COMPLETED, "admins": set()}
    monkeypatch.setattr(global_layer, "instance_state", lambda settings, force=False: state["value"])
    monkeypatch.setattr(global_layer, "is_admin", lambda settings, subject: str(subject) in state["admins"])
    return state


def test_a_non_admin_is_refused_on_every_api_route_while_the_layer_is_missing(client, gate):
    """The point of the middleware: an EXISTING credential is not a way past the gate."""
    gate["value"] = GATED
    gate["admins"] = {"7"}
    r = client.get("/api/workspace/tree", headers={"X-User-Id": "42"})
    assert r.status_code == 403
    assert r.json()["detail"] == "This Vexa is being set up by its administrator."
    assert r.json()["global_setup"] == "missing"


def test_the_admin_is_not_refused(client, gate):
    gate["value"] = GATED
    gate["admins"] = {"42"}
    assert client.get("/api/workspace/tree", headers={"X-User-Id": "42"}).status_code != 403


def test_the_gate_route_stays_open_or_the_gate_is_a_deadlock(client, gate):
    """`/api/global/*` is the state the wizard polls and the verb that lifts the gate. Blocking it
    would leave the only person who can open the gate looking at a screen that never changes."""
    gate["value"] = GATED
    gate["admins"] = {"7"}
    assert client.get("/api/global/state", headers={"X-User-Id": "42"}).status_code != 403


def test_a_virgin_instance_refuses_nobody(client, gate):
    """No admin yet means the next sign-in IS the claim. Refusing here makes a fresh install
    unclaimable — a deadlock, not a gate."""
    gate["value"] = VIRGIN
    gate["admins"] = set()
    assert client.get("/api/workspace/tree", headers={"X-User-Id": "42"}).status_code != 403


def test_a_degraded_read_is_unknown_not_missing(client, gate):
    """`instance_state` fails CLOSED for callers that send. This one must not: an unreachable
    admin-api would otherwise lock every user out of a working instance, and a deployment with no
    admin-api configured at all would lock them out permanently."""
    gate["value"] = DEGRADED
    gate["admins"] = set()
    assert client.get("/api/workspace/tree", headers={"X-User-Id": "42"}).status_code != 403


def test_a_completed_layer_gates_nothing(client, gate):
    gate["value"] = COMPLETED
    gate["admins"] = set()
    assert client.get("/api/workspace/tree", headers={"X-User-Id": "42"}).status_code != 403


def test_the_five_files_and_the_readme_rule(tmp_path):
    """What `state()` calls ready. The README rule is the founder's: *"the first chat needs to
    present itself knowing about itself — which company it's from and what's their service"* — an
    agent can only say which company it belongs to if a human wrote the name down."""
    for name in global_layer.LAYER_FILES:
        (tmp_path / name).write_text("# x\n\nsomething.\n")
    (tmp_path / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    st = global_layer.state(tmp_path)
    assert st["ready"] and st["company"] == "Acme GmbH"
    assert st["service"] == "Acme GmbH sells widgets."
    assert st["missing_files"] == [] and st["reasons"] == []

    # a placeholder is not a company name, and the words the setup conversation itself uses while
    # it is still asking are exactly the ones that must not lift the gate
    for placeholder in ("# Company", "# Your Company", "# TBD", "# _global"):
        (tmp_path / "README.md").write_text(placeholder + "\n\nsomething.\n")
        assert global_layer.state(tmp_path)["ready"] is False

    # a heading with no sentence under it is not enough either
    (tmp_path / "README.md").write_text("# Acme GmbH\n\n## Principles\n\nnope.\n")
    st = global_layer.state(tmp_path)
    assert st["ready"] is False and st["service"] is None

    # an empty file counts as missing — a touched file is not a written one
    (tmp_path / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    (tmp_path / "MISSING.md").write_text("   \n")
    assert "MISSING.md" in global_layer.state(tmp_path)["missing_files"]


def test_the_repo_exists_before_its_first_writer(tmp_path):
    """`_global` shipped as a bare directory that every worker read on every turn, with nothing
    recording who changed it. One admin edit changes how every agent in the deployment behaves."""
    assert global_layer.ensure_repo(tmp_path) is True
    assert (tmp_path / ".git").is_dir()
    assert global_layer.ensure_repo(tmp_path) is False   # idempotent

    (tmp_path / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    sha = global_layer.commit(tmp_path, author_email="admin@acme.test",
                              author_name="the admin", message="company layer: Acme GmbH")
    assert sha
    # the AUTHOR is the human. The agent typed it; the admin accepted it; the reviewable record has
    # to name the person who is answerable for what every agent in the company will now carry.
    import subprocess
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "-1", "--format=%an <%ae>"],
                         capture_output=True, text=True).stdout.strip()
    assert log == "the admin <admin@acme.test>"
    # a re-run with nothing to commit is not an error — the acceptance verb is idempotent
    assert global_layer.commit(tmp_path, author_email="admin@acme.test",
                               author_name="the admin", message="again") == sha


# ── ONE STORE (the 2026-09-02 phantom-`_global` blocker) ────────────────────────────────────────

def _settings(tmp_path, global_path):
    from types import SimpleNamespace
    return SimpleNamespace(workspaces_dir=str(tmp_path),
                           global_system_workspace_path=str(global_path),
                           global_system_workspace_ref="", global_admin_subjects="")


def test_an_in_store_global_emits_NO_source_so_it_rides_the_store_bind(tmp_path):
    """The bug, as a test.

    `source` is resolved by the DOCKER DAEMON ON THE HOST; every other value on the mount is
    resolved by agent-api INSIDE ITS CONTAINER. When `_global` lives in the workspace store those
    are two different filesystems wearing one string. Emitting `source` picked the host one, docker
    auto-created an empty directory there, and the founder's setup chat wrote the company layer into
    a store no reader of `_global` has ever looked at — successfully, which is why nothing reported
    it. Emitting no source makes the runtime bind it out of the store volume by subpath, the same
    way `/workspaces/57` and `_system` are already bound: one store, resolved once, by the component
    that owns it."""
    from control_plane.system_mounts import GLOBAL_SLUG, global_mount
    (tmp_path / GLOBAL_SLUG).mkdir()
    m = global_mount(_settings(tmp_path, tmp_path / GLOBAL_SLUG), str(tmp_path))
    assert "source" not in m
    assert m["path"] == f"{tmp_path}/{GLOBAL_SLUG}"
    assert m["write"] is False


def test_an_out_of_store_global_keeps_its_own_source(tmp_path):
    """A `_global` genuinely outside the store is a real deployment shape and is NOT the broken one:
    an out-of-store path means the same thing to agent-api and to the daemon."""
    from control_plane.system_mounts import GLOBAL_SLUG, global_mount
    outside = tmp_path.parent / (tmp_path.name + "-elsewhere")
    outside.mkdir(exist_ok=True)
    m = global_mount(_settings(tmp_path, outside), str(tmp_path))
    assert m["source"] == str(outside)
    assert m["path"] == f"{tmp_path}/{GLOBAL_SLUG}"


def test_an_in_store_global_under_another_name_is_refused(tmp_path):
    """The runtime derives the store subpath from `path`, which is always `<root>/_global`. Honouring
    an in-store directory under a different name would mount something other than what the operator
    configured — silently, in the same class as the bug above."""
    import pytest as _pytest
    from control_plane.system_mounts import global_mount
    (tmp_path / "company").mkdir()
    with _pytest.raises(RuntimeError, match="must BE"):
        global_mount(_settings(tmp_path, tmp_path / "company"), str(tmp_path))


def test_a_missing_or_non_directory_global_fails_before_spawn(tmp_path):
    """Never auto-create, and never bind a file. Docker auto-creating the missing directory is what
    made the phantom store; agent-api refusing first is what stops it reaching docker at all."""
    import pytest as _pytest
    from control_plane.system_mounts import global_mount
    with _pytest.raises(RuntimeError, match="does not exist"):
        global_mount(_settings(tmp_path, tmp_path / "nope"), str(tmp_path))
    f = tmp_path / "afile"
    f.write_text("x")
    with _pytest.raises(RuntimeError, match="not a directory"):
        global_mount(_settings(tmp_path, f), str(tmp_path))
