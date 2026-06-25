"""Pure unit tests (no docker daemon) for the cosmetic worker container naming + grouping labels.

Agent-dispatch workers get a ``vexa-worker-*`` container name (the workload id / Stream topics / reaper
keys are untouched) and ``vexa.role``/``vexa.kind`` labels; non-agent workloads pass through unchanged.
"""
from runtime_kernel.docker_backend import DockerBackend, _worker_naming


def test_meet_worker_named_and_labelled():
    leaf, labels = _worker_naming("agent-meet-abc123")
    assert leaf == "worker-meet-abc123"
    assert labels == {"vexa.role": "worker", "vexa.kind": "meet"}


def test_chat_worker_named_and_labelled():
    leaf, labels = _worker_naming("agent-alice-chat")
    assert leaf == "worker-alice-chat"
    assert labels == {"vexa.role": "worker", "vexa.kind": "chat"}


def test_event_worker_named_and_labelled():
    leaf, labels = _worker_naming("agent-alice-scheduled-deadbeef12")
    assert leaf == "worker-alice-scheduled-deadbeef12"
    assert labels == {"vexa.role": "worker", "vexa.kind": "event"}


def test_non_agent_workload_unchanged():
    leaf, labels = _worker_naming("meeting-bot-xyz")
    assert leaf == "meeting-bot-xyz"
    assert labels == {}


def test_cname_applies_prefix_and_rename():
    b = DockerBackend()
    assert b._cname("agent-meet-abc123") == "vexa-worker-meet-abc123"
    assert b._cname("agent-alice-chat") == "vexa-worker-alice-chat"
    assert b._cname("rt-dockertest") == "vexa-rt-dockertest"  # non-agent: prefix only
