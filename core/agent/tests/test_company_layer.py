"""The company layer on agent-api's side — OPTIONAL since founder ruling 2026-10-08.

*"let's remove global setup at all so that there is no need to setup global at all - let it be
empty with no data - it's fine."* This reverses the 2026-09-02 ruling that a Vexa whose admin had
not written the company layer served nobody. These tests hold the new shape:

  * no request is refused for want of a company layer — a non-admin on an instance whose `_global`
    is empty (or missing entirely) is served like anyone else;
  * `_global` itself is an empty directory agent-api creates in its own store, idempotently, when no
    out-of-store path is configured — so a fresh stack dispatches with no configuration;
  * the optional acceptance verb still verifies and commits what an admin chose to write.
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


def test_a_non_admin_is_served_while_the_company_layer_is_unwritten(client, monkeypatch):
    """The removed middleware answered 403 here. Nothing asks admin-api about `_global` any more."""
    monkeypatch.setattr(global_layer, "is_admin", lambda settings, subject: False)
    r = client.get("/api/workspace/tree", headers={"X-User-Id": "42"})
    assert r.status_code != 403
    assert "being set up" not in r.text


def test_the_gate_vocabulary_is_gone():
    """No reader of the old gate value survives to be re-wired by accident."""
    for name in ("instance_state", "mark_ready", "GATE_SENTENCE", "COMPLETED", "MISSING"):
        assert not hasattr(global_layer, name), name


def test_the_state_route_is_gone(client):
    """`/api/global/state` existed for the setup wizard's poll and the gate's refusal copy."""
    assert client.get("/api/global/state", headers={"X-User-Id": "42"}).status_code == 404


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
    """Never auto-create a CONFIGURED path other than the in-store `_global`, and never bind a file.
    Docker auto-creating the missing directory is what made the phantom store; agent-api refusing
    first is what stops it reaching docker at all."""
    import pytest as _pytest
    from control_plane.system_mounts import global_mount
    with _pytest.raises(RuntimeError, match="does not exist"):
        global_mount(_settings(tmp_path, tmp_path / "nope"), str(tmp_path))
    f = tmp_path / "afile"
    f.write_text("x")
    with _pytest.raises(RuntimeError, match="not a directory"):
        global_mount(_settings(tmp_path, f), str(tmp_path))


# ── `_global` EXISTS EMPTY, WITH NO CONFIGURATION (founder ruling 2026-10-08) ──────────────────

def test_unconfigured_global_is_the_in_store_directory_created_empty(tmp_path):
    """A fresh stack sets nothing and still dispatches: `_global` is agent-api's own, in its store,
    created empty, and rides the store bind like any other workspace."""
    from control_plane.system_mounts import GLOBAL_SLUG, global_mount
    m = global_mount(_settings(tmp_path, ""), str(tmp_path))
    assert (tmp_path / GLOBAL_SLUG).is_dir()
    assert list((tmp_path / GLOBAL_SLUG).iterdir()) == []      # empty is fine
    assert "source" not in m and m["path"] == f"{tmp_path}/{GLOBAL_SLUG}" and m["write"] is False
    # idempotent — a second dispatch neither fails nor touches what is there
    (tmp_path / GLOBAL_SLUG / "README.md").write_text("# Acme\n\nAcme sells widgets.\n")
    global_mount(_settings(tmp_path, ""), str(tmp_path))
    assert (tmp_path / GLOBAL_SLUG / "README.md").read_text().startswith("# Acme")


def test_a_configured_in_store_global_that_is_missing_is_created(tmp_path):
    """`VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH=<store>/_global` names agent-api's own store, so a missing
    directory there is created rather than refused."""
    from control_plane.system_mounts import GLOBAL_SLUG, global_mount
    m = global_mount(_settings(tmp_path, tmp_path / GLOBAL_SLUG), str(tmp_path))
    assert (tmp_path / GLOBAL_SLUG).is_dir() and "source" not in m


def test_an_empty_global_FILE_left_by_a_bind_mountpoint_is_replaced(tmp_path):
    """The compose file used to bind `/dev/null` over `/workspaces/_global`; docker created that
    mountpoint inside the named volume as an EMPTY FILE, which outlived the bind and made the
    directory impossible to create. An empty regular file there is that residue and nothing else."""
    from control_plane.system_mounts import GLOBAL_SLUG, ensure_global_dir
    (tmp_path / GLOBAL_SLUG).write_text("")
    assert ensure_global_dir(tmp_path).is_dir()


def test_a_non_empty_global_FILE_is_somebodys_data_and_is_left_alone(tmp_path):
    import pytest as _pytest
    from control_plane.system_mounts import GLOBAL_SLUG, ensure_global_dir
    (tmp_path / GLOBAL_SLUG).write_text("not ours")
    with _pytest.raises(FileExistsError):
        ensure_global_dir(tmp_path)
    assert (tmp_path / GLOBAL_SLUG).read_text() == "not ours"


def test_an_unwritten_placeholder_never_reaches_a_turn_as_organisation_context(tmp_path):
    """With `_global` allowed to stay unwritten, the seed's "# Company" README would otherwise be
    loaded into every turn as the employer's name. Empty or placeholder-only reads as no context."""
    from worker.engine import global_context_preamble
    mounts = [{"slug": "_global", "path": str(tmp_path), "role": "global", "write": False}]
    (tmp_path / "README.md").write_text("# Company\n\n<!-- vexa:unwritten — fill me -->\n")
    assert "# Company" not in global_context_preamble(mounts)
    (tmp_path / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    assert "Acme GmbH sells widgets." in global_context_preamble(mounts)
