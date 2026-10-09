"""Every git subprocess in agent-api, the worker and the workspace primitives goes through gitexec.

``shared.gitexec.run_git`` (vendored as ``llm.gitexec`` and ``workspaces.shared.gitexec``) is what
keeps a workspace repository from choosing a program git runs. A raw ``subprocess.run(["git", …])``
anywhere else would quietly run without it — so this scans the source of ``core/agent`` and
``core/workspaces`` (tests excepted) and fails on one. The scanner proves it is not vacuous by
catching every raw shape in a planted sample first.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
CORE = AGENT.parent
ROOTS = (AGENT, CORE / "workspaces")
#: The module itself, in each place it is vendored (parity fact `git-exec`).
GITEXEC = {AGENT / "shared" / "gitexec.py", AGENT / "llm" / "gitexec.py",
           CORE / "workspaces" / "shared" / "gitexec.py"}

_SPAWNERS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
             "system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
             "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnlp",
             "create_subprocess_exec", "create_subprocess_shell", "posix_spawn", "posix_spawnp"}
_LIBRARIES = {"git", "pygit2", "dulwich", "sh", "plumbum"}


def _is_git_word(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        v = node.value.strip()
        return v == "git" or v.endswith("/git") or v.startswith("git ") or "/git " in v
    if isinstance(node, ast.JoinedStr) and node.values:
        return _is_git_word(node.values[0])
    return False


def _is_git_argv(node: ast.AST, git_names: set[str]) -> bool:
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _is_git_word(node.elts[0])
    if isinstance(node, ast.Name):
        return node.id in git_names
    return _is_git_word(node)


def raw_git_calls(source: str, filename: str = "<src>") -> list[str]:
    """Every place in ``source`` that starts git without gitexec, as ``file:line what``."""
    tree = ast.parse(source, filename)
    found: list[str] = []
    git_names: set[str] = set()
    for node in ast.walk(tree):          # names bound to a git argv anywhere in the module
        if isinstance(node, ast.Assign) and _is_git_argv(node.value, set()):
            git_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None \
                and isinstance(node.target, ast.Name) and _is_git_argv(node.value, set()):
            git_names.add(node.target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _LIBRARIES:
                    found.append(f"{filename}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[0] in _LIBRARIES and node.level == 0:
            found.append(f"{filename}:{node.lineno} from {node.module} import …")
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name == "which" and node.args and _is_git_word(node.args[0]):
                found.append(f"{filename}:{node.lineno} which('git')")
                continue
            if name not in _SPAWNERS:
                continue
            candidates = list(node.args[:1]) + [k.value for k in node.keywords if k.arg in ("args", "cmd")]
            if any(_is_git_argv(c, git_names) for c in candidates):
                found.append(f"{filename}:{node.lineno} {name}(git …)")
    return found


def _sources():
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            parts = set(path.relative_to(root).parts)
            if parts & {"tests", ".venv", "node_modules", "__pycache__"} or path in GITEXEC:
                continue
            yield path


PLANTED = '''
import subprocess, os, shutil, asyncio
import subprocess as _sp
import git
from dulwich import porcelain
subprocess.run(["git", "status"])
_sp.run(["git", "-C", "/w", "add", "-A"], check=False)
subprocess.Popen(("git", "log"))
subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"])
subprocess.run("git commit -m x", shell=True)
os.system("git gc")
argv = ["git", "show"]
subprocess.run(argv)
subprocess.run(args=["git", "fetch"])
shutil.which("git")
asyncio.create_subprocess_exec("git", "push")
'''


def test_the_scanner_catches_every_raw_shape():
    hits = raw_git_calls(PLANTED)
    lines = {int(h.split(":")[1].split()[0]) for h in hits}
    assert lines == {4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16}, hits


def test_the_scanner_lets_ordinary_code_through():
    ok = '''
import subprocess
from shared.gitexec import run_git
run_git(ws, "status")
subprocess.run(["claude", "-p", "x"])
label = "git" if any(k in t for k in ("git", "commit")) else "other"
ROLES = ("agent", "human", "git")
'''
    assert raw_git_calls(ok) == []


def test_no_git_subprocess_bypasses_gitexec():
    offenders = []
    for path in _sources():
        offenders += raw_git_calls(path.read_text(encoding="utf-8"), str(path.relative_to(CORE)))
    assert offenders == [], ("git started without shared.gitexec.run_git — route it through "
                             "run_git (or its vendored twin):\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("copy", sorted(GITEXEC - {AGENT / "shared" / "gitexec.py"}))
def test_the_vendored_copies_are_the_canonical_bytes(copy):
    assert copy.read_bytes() == (AGENT / "shared" / "gitexec.py").read_bytes()
