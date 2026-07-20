"""A1 (#675) — OFFLINE proof that the k8s backend execs an in-image path for the meeting-bot profile.

`kubectl run --command -- <argv>` REPLACES the image entrypoint (sets Pod.spec.containers[].command).
So whatever the meeting-bot profile carries as its command becomes argv[0] of the Pod. The shipped bot
image has ENTRYPOINT ["/app/entrypoint.sh"] and no /app/vexa-bot/ directory — so a profile command of
/app/vexa-bot/entrypoint.sh makes every k8s spawn StartError (exit 128, "no such file or directory").

The fix drops the meeting-bot profile command: the k8s backend then omits `--command` entirely and the
Pod boots the image ENTRYPOINT — the real launcher. These tests capture the exact kubectl argv without
a cluster (the live-cluster lifecycle lives in test_k8s_backend.py) by stubbing the module's _kubectl.

RED on main: pre-fix the meeting-bot runnable carries ["/app/vexa-bot/entrypoint.sh"], so the argv here
would contain `--command -- /app/vexa-bot/entrypoint.sh` and the assertions below fail.
"""
from __future__ import annotations

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import default_registry
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable


def _capture_run_argv(monkeypatch) -> list:
    """Stub the module-level _kubectl so start() runs offline; return the captured argv list."""
    calls: list[list[str]] = []

    def fake_kubectl(*args, check=True):
        calls.append(list(args))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    return calls


def test_meeting_bot_k8s_run_omits_command_uses_image_entrypoint(monkeypatch):
    """The meeting-bot profile has no command ⇒ `kubectl run` carries NO `--command`, so the Pod execs
    the shipped bot image's own ENTRYPOINT (the real, in-image launcher)."""
    calls = _capture_run_argv(monkeypatch)
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot:test")
    runnable = default_registry().resolve("meeting-bot")
    assert runnable.command is None  # the source of the fix (#675)
    assert runnable.image == "vexaai/vexa-bot:test"

    K8sBackend(namespace="ns").start("mtg-1", runnable, env={"VEXA_BOT_CONFIG": "{}"})

    run_argv = calls[0]
    assert run_argv[0] == "run"
    # No entrypoint replacement: the image ENTRYPOINT (/app/entrypoint.sh) boots the container.
    assert "--command" not in run_argv
    # And the phantom path never appears anywhere in the argv.
    assert not any("/app/vexa-bot/entrypoint.sh" in a for a in run_argv)


def test_k8s_run_still_replaces_entrypoint_for_a_profile_that_has_a_command(monkeypatch):
    """The entrypoint-replacement machinery is intact: a profile that DOES carry a command (e.g. agent)
    still replaces the image ENTRYPOINT — the #675 fix is scoped to dropping the bogus meeting-bot
    command, not to removing entrypoint replacement wholesale.

    ZAKI adaptation: this backend spawns via a COMPLETE `--overrides` container (the only way to set the
    #15 pod-hardening securityContext), so the command travels in that container's `command` field, not
    a `kubectl run --command` flag. The behavioural guarantee is identical — a command-carrying profile's
    argv still overrides the entrypoint — but it is asserted on the pod spec rather than the run flag."""
    import json

    calls = _capture_run_argv(monkeypatch)
    K8sBackend(namespace="ns").start(
        "agent-1", Runnable(image="img", command=["python", "-m", "worker"]), env={}
    )
    run_argv = calls[0]
    override = json.loads(run_argv[run_argv.index("--overrides") + 1])
    assert override["spec"]["containers"][0]["command"] == ["python", "-m", "worker"]
