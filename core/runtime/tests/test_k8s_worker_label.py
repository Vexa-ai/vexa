"""A spawned Pod says which class of workload it is — the label the chart's NetworkPolicies key on.

Runtime-managed pods may reach meeting-api for their lifecycle callbacks and recording uploads; agent
workers act through the gateway and must not, and each class has its own egress policy. The policies
can tell them apart only by a label, so the k8s backend stamps `vexa.role` from the PROFILE that runs
the workload. The caller's workload id plays no part: an agent-profile Pod is a worker whatever it
is called, and a bot is a bot even when its id starts with `agent-`.
"""
from runtime_kernel import default_registry
from runtime_kernel.k8s_backend import ROLE_LABEL, build_pod
from runtime_kernel.profiles import Runnable


def _labels(workload_id: str, runnable: Runnable) -> dict:
    pod = build_pod(name=f"vexa-{workload_id}", workload_id=workload_id,
                    runnable=runnable, env={}, namespace=None, resources=None)
    return pod["metadata"]["labels"]


def _profile(name: str) -> Runnable:
    return default_registry().get(name).runnable


def test_an_agent_worker_pod_carries_the_worker_role():
    labels = _labels("agent-58-chat", _profile("agent"))
    assert labels[ROLE_LABEL] == "worker"
    assert labels["runtime.managed"] == "true"


def test_a_meeting_bot_pod_carries_the_bot_role():
    labels = _labels("meeting-bot-1234", _profile("meeting-bot"))
    assert labels[ROLE_LABEL] == "bot"
    assert labels["runtime.managed"] == "true"


def test_the_role_follows_the_profile_not_the_workload_id():
    assert _labels("mtg-1-abcdef12", _profile("agent"))[ROLE_LABEL] == "worker"
    assert _labels("agent-58-chat", _profile("meeting-bot"))[ROLE_LABEL] == "bot"


def test_a_classless_runnable_carries_no_role():
    assert ROLE_LABEL not in _labels("agent-58-chat", Runnable(image="img"))


def test_a_spawned_pod_is_not_a_cluster_client():
    for profile in ("agent", "meeting-bot"):
        pod = build_pod(name="vexa-w", workload_id="w", runnable=_profile(profile), env={},
                        namespace=None, resources=None)
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert pod["spec"]["enableServiceLinks"] is False
