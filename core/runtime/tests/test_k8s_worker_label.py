"""An agent worker's Pod says it is one — the label the chart's NetworkPolicy keys on.

Runtime-managed pods may reach meeting-api for their lifecycle callbacks and recording uploads; agent
workers act through the gateway and must not. The policy can tell them apart only by a label, so the
k8s backend stamps the same `vexa.role: worker` the docker backend stamps, and nothing else gets it.
"""
from runtime_kernel.k8s_backend import ROLE_LABEL, build_pod
from runtime_kernel.profiles import Runnable


def _labels(workload_id: str) -> dict:
    pod = build_pod(name=f"vexa-{workload_id}", workload_id=workload_id,
                    runnable=Runnable(image="img"), env={}, namespace=None, resources=None)
    return pod["metadata"]["labels"]


def test_an_agent_worker_pod_carries_the_worker_role():
    labels = _labels("agent-58-chat")
    assert labels[ROLE_LABEL] == "worker"
    assert labels["runtime.managed"] == "true"


def test_a_meeting_bot_pod_carries_no_role():
    labels = _labels("meeting-bot-1234")
    assert ROLE_LABEL not in labels
    assert labels["runtime.managed"] == "true"
