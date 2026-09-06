"""Finding 2 (live, 2026-09-03) — the rig was down 2.5 minutes for a dependency it does not use.

What happened: `rig.sh` runs the server out of `~/dev/vexa-flows1315/core/flows/.venv`, a SHARED
venv belonging to a stale checkout, because the rig has never had one of its own. This module's
`import shared.git_redaction` then failed on a missing `pydantic_settings`, the server would not
start, and stage-1 had to install a package into that shared venv to bring it back.

The dependency was never real. `shared/git_redaction.py` imports `re` and nothing else — it is
stdlib-pure by design, and so is `control_plane/secret_store.py` ("stdlib only. No new dependency
lands in the control-plane image for this"). What dragged pydantic in was the PACKAGE: importing
`shared.git_redaction` executes `shared/__init__.py`, which re-exports `Settings` from
`shared.config`, which is pydantic-settings. The rig paid for the entire agent control plane to get
two regex functions.

So the rig loads those two files DIRECTLY, by path, and these tests hold the three things that
makes true: the import is light, the two modules stay stdlib-pure, and the behaviour is identical
to importing them as modules.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import rig_secrets

RIG_DIR = pathlib.Path(__file__).resolve().parents[1]
BORROWED = ("shared/git_redaction.py", "control_plane/secret_store.py")


def test_importing_rig_secrets_does_not_drag_in_the_control_plane():
    """GATE (finding 2). A subprocess, because the parent may already hold pydantic for other
    reasons — the question is what a FRESH interpreter has to have installed to import this."""
    probe = (
        "import sys, json;"
        f"sys.path.insert(0, {str(RIG_DIR)!r});"
        "import rig_secrets;"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.split('.')[0] in {'pydantic', 'pydantic_settings', 'shared', 'control_plane'})))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         env={**_env(), "VEXA_RIG_STATE_DIR": str(rig_secrets.STATE_DIR)})
    assert out.returncode == 0, out.stderr[-2000:]
    dragged = out.stdout.strip().splitlines()[-1]
    assert dragged == "[]", (
        f"importing rig_secrets pulled in {dragged} — the rig is paying for the agent control "
        "plane's dependency tree to get two stdlib-pure files, which is what took it down")


def test_the_two_borrowed_modules_are_stdlib_pure():
    """The rig's contract with `core/agent`: these two files carry no third-party import.

    Loading them by path is only safe while that holds. If one of them grows a dependency, this
    fails HERE — at the gate, naming the module — rather than at the rig's next restart, which is
    where it failed the first time."""
    std = set(sys.stdlib_module_names)
    for rel in BORROWED:
        src = pathlib.Path(rig_secrets.agent_src()) / rel
        assert src.is_file(), f"{rel} moved — rig_secrets loads it by path"
        outside = set()
        for node in ast.walk(ast.parse(src.read_text())):
            if isinstance(node, ast.Import):
                outside |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                outside.add(node.module.split(".")[0])
        assert not (outside - std), f"{rel} now imports {sorted(outside - std)}"


def test_the_borrowed_code_behaves_exactly_as_the_module_would():
    """Loading by path must not be a fork. Same functions, same answers."""
    assert rig_secrets.redact("ghp_" + "A" * 36) != "ghp_" + "A" * 36
    assert rig_secrets.looks_like_token("glpat-" + "A" * 20)
    assert rig_secrets.TOKEN_PREFIXES and "ghp_" in rig_secrets.TOKEN_PREFIXES
    sealed = rig_secrets._secret_store.seal("hello", b"k" * 32)
    assert rig_secrets._secret_store.unseal(sealed, b"k" * 32) == "hello"
    assert rig_secrets._secret_store.unseal(sealed, b"x" * 32) is None


def test_every_third_party_import_in_the_rig_is_declared():
    """GATE (finding 2, the other half). The rig had no `pyproject.toml`, so nothing anywhere said
    what it needs to run — which is why it was started against whatever venv was lying around."""
    import tomllib

    pyproject = RIG_DIR / "pyproject.toml"
    assert pyproject.is_file(), "the rig still has no dependency declaration"
    meta = tomllib.loads(pyproject.read_text())
    declared = {_dist(d) for d in meta["project"]["dependencies"]}

    std = set(sys.stdlib_module_names)
    local = {"vexa_control_mcp", "vexa_oauth", "rig_secrets", "mcpcli", "rehearse",
             "shared", "control_plane",          # loaded by path, never installed
             "flows", "flows_defs"}              # VEXA_FLOWS_SRC, a deployment input
    used = set()
    for f in ("vexa_control_mcp.py", "vexa_oauth.py", "rig_secrets.py", "mcpcli.py"):
        for node in ast.walk(ast.parse((RIG_DIR / f).read_text())):
            if isinstance(node, ast.Import):
                used |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                used.add(node.module.split(".")[0])
    missing = {m for m in used if m not in std and m not in local} - {_mod(d) for d in declared}
    assert not missing, f"imported by the rig and declared nowhere: {sorted(missing)}"


_DIST_TO_MODULE = {"mcp": "mcp", "uvicorn": "uvicorn", "psycopg": "psycopg"}


def _dist(spec: str) -> str:
    for sep in (">=", "<=", "==", "~=", ">", "<", "[", ";"):
        spec = spec.split(sep)[0]
    return spec.strip()


def _mod(dist: str) -> str:
    return _DIST_TO_MODULE.get(dist, dist.replace("-", "_"))


def _env():
    import os
    return {k: v for k, v in os.environ.items()}
