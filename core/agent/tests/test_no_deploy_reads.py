"""`core/` DOES NOT READ `deploy/`.

The domains are what merges to main. `deploy/` is a lane — compose overrides, the dogfood rig, the
scripts one host runs — and it merges on a different schedule or not at all. A file under `core/`
that reads one under `deploy/` makes the two inseparable: merging the domains drags the lane with
them, and a lane file nobody meant to publish becomes a dependency of the product.

It had happened five times over, all of it invisible because nothing asserted otherwise: five tests
in this package read `deploy/dogfood/rig/vexa_control_mcp.py` as their source of truth, and the
worker's MCP allow-list cited a measurement document in the same directory as its provenance.

WHAT IS ALLOWED, and why it is not the same thing:
  * `deploy/compose/docker-compose.yml` — a DEPLOY SURFACE assertion. A test that says "this key
    reaches the container" has to read the surface that carries it; there is no in-core copy that
    could be true. Named per file, never blanket.
  * a path inside a STRING that is prose — a docstring explaining where something came from is a
    citation, not a read. The check looks for path construction, not for the word.
  * the word "deploy" that is not this repo's directory — `deploy_keys.py` names a key file
    `deploy/<id>.priv` inside a secret store, and `entities.py` has "deploy" as a plain word. The
    check matches the LANE DIRECTORIES by name, so it cannot fire on either.
"""
from __future__ import annotations

import ast
import pathlib
import re

CORE = pathlib.Path(__file__).resolve().parents[2]
REPO = CORE.parent

#: The lane directories this repo actually has. Matching them by name is what keeps the check off
#: `deploy/<id>.priv` inside a secret store and off the plain English word.
LANE = re.compile(r"(^|/)deploy/(dogfood|compose|helm|lite|contracts)(/|$)")
#: A path built segment by segment — `root / "deploy" / "dogfood" / "mail"` — never matches the
#: regex above, because no single literal in it is a path. "dogfood" is unambiguous on its own: it
#: is only ever the lane, so one literal is enough to catch the joined form.
SEGMENT = "dogfood"

#: file (repo-relative) -> why it may read a deploy surface. One line each, and it should be
#: uncomfortable to add one.
ALLOWED = {
    "core/agent/tests/test_secrets_key_profile.py":
        "asserts a secret key reaches the container — a claim about the deploy surface itself, "
        "which has no in-core copy that could be true",
    "core/flows/eval/dna/replay.py":
        "an eval CLI reading a file in the OPERATOR'S HOME (~/dev/estate/...), not this repo — a "
        "different failure class: it makes nothing inseparable, it just does not run elsewhere",
}


def _reads_a_deploy_path(tree: ast.AST) -> list:
    """Every expression that BUILDS a path under deploy/ — not every mention of the word.

    A string that is only ever a docstring or a comment is a citation. What matters is a literal
    that gets joined onto a path or opened."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        v = node.value
        if not (LANE.search(v) or v == SEGMENT):
            continue
        hits.append((node.lineno, v[:80]))
    return hits


def _docstring_lines(tree: ast.AST) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(node, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
               and isinstance(b[0].value.value, str):
                out |= set(range(b[0].lineno, b[0].end_lineno + 1))
    return out


def test_no_file_under_core_builds_a_path_under_deploy():
    bad = []
    for f in sorted(CORE.rglob("*.py")):
        if {".venv", "__pycache__", "node_modules"} & set(f.parts):
            continue
        rel = str(f.relative_to(REPO))
        if f.name == pathlib.Path(__file__).name:
            continue                          # the detector names the paths it detects
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except SyntaxError:
            continue
        docs = _docstring_lines(tree)
        for lineno, value in _reads_a_deploy_path(tree):
            if lineno in docs:
                continue                      # a citation, not a read
            if rel in ALLOWED:
                continue
            bad.append(f"{rel}:{lineno} -> {value!r}")
    assert not bad, (
        "a file under core/ reads a path under deploy/ — merging the domains would drag the lane "
        "with them:\n  " + "\n  ".join(bad))


def test_the_allowlist_has_no_dead_entries():
    """An allowlist row naming a file that no longer reads a deploy path is a claim about the code
    that has stopped being true."""
    for rel in ALLOWED:
        assert (REPO / rel).is_file(), f"{rel} no longer exists"


def test_the_worker_allowlist_is_declared_in_core():
    """The MCP allow-set is the worker's least-privilege boundary. Its provenance was a measurement
    document in the dogfood lane, so the boundary could not be reviewed — or shipped — without it."""
    decl = CORE / "agent" / "worker" / "mcp_tools.v1.json"
    assert decl.is_file(), "the worker's tool allow-list has no declared file in core/"
    import json
    doc = json.loads(decl.read_text())
    assert doc["tools"], "the allow-list is empty"
    assert doc.get("provenance"), "an allow-list with no provenance is a list nobody can review"
