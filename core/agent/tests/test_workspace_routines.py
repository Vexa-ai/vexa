"""Workspace routine files — governed config compiled to durable schedule.v1 jobs."""
from __future__ import annotations

from pathlib import Path

from control_plane.workspace_routines import load_routine_file, reconcile_workspace_routines, set_routine_file_enabled
from tests.test_routines import _FakeScheduler


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_load_routine_file_valid_appends_body(tmp_path):
    path = tmp_path / "routines" / "morning.md"
    _write(
        path,
        "---\n"
        "enabled: true\n"
        "cron: '0 8 * * *'\n"
        "prompt: Summarize overnight updates.\n"
        "---\n"
        "Focus on urgent follow-ups.\n",
    )

    routine = load_routine_file(path)

    assert routine is not None
    assert routine.name == "morning"
    assert routine.enabled is True
    assert routine.cron == "0 8 * * *"
    assert routine.prompt == "Summarize overnight updates.\n\nFocus on urgent follow-ups."


def test_load_routine_file_invalid_and_disabled(tmp_path, caplog):
    invalid_cron = tmp_path / "routines" / "bad-cron.md"
    missing_prompt = tmp_path / "routines" / "missing-prompt.md"
    disabled = tmp_path / "routines" / "off.md"
    _write(invalid_cron, "---\nenabled: true\ncron: not-a-cron\nprompt: do it\n---\n")
    _write(missing_prompt, "---\nenabled: true\ncron: '0 8 * * *'\n---\nbody only\n")
    _write(disabled, "---\nenabled: false\n---\n")

    assert load_routine_file(invalid_cron) is None
    assert load_routine_file(missing_prompt) is None
    off = load_routine_file(disabled)

    assert off is not None
    assert off.enabled is False
    assert "invalid cron" in caplog.text
    assert "missing prompt" in caplog.text


def test_set_routine_file_enabled_preserves_frontmatter_and_body(tmp_path):
    workspaces = tmp_path / "workspaces"
    path = workspaces / "u_jane" / "routines" / "brief.md"
    _write(
        path,
        "---\n"
        "cron: '0 9 * * *'\n"
        "enabled: true # user-visible toggle\n"
        "prompt: Do the brief.\n"
        "---\n"
        "Keep this body exactly.\n",
    )

    set_routine_file_enabled("u_jane", "brief", enabled=False, workspaces_dir=workspaces)

    assert path.read_text() == (
        "---\n"
        "cron: '0 9 * * *'\n"
        "enabled: false # user-visible toggle\n"
        "prompt: Do the brief.\n"
        "---\n"
        "Keep this body exactly.\n"
    )
    routine = load_routine_file(path)
    assert routine is not None
    assert routine.enabled is False


def test_reconcile_upserts_and_is_idempotent(tmp_path):
    workspaces = tmp_path / "workspaces"
    routine_path = workspaces / "u_jane" / "routines" / "brief.md"
    _write(
        routine_path,
        "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do the brief.\n---\n",
    )
    scheduler = _FakeScheduler()

    first = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )
    second = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert first.scheduled == 1
    assert second.scheduled == 0
    assert second.kept == 1
    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job["metadata"]["source"] == "workspace-routine"
    assert job["metadata"]["owner"] == "u_jane"
    assert job["metadata"]["name"] == "brief"
    assert job["request"]["body"]["start"]["entrypoint"]["inline"] == "Do the brief."

    _write(
        routine_path,
        "---\nenabled: true\ncron: '30 9 * * *'\nprompt: Do the updated brief.\n---\n",
    )
    updated = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert updated.cancelled == 1
    assert updated.scheduled == 1
    assert len(scheduler.jobs) == 1
    assert scheduler.jobs[0]["cron"] == "30 9 * * *"
    assert scheduler.jobs[0]["metadata"]["name"] == "brief"


def test_reconcile_removed_or_disabled_file_cancels_job(tmp_path):
    workspaces = tmp_path / "workspaces"
    enabled = workspaces / "u_jane" / "routines" / "enabled.md"
    disabled = workspaces / "u_jane" / "routines" / "soon-disabled.md"
    body = "---\nenabled: true\ncron: '*/15 * * * *'\nprompt: Run this.\n---\n"
    _write(enabled, body)
    _write(disabled, body)
    scheduler = _FakeScheduler()

    reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )
    assert len(scheduler.jobs) == 2

    enabled.unlink()
    _write(disabled, "---\nenabled: false\n---\n")
    result = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert result.cancelled == 2
    assert scheduler.jobs == []
