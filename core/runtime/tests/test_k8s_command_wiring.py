"""A1 (#675) — OFFLINE proof that the k8s backend execs an in-image path for the meeting-bot profile.

An explicit `command` on a Pod's container REPLACES the image entrypoint. So whatever the meeting-bot
profile carries as its command becomes argv[0] of the Pod. The shipped bot image has
ENTRYPOINT ["/app/entrypoint.sh"] and no /app/vexa-bot/ directory — so a profile command of
/app/vexa-bot/entrypoint.sh makes every k8s spawn StartError (exit 128, "no such file or directory").

The fix drops the meeting-bot profile command: the backend then omits `command` from the container
entirely and the Pod boots the image ENTRYPOINT — the real launcher. These tests capture the exact
submitted Pod manifest without a cluster (the live-cluster lifecycle lives in test_k8s_backend.py) by
faking the API server (k8s_fake.FakeApi).

RED on main: pre-fix the meeting-bot runnable carries ["/app/vexa-bot/entrypoint.sh"], so the
container below would carry that `command` and the assertions fail.
"""
from __future__ import annotations

import json

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import default_registry
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable


def _capture_submitted_pods(monkeypatch) -> list:
    """Fake the API server so start() runs offline; return the submitted Pod manifests (a live view)."""
    from k8s_fake import FakeApi

    return _Submitted(FakeApi().install(monkeypatch))


class _Submitted:
    def __init__(self, api):
        self._api = api

    def __getitem__(self, i):
        return self._api.submitted[i]

    def __len__(self):
        return len(self._api.submitted)

    def __iter__(self):
        return iter(self._api.submitted)


def test_meeting_bot_k8s_pod_omits_command_uses_image_entrypoint(monkeypatch):
    """The meeting-bot profile has no command ⇒ the submitted Pod's container carries NO `command`,
    so it execs the shipped bot image's own ENTRYPOINT (the real, in-image launcher)."""
    pods = _capture_submitted_pods(monkeypatch)
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot:test")
    runnable = default_registry().resolve("meeting-bot")
    assert runnable.command is None  # the source of the fix (#675)
    assert runnable.image == "vexaai/vexa-bot:test"

    K8sBackend(namespace="ns").start("mtg-1", runnable, env={"VEXA_BOT_CONFIG": "{}"})

    container = pods[0]["spec"]["containers"][0]
    # No entrypoint replacement: the image ENTRYPOINT (/app/entrypoint.sh) boots the container.
    assert "command" not in container
    # And the phantom path never appears anywhere in the manifest.
    assert "/app/vexa-bot/entrypoint.sh" not in json.dumps(pods[0])


def test_k8s_pod_still_replaces_entrypoint_for_a_profile_that_has_a_command(monkeypatch):
    """The replace machinery is intact: a profile that DOES carry a command (e.g. agent) still gets
    it as the container's argv — the fix is scoped to dropping the bogus meeting-bot command, not to
    removing entrypoint replacement wholesale."""
    pods = _capture_submitted_pods(monkeypatch)
    K8sBackend(namespace="ns").start(
        "agent-1", Runnable(image="img", command=["python", "-m", "worker"]), env={}
    )
    assert pods[0]["spec"]["containers"][0]["command"] == ["python", "-m", "worker"]
