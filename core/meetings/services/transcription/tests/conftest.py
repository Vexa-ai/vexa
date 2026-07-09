"""Autonomous test fixtures — no GPU, no model download, no network.

The faster-whisper model is loaded lazily (on first use / startup), NOT at import, and
`TestClient(app)` without the `with` block does not run lifespan — so the suite exercises
the HTTP contract (health states, auth, validation) against the real app with `model` left
unloaded or replaced by a sentinel.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import transcription.main as svc


@pytest.fixture
def client() -> TestClient:
    return TestClient(svc.app)


@pytest.fixture
def loaded(monkeypatch):
    """Pretend the model is loaded (a non-None sentinel) without touching faster-whisper."""
    monkeypatch.setattr(svc, "model", object())
    return svc
