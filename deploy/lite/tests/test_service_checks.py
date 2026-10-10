"""The live service check exercises the function admin-api really admits a worker token with.

``service_checks.py`` finds that function as the one ``/internal/validate`` awaits on the delegation
module and calls it inside the booted container. This offline half fails on a rename the moment it
lands: validate.py awaits exactly one function there, the module defines it, and it is not private.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "core" / "identity" / "services" / "admin-api" / "src" / "admin_api" / "app"
CHECK = (Path(__file__).resolve().parent / "service_checks.py").read_text()


def test_validate_admits_through_one_public_function_the_module_defines():
    called = sorted(set(re.findall(r"await revocation\.([a-z_]+)\(", (APP / "validate.py").read_text())))
    assert len(called) == 1, called
    (name,) = called
    assert not name.startswith("_")
    module = ast.parse((APP / "delegation_revocation.py").read_text())
    defined = {n.name for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert name in defined
    for prefix in ("LIVE_PREFIX", "REVOKED_PREFIX"):
        assert re.search(rf"^{prefix} = ", (APP / "delegation_revocation.py").read_text(), flags=re.M), prefix


def test_the_live_check_finds_the_function_rather_than_naming_one():
    assert r'await revocation\.([a-z_]+)\(' in CHECK and "getattr(r, \"@FUNC@\")" in CHECK
    assert "is_revoked" not in CHECK and "is_admitted" not in CHECK
    for step in ("never=", "live=", "revoked=", "r.LIVE_PREFIX", "r.REVOKED_PREFIX"):
        assert step in CHECK, step
