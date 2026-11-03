"""The booking cap reaches meeting-api in compose (offline — no stack, no docker).

`BOOKED_MAX_PER_ACCOUNT` (ADR-0052 §8) is one value for the whole deployment (founder, 3 Nov 2026):
500 unless `.env` says otherwise. These pin the wiring; meeting-api's own tests pin what it does.
"""
from __future__ import annotations

from pathlib import Path

from flows_wiring_test import _service

ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"


def test_meeting_api_takes_the_cap_from_env_with_500_by_default():
    assert "- BOOKED_MAX_PER_ACCOUNT=${BOOKED_MAX_PER_ACCOUNT:-500}" in _service("meeting-api")


def test_the_env_example_names_the_cap():
    assert "BOOKED_MAX_PER_ACCOUNT=500" in ENV_EXAMPLE.read_text().splitlines()
