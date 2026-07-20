"""A1 (#675) — OFFLINE proof that the k8s backend execs an in-image path for the meeting-bot profile.

This fork's k8s backend spawns via a complete `kubectl run --overrides` container (the only way to
attach the #15 pod-hardening securityContext), so a profile's command lands in
`Pod.spec.containers[0].command` INSIDE that --overrides JSON — NOT via a `kubectl run --command` flag.
The shipped bot image has ENTRYPOINT ["/app/entrypoint.sh"] and no /app/vexa-bot/ directory — so a
container command of /app/vexa-bot/entrypoint.sh makes every k8s spawn StartError.

The #675 fix drops the meeting-bot profile command (command=None): the --overrides container then emits
NO `command` key and the Pod boots the image ENTRYPOINT — the real launcher. These tests capture the
exact kubectl argv + --overrides spec without a cluster (the live-cluster lifecycle lives in
test_k8s_backend.py) by stubbing the module's _kubectl.

RED on main: pre-fix the meeting-bot runnable carried ["/app/vexa-bot/entrypoint.sh"], so the
--overrides container below would carry `"command": ["/app/vexa-bot/entrypoint.sh"]` and the
container-command assertion here would fail.
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
    """The meeting-bot profile has no command ⇒ its --overrides container carries NO `command` key
    (and the argv no `--command` flag), so the Pod execs the shipped bot image's own ENTRYPOINT."""
    import json

    calls = _capture_run_argv(monkeypatch)
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot:test")
    runnable = default_registry().resolve("meeting-bot")
    assert runnable.command is None  # the source of the fix (#675)
    assert runnable.image == "vexaai/vexa-bot:test"

    K8sBackend(namespace="ns").start("mtg-1", runnable, env={"VEXA_BOT_CONFIG": "{}"})

    run_argv = calls[0]
    assert run_argv[0] == "run"
    # The MEANINGFUL proof: this backend puts the command in the --overrides container, so assert that
    # container carries no `command` key — hence k8s boots the image ENTRYPOINT.
    override = json.loads(run_argv[run_argv.index("--overrides") + 1])
    assert "command" not in override["spec"]["containers"][0]
    # No --command flag either, and the phantom path never appears anywhere in the argv.
    assert "--command" not in run_argv
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
