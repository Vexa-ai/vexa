"""The credential broker is healthy only when its store answers (offline — no stack).

The broker's /health is liveness (the process answers, store or not); /ready answers 503 while the
credential store does not. Compose's healthcheck gates `depends_on: service_healthy`, so it reads
/ready.
"""
from __future__ import annotations

from workspace_store_wiring_test import _service


def test_the_brokers_healthcheck_reads_readiness():
    block = _service("credential-broker")
    (check,) = [line for line in block.splitlines() if "urlopen(" in line]
    assert "localhost:8100/ready" in check
