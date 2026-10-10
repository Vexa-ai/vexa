"""No file in agent-api, the worker or the workspace primitives is opened by name, except through
``workspace_paths`` or for a reason written down here.

These processes run as root over work trees the model's tools can write, and opening a path by name
follows a link at the leaf and at every directory on the way — the class
``test_worktree_links_refused.py`` closes one site at a time. ``workspace_paths`` (vendored as
``llm.workspace_paths``, parity fact ``workspace-paths``) is the one place that opens a work-tree
path, nofollow. This scans the source of ``core/agent`` and ``core/workspaces`` (tests excepted) for
a bare ``open()``, ``Path.open``/``read_text``/``write_text``/``read_bytes``/``write_bytes``, or a
``shutil`` ``rmtree``/``copytree``/``copy2``/``copyfile``, and fails on any outside the allow-list
below. Each allowed entry names the function and why the path it opens is not a work tree. An entry
that no longer matches anything fails too, so the list cannot rot into a blanket exemption. The
scanner proves it is not vacuous by catching every shape in a planted sample first.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
CORE = AGENT.parent
ROOTS = (AGENT, CORE / "workspaces")
#: The helper itself, in each place it is vendored.
HELPER = {CORE / "workspaces" / "shared" / "workspace_paths.py", AGENT / "llm" / "workspace_paths.py"}
_SKIP_DIRS = {"tests", ".venv", "node_modules", "__pycache__"}

#: Trees outside the serving processes' reach of a work tree, skipped whole.
OUT_OF_SCOPE = {
    "agent/services": "separate services (the credential broker) with no workspace store mounted",
    "agent/eval": "benches and replays a developer runs by hand over fixtures, never in a server",
    "agent/contracts": "the contract schemas and their loader, read from the image",
}

_OPERATOR = "an operator-mounted key, token, credential or config file — never under the store"
_IMAGE = "a file this image ships (a manifest, a policy, a tool definition, a preset's image copy)"
_STORE = ("control-plane state in a root-owned dot-directory of the store (.secrets, .attached, "
          ".imports, .invites, .routines, .onboarding-research) — no work tree, no tool writes it")
_PROCESS = "the process's own temp directory or container log — never under the store"

#: ``<path under core/>::<function qualname>`` (``*`` for a whole module) → why it is not a work tree.
ALLOW = {
    "agent/control_plane/broker_assertion.py::load_key": _OPERATOR,
    "agent/control_plane/config_preflight.py::*": _OPERATOR,
    "agent/control_plane/config_test.py::test_subscription_credentials": _OPERATOR,
    "agent/control_plane/git_credentials.py::read_github_token": _OPERATOR,
    "agent/control_plane/identity_token.py::_read": _OPERATOR,
    "agent/control_plane/route_policy.py::load": _IMAGE,
    "agent/control_plane/routers/health.py::build.mcp_tools_manifest": _IMAGE,
    "agent/control_plane/scaffolds.py::read_preset": _IMAGE,
    "agent/shared/tools.py::ToolRegistry.from_dir": _IMAGE,
    "agent/worker/engine.py::<module>": _IMAGE,
    "agent/control_plane/secret_store.py::*": _STORE,
    "agent/control_plane/git_secret_store.py::get": _STORE,
    "agent/control_plane/workspace_attach.py::_load_state": _STORE,
    "agent/control_plane/workspace_attach.py::_plain_state": _STORE,
    "agent/control_plane/workspace_import.py::*": _STORE,
    "agent/control_plane/workspace_membership.py::read_invites": _STORE,
    "agent/control_plane/workspace_membership.py::_write_invites": _STORE,
    "agent/control_plane/workspace_routines.py::_read_approvals": _STORE,
    "agent/control_plane/workspace_routines.py::_write_approvals": _STORE,
    "agent/control_plane/workspace_routines.py::_record_existing_approvals": _STORE,
    "agent/control_plane/routine_resign.py::resign_unsigned_routines": _STORE,
    "agent/control_plane/onboarding_research.py::Research.locked": _STORE,
    "agent/control_plane/onboarding_research.py::Research._write_audit": _STORE,
    "agent/control_plane/deploy_keys.py::*": _PROCESS,
    "agent/worker/friction.py::report": _PROCESS,
}

_PATH_ATTRS = {"read_text", "write_text", "read_bytes", "write_bytes"}
_SHUTIL_ATTRS = {"rmtree", "copytree", "copy2", "copyfile"}
#: ``<module>.open`` that is not a file opened by path name (``os.open`` is the fd primitive the
#: helper is built from; the others open no file).
_NOT_A_FILE_OPEN = {"os", "webbrowser", "tarfile"}


def bare_io_calls(source: str, filename: str = "<src>") -> list[tuple[str, int, str]]:
    """Every bare file open in ``source``, as ``(enclosing qualname, line, what)``."""
    found: list[tuple[str, int, str]] = []

    def visit(node: ast.AST, stack: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = (*stack, node.name)
        if isinstance(node, ast.Call):
            f = node.func
            what = ""
            if isinstance(f, ast.Name) and f.id in ("open", "rmtree", "copytree", "copy2", "copyfile"):
                what = f.id
            elif isinstance(f, ast.Attribute):
                if f.attr in _PATH_ATTRS or f.attr in _SHUTIL_ATTRS:
                    what = f.attr
                elif f.attr == "open" and not (isinstance(f.value, ast.Name)
                                               and f.value.id in _NOT_A_FILE_OPEN):
                    what = ".open"
            if what:
                found.append((".".join(stack) or "<module>", node.lineno, what))
        for child in ast.iter_child_nodes(node):
            visit(child, stack)

    visit(ast.parse(source, filename), ())
    return found


def _sources():
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(CORE).as_posix()
            if set(path.relative_to(root).parts) & _SKIP_DIRS or path in HELPER:
                continue
            if any(rel.startswith(d + "/") for d in OUT_OF_SCOPE):
                continue
            yield path, rel


def _allowed(rel: str, qualname: str) -> "str | None":
    for key in (f"{rel}::{qualname}", f"{rel}::*"):
        if key in ALLOW:
            return key
    return None


PLANTED = '''
import io, os, shutil
from pathlib import Path
from shutil import rmtree
def reader(ws):
    open(ws / "a")
    (ws / "b").read_text()
    Path(ws, "c").read_bytes()
    with (ws / "d").open("w") as f: pass
    io.open(ws / "e")
class Writer:
    def write(self, ws):
        (ws / "f").write_text("x")
        (ws / "g").write_bytes(b"x")
        shutil.rmtree(ws / "h")
        rmtree(ws / "i")
        shutil.copytree("/seed", ws)
        shutil.copy2("/seed/x", ws / "x")
        shutil.copyfile("/seed/y", ws / "y")
'''


def test_the_scanner_catches_every_shape():
    hits = bare_io_calls(PLANTED)
    assert {line for _q, line, _w in hits} == {6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 19}, hits
    assert ("Writer.write", 15, "rmtree") in hits and ("reader", 6, "open") in hits


def test_the_scanner_lets_the_helper_and_the_fd_primitives_through():
    ok = '''
import os
from workspaces.shared import workspace_paths as wpaths
def f(ws):
    wpaths.read_text_inside(ws, "a")
    wpaths.write_text_inside(ws, "b", "x")
    wpaths.remove_tree(ws / "c")
    fd = os.open("d", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=3)
    with os.fdopen(fd, "rb") as fh:
        fh.read()
'''
    assert bare_io_calls(ok) == []


def test_no_work_tree_path_is_opened_by_name_outside_workspace_paths():
    offenders = []
    for path, rel in _sources():
        for qualname, line, what in bare_io_calls(path.read_text(encoding="utf-8"), rel):
            if not _allowed(rel, qualname):
                offenders.append(f"{rel}:{line} {what} in {qualname}")
    assert offenders == [], (
        "a file opened by name outside workspace_paths — if the path can be under a work tree, go "
        "through workspace_paths (read_text_inside, write_text_inside, copy_file_inside, "
        "remove_tree, …); if it cannot, add the function to ALLOW with the reason:\n  "
        + "\n  ".join(offenders))


def test_every_allow_entry_still_names_a_bare_open():
    used = set()
    for path, rel in _sources():
        for qualname, _line, _what in bare_io_calls(path.read_text(encoding="utf-8"), rel):
            key = _allowed(rel, qualname)
            if key:
                used.add(key)
    stale = sorted(set(ALLOW) - used)
    assert stale == [], f"allow-list entries that no longer match a bare open — remove them: {stale}"


@pytest.mark.parametrize("copy", sorted(HELPER - {CORE / "workspaces" / "shared" / "workspace_paths.py"}))
def test_the_vendored_helper_is_the_canonical_bytes(copy):
    assert copy.read_bytes() == (CORE / "workspaces" / "shared" / "workspace_paths.py").read_bytes()
