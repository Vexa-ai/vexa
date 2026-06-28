"""``RUNTIME_BACKEND`` selects the spawn backend in the production entrypoint (P5.1 helm wiring).

Pure unit test — no Docker daemon and no Kubernetes cluster needed: the backend constructors are
lazy (they don't connect at __init__), so we only assert the env→class mapping and the invariant
that the Docker-only image-alias step is skipped for backends that lack ``ensure_image_alias``."""
from __future__ import annotations

import pytest

from runtime_kernel.__main__ import _build_backend
from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.process_backend import ProcessBackend


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, DockerBackend),          # default → docker (compose/desktop)
        ("docker", DockerBackend),
        ("DOCKER", DockerBackend),      # case-insensitive
        ("k8s", K8sBackend),            # helm / real cluster
        ("process", ProcessBackend),    # no-container fallback
    ],
)
def test_runtime_backend_env_selects_backend(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RUNTIME_BACKEND", raising=False)
    else:
        monkeypatch.setenv("RUNTIME_BACKEND", value)
    assert isinstance(_build_backend(), expected)


def test_k8s_backend_reads_pod_namespace(monkeypatch):
    monkeypatch.setenv("RUNTIME_BACKEND", "k8s")
    monkeypatch.setenv("POD_NAMESPACE", "vexa-prod")
    backend = _build_backend()
    assert isinstance(backend, K8sBackend)
    assert backend._ns == "vexa-prod"


def test_only_docker_backend_has_image_alias():
    # build_production_app() guards the AGENT_WORKER_IMAGE tag-alias on hasattr(backend,
    # "ensure_image_alias"): docker makes a local alias; k8s/process pull by full ref instead.
    assert hasattr(DockerBackend, "ensure_image_alias")
    assert not hasattr(K8sBackend, "ensure_image_alias")
    assert not hasattr(ProcessBackend, "ensure_image_alias")
