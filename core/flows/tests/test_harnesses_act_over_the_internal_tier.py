"""The eval and witness harnesses name a person the way flows itself does.

agent-api believes an `x-user-*` header only from the gateway's signature or from a service
presenting `X-Internal-Secret` (ADR-0041); a bare `X-User-Id` is a 401. The scripts under `eval/` and
`witness/` drive a live stack as a real user — chat turns, workspace reads, desk resets — and every
one of them used to send that bare header. They now build it with `flows_steps.agent.as_person`, the
same internal-tier headers the production steps send, which refuses to run without the secret.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

FLOWS = Path(__file__).resolve().parents[1]
HARNESSES = sorted(p for d in ("eval", "witness") for p in (FLOWS / d).rglob("*.py"))


def _bare_identity_headers(path: Path) -> list:
    """Lines where a string literal names `X-User-Id` — a header built by hand."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.strip().lower() == "x-user-id"]


def test_no_harness_builds_a_bare_identity_header():
    offenders = {str(p.relative_to(FLOWS)): lines for p in HARNESSES
                 if (lines := _bare_identity_headers(p))}
    assert not offenders, f"build these with flows_steps.agent.as_person: {offenders}"


def test_the_witness_steps_act_for_a_person_over_the_internal_tier(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-test-secret-not-a-placeholder")
    spec = importlib.util.spec_from_file_location("real_steps", FLOWS / "witness" / "real_steps.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    assert rs.as_person("7") == {"X-User-Id": "7",
                                 "X-Internal-Secret": "a-test-secret-not-a-placeholder"}


def test_without_the_secret_a_harness_refuses_rather_than_sends_a_bare_id(monkeypatch):
    from flows_steps.agent import as_person

    for name in ("INTERNAL_API_SECRET", "VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="INTERNAL_API_SECRET"):
        as_person("7")
