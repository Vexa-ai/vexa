"""Shared eval fixtures: the lifecycle.v1 goldens + a fake webhook receiver + fakeredis.

All autonomous — no docker, no meeting, no network. The lifecycle goldens are loaded BY
PATH from the sealed contract (the seam, P8); the webhook receiver is an in-memory async
callable that records every delivery; the redis is `fakeredis.aioredis` (no server).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture(autouse=True)
def _stt_configured(monkeypatch):
    """CC4 baseline: the product transcribes by default, so the suite runs as a TRANSCRIPTION-configured
    deployment — STT creds present → a default `transcribe_enabled=true` spawn is served (not 503). A test
    for the UNCONFIGURED case clears these explicitly (`monkeypatch.delenv`)."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test/transcribe")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "test-stt-token")


# --- lifecycle.v1 goldens (the seam) ---------------------------------------------------

def _repo_root() -> Path:
    rel = Path("meetings") / "contracts" / "lifecycle.v1"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent
    raise FileNotFoundError("monorepo root with meetings/contracts/lifecycle.v1 not found")


def _golden_dir() -> Path:
    return _repo_root() / "meetings" / "contracts" / "lifecycle.v1" / "golden"


def load_golden(name: str) -> Dict[str, Any]:
    """Load a lifecycle.v1 golden by `LifecycleEvent.<case>` stem."""
    return json.loads((_golden_dir() / f"{name}.json").read_text())


@pytest.fixture
def goldens() -> Dict[str, Dict[str, Any]]:
    """Every lifecycle.v1 golden, keyed by case (e.g. 'joining', 'active')."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(_golden_dir().glob("LifecycleEvent.*.json")):
        case = p.stem.split(".", 1)[1]
        out[case] = json.loads(p.read_text())
    return out


# --- fake webhook receiver -------------------------------------------------------------

@dataclass
class FakeReceiver:
    """An in-memory webhook receiver. `respond` decides the status code per call.

    Records (url, body bytes, headers) of every delivery so the eval can recompute the
    HMAC and assert what was/wasn't delivered. The default responds 200; tests can set
    `.next_codes` to script a 500-then-200 sequence (the retry path).
    """

    default_code: int = 200
    next_codes: List[int] = field(default_factory=list)
    received: List[Dict[str, Any]] = field(default_factory=list)

    async def __call__(self, url: str, body: bytes, headers: Dict[str, str]) -> "FakeResponse":
        code = self.next_codes.pop(0) if self.next_codes else self.default_code
        self.received.append({"url": url, "body": body, "headers": dict(headers), "code": code})
        return FakeResponse(code)


@dataclass
class FakeResponse:
    status_code: int


@pytest.fixture
def receiver() -> FakeReceiver:
    return FakeReceiver()


# --- fakeredis -------------------------------------------------------------------------

@pytest.fixture
async def fake_redis():
    """An async fakeredis client (no server). Flushed per test."""
    import fakeredis.aioredis as fakeaio

    client = fakeaio.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.flushall()
        await client.aclose()
