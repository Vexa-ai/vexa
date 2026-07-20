"""#528 · B1 (runtime lane) — the standing gate: no bare Redis client construction.

Twin of core/meetings/services/meeting-api/tests/test_redis_client_hardening.py, rooted at the
runtime_kernel package. Kept identical by contract (no cross-brick import) — a bare Redis client
(no socket_timeout / health_check_interval) is the shape that turned a Redis blip into a hung
scheduler tick that only a restart healed (2026-04-21). Zero deps — stdlib ``ast``.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "runtime_kernel"

# Reviewed exceptions: "<relpath>:<lineno>" → justification. Empty by design: the one site is hardened.
ALLOWLIST: dict[str, str] = {}

REQUIRED = {"socket_timeout", "health_check_interval"}


def _is_redis_construction(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr == "from_url" or f.attr in {"Redis", "StrictRedis"}:
            return True
    if isinstance(f, ast.Name) and f.id in {"Redis", "StrictRedis"}:
        return True
    return False


def _kwarg_names(call: ast.Call) -> set[str]:
    names = {kw.arg for kw in call.keywords if kw.arg is not None}
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
    """Every Redis client in runtime_kernel sets socket_timeout + health_check_interval. RED at base
    (1 finding: __main__.py), GREEN at head."""
    findings = scan()
    assert findings == [], "bare Redis client construction (see #528):\n" + "\n".join(findings)


def test_gate_detects_a_bare_client(tmp_path):
    (tmp_path / "p").mkdir(parents=True)
    (tmp_path / "p" / "x.py").write_text("import redis\nc = redis.from_url('redis://x', decode_responses=True)\n")
    assert len(scan(tmp_path / "p")) == 1
    (tmp_path / "p" / "x.py").write_text(
        "import redis\nc = redis.from_url('redis://x', socket_timeout=10, health_check_interval=30)\n")
    assert scan(tmp_path / "p") == []
