"""#528 · B1 (meeting-api lane) — the standing gate: no bare Redis client construction.

A stdlib-``ast`` gate over ``src/meeting_api/**/*.py``. It fails on any Redis client construction
(``redis.from_url`` / ``aioredis.from_url`` / any ``*.from_url`` alias, or ``Redis(...)`` /
``StrictRedis(...)``) whose kwargs lack BOTH ``socket_timeout`` AND ``health_check_interval`` — the
"bare client" shape that, on the 2026-04-21 / 04-26 incidents, kept broken connections after a Redis
outage until every service was manually restarted (no timeout → the socket never raises; no health
check → the pool never revalidates).

Rooted at meeting_api only (the runtime lane has a twin at core/runtime/tests). Zero new deps —
stdlib ``ast`` — so it runs in the SQLAlchemy-free gate venv. A legitimate future bare client goes
in ALLOWLIST as a reviewed decision, never a silent hole.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "meeting_api"

# Reviewed exceptions: "<relpath>:<lineno>" → justification. Empty by design: both in-src sites hardened.
ALLOWLIST: dict[str, str] = {}

REQUIRED = {"socket_timeout", "health_check_interval"}


def _is_redis_construction(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr == "from_url":
            return True
        if f.attr in {"Redis", "StrictRedis"}:
            return True
    if isinstance(f, ast.Name) and f.id in {"Redis", "StrictRedis"}:
        return True
    return False


def _kwarg_names(call: ast.Call) -> set[str]:
    names = {kw.arg for kw in call.keywords if kw.arg is not None}
    # a **kwargs splat could carry them dynamically — treat as satisfied (can't prove otherwise),
    # but that is a deliberate escape hatch, not the shape we're guarding.
    if any(kw.arg is None for kw in call.keywords):
        names |= REQUIRED
    return names


def scan(root: Path = SRC) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root.parent.parent))
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_redis_construction(node):
                if f"{rel}:{node.lineno}" in ALLOWLIST:
                    continue
                missing = REQUIRED - _kwarg_names(node)
                if missing:
                    findings.append(f"{rel}:{node.lineno} bare redis client — missing {sorted(missing)}")
    return sorted(findings)


def test_no_bare_redis_clients():
    """Every Redis client in meeting_api sets socket_timeout + health_check_interval. RED at base
    (2 findings: __main__.py + collector/adapters.py), GREEN at head. Negative control: dropping the
    kwargs from either site re-adds its finding."""
    findings = scan()
    assert findings == [], "bare Redis client construction (see #528):\n" + "\n".join(findings)


def test_gate_detects_a_bare_client(tmp_path):
    """The gate actually fires on the bare shape (so green means clean, not broken)."""
    (tmp_path / "m").mkdir(parents=True)
    (tmp_path / "m" / "x.py").write_text("import redis.asyncio as r\nc = r.from_url('redis://x', decode_responses=True)\n")
    bad = scan(tmp_path / "m")
    assert len(bad) == 1 and "missing" in bad[0]
    (tmp_path / "m" / "x.py").write_text(
        "import redis.asyncio as r\nc = r.from_url('redis://x', socket_timeout=10, health_check_interval=30)\n")
    assert scan(tmp_path / "m") == []
