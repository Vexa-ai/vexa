"""No workspace-store write inside a streamed response body (WS-S3, coordinator 2026-10-17).

Once ``http.response.start`` has gone out, the client already holds a 200: a write after that point
would be acknowledged before it is durable. Two gates hold it:

* **This scan (CI).** For every ``StreamingResponse(...)`` in ``core/agent``, every function its body
  expression calls is followed through module-resolved calls (same-module functions, ``from x import
  f``, ``alias.f``) across ``control_plane``, ``shared`` and ``core/workspaces``. A chain that reaches a
  store write — a ``workspace_paths`` writer or remover, ``write_json_atomic``, ``notify_written``/
  ``notify_removed``, or a ref-moving git subcommand — fails, unless a function on the chain is in
  :data:`ALLOW` with its reason AND wraps the write in ``durable_in_stream()``. Calls through objects
  (``self.x``, ``store.y``) cannot be resolved statically; the runtime guard covers them.
* **The runtime guard** (``workspaces.store.observer.StreamGuard`` + ``guard_response_not_started``):
  on the s3 backend a store write after the response began raises ``write_after_response_started``.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

AGENT = Path(__file__).resolve().parents[1]
CORE = AGENT.parent
ROOTS = (AGENT / "control_plane", AGENT / "shared", CORE / "workspaces")

WRITERS = {"write_bytes_inside", "write_text_inside", "write_new_inside", "unlink_inside",
           "append_text_inside", "remove_tree", "clear_tree_inside", "copy_file_inside",
           "copy_tree_inside", "write_json_atomic", "notify_written", "notify_removed"}
REF = {"commit", "merge", "reset", "revert", "cherry-pick", "rebase", "pull", "update-ref", "branch",
       "tag", "checkout", "switch", "stash", "am", "fast-import", "filter-branch", "replace", "notes",
       "clone"}

#: A function on a streamed body's call chain that may write, each with why its write is
#: acknowledged by a LATER event and not by the response status. It must use ``durable_in_stream``.
ALLOW = {
    "control_plane.routers.chats._mint_meeting_page":
        "the meeting page and its commit are durable in the workspace store before the stream yields "
        "the event that announces them (Vexa-ai/vexa#1601: minting after the yield races the client)",
}


def _modname(path: Path, core: Path) -> str:
    parts = list(path.relative_to(core).with_suffix("").parts)
    if parts[0] == "agent":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


class Scanner:
    def __init__(self, roots, core: Path) -> None:
        self.mods: dict[str, ast.Module] = {}
        self.src: dict[str, str] = {}
        for root in roots:
            for p in sorted(Path(root).rglob("*.py")):
                if "tests" in p.parts or ".venv" in p.parts:
                    continue
                m = _modname(p, core)
                self.src[m] = p.read_text()
                self.mods[m] = ast.parse(self.src[m])
        self.defs: dict = {}
        self.aliases: dict = {}
        for m, t in self.mods.items():
            al = {}
            for n in ast.walk(t):
                if isinstance(n, ast.ImportFrom) and n.module:
                    for a in n.names:
                        al[a.asname or a.name] = f"{n.module}.{a.name}"
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        al[a.asname or a.name.split(".")[0]] = a.name
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defs.setdefault((m, n.name), []).append(n)
            self.aliases[m] = al
        self.memo: dict = {}

    def resolve(self, m: str, call: ast.Call) -> list:
        f = call.func
        if isinstance(f, ast.Name):
            if (m, f.id) in self.defs:
                return [(m, f.id)]
            tgt = self.aliases[m].get(f.id, "")
            if "." in tgt and tuple(tgt.rsplit(".", 1)) in self.defs:
                return [tuple(tgt.rsplit(".", 1))]
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            tgt = self.aliases[m].get(f.value.id)
            if tgt and (tgt, f.attr) in self.defs:
                return [(tgt, f.attr)]
        return []

    @staticmethod
    def direct(node) -> str:
        for c in ast.walk(node):
            if isinstance(c, ast.Call):
                nm = _name(c)
                if nm in WRITERS:
                    return nm
                if nm in ("run_git", "_git", "git"):
                    lits = [a.value for a in c.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                    hit = [x for x in lits if x in REF]
                    if hit:
                        return f"git {hit[0]}"
        return ""

    def chain(self, key, stack=()):
        if key in self.memo:
            return self.memo[key]
        if key in stack:
            return None
        self.memo[key] = None
        for node in self.defs.get(key, []):
            d = self.direct(node)
            if d:
                self.memo[key] = [f"{key[0]}.{key[1]}", d]
                return self.memo[key]
            for c in ast.walk(node):
                if isinstance(c, ast.Call):
                    for k in self.resolve(key[0], c):
                        r = self.chain(k, stack + (key,))
                        if r:
                            self.memo[key] = [f"{key[0]}.{key[1]}"] + r
                            return self.memo[key]
        return None

    def streamed_writes(self) -> list[tuple[str, int, list]]:
        out = []
        for m, t in self.mods.items():
            for c in ast.walk(t):
                if not (isinstance(c, ast.Call) and _name(c) == "StreamingResponse" and c.args):
                    continue
                for x in ast.walk(c.args[0]):
                    keys = self.resolve(m, x) if isinstance(x, ast.Call) else (
                        [(m, x.id)] if isinstance(x, ast.Name) and (m, x.id) in self.defs else [])
                    for k in keys:
                        r = self.chain(k)
                        if r and r not in [o[2] for o in out]:
                            out.append((m, c.lineno, r))
        return out

    def allowed(self, chain: list) -> bool:
        for fq in chain[:-1]:
            if fq in ALLOW:
                m, fn = fq.rsplit(".", 1)
                return any("durable_in_stream" in ast.get_source_segment(self.src[m], n)
                           for n in self.defs.get((m, fn), []))
        return False


def test_no_streamed_body_writes_the_workspace_store():
    s = Scanner(ROOTS, CORE)
    bad = [f"{m}:{line}: " + " -> ".join(ch) for m, line, ch in s.streamed_writes() if not s.allowed(ch)]
    assert not bad, ("a streamed response body reaches a workspace-store write — it would be "
                     "acknowledged by a 200 that already left. Do the write before the response, or "
                     "wrap it in durable_in_stream() and list the function in ALLOW with why:\n  "
                     + "\n  ".join(bad))


def test_every_allow_entry_still_matches_and_wraps_its_write():
    s = Scanner(ROOTS, CORE)
    chains = [ch for _, _, ch in s.streamed_writes()]
    for fq in ALLOW:
        assert any(fq in ch for ch in chains), f"{fq} is no longer on a streamed body's chain: drop it"
        assert any(s.allowed(ch) for ch in chains if fq in ch), f"{fq} must use durable_in_stream()"


def test_the_scanner_catches_a_planted_write(tmp_path):
    pkg = tmp_path / "control_plane"
    (pkg / "routers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "routers" / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text(
        "from workspaces.shared import workspace_paths as wpaths\n"
        "def save(root):\n    wpaths.write_text_inside(root, 'x.json', '{}')\n"
        "def commit(repo):\n    _git(repo, 'commit', '-m', 'x')\n")
    (pkg / "routers" / "stream.py").write_text(
        "from fastapi.responses import StreamingResponse\n"
        "from control_plane import helpers\n"
        "from control_plane.helpers import commit\n"
        "def build():\n"
        "    def gen(root):\n        helpers.save(root)\n        yield b'x'\n"
        "    def gen2(repo):\n        commit(repo)\n        yield b'y'\n"
        "    StreamingResponse(gen('/r'))\n    StreamingResponse(gen2('/r'))\n")
    s = Scanner([pkg], tmp_path)
    found = {ch[-1] for _, _, ch in s.streamed_writes()}
    assert found == {"write_text_inside", "git commit"}


# ── the runtime guard ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def s3_app(tmp_path, monkeypatch):
    from control_plane import workspace_store_service as svc
    from workspaces.store import boot as store_boot
    from workspaces.store.objects import MemoryObjects

    bucket = MemoryObjects(conditional=False)
    run = store_boot.start(str(tmp_path / "cache"), fakeredis.FakeRedis(decode_responses=True),
                           env={"VEXA_WORKSPACE_BACKEND": "s3"}, objects=bucket)
    monkeypatch.setattr(svc, "_running", run)
    yield svc, bucket, tmp_path / "cache"
    run.stop()


def test_a_store_write_after_the_response_began_raises_and_is_not_kept(s3_app):
    from workspaces.shared import workspace_paths as wpaths
    from workspaces.store.observer import durable_in_stream

    svc, bucket, cache = s3_app
    app = FastAPI()
    seen = {}

    @app.get("/stream")
    def stream():
        def body():
            yield b"started\n"
            try:
                wpaths.write_text_inside(cache, ".attached/17/state.json", "late")
            except Exception as exc:  # noqa: BLE001
                seen["error"] = getattr(exc, "reason", type(exc).__name__)
            with durable_in_stream():
                wpaths.write_text_inside(cache, ".attached/17/page.json", "acknowledged by the next event")
            yield b"done\n"
        return StreamingResponse(body())

    @app.post("/before")
    def before():
        wpaths.write_text_inside(cache, ".attached/17/before.json", "fine")
        return {"ok": True}

    svc.install(app, redis_url="", workspaces_dir=str(cache), sync_interval_s=3600)
    client = TestClient(app)
    assert client.get("/stream").text == "started\ndone\n"
    assert seen["error"] == "write_after_response_started"
    assert not (cache / ".attached/17/state.json").exists(), "the refused write is not kept"
    assert bucket.get("files/.attached/17/state.json") is None
    assert bucket.get("files/.attached/17/page.json") == b"acknowledged by the next event"
    assert client.post("/before").status_code == 200
    assert bucket.get("files/.attached/17/before.json") == b"fine", "writes before the response are untouched"


# ── a bare size on a stub: the helpers agent-api uses hydrate first ────────────────────────────
def test_stat_on_a_stub_returns_the_real_size(tmp_path):
    from workspaces.shared import gitexec
    from workspaces.shared import workspace_paths as wpaths
    from workspaces.store import boot as store_boot
    from workspaces.store.lock import WorkspaceLocks
    from workspaces.store.objects import MemoryObjects
    from workspaces.store.repostore import STUB_FLAG, RepoStore

    bucket, redis = MemoryObjects(conditional=False), fakeredis.FakeRedis(decode_responses=True)
    seed = RepoStore(objects=bucket, locks=WorkspaceLocks(redis), cache_root=tmp_path / "seed")
    (seed.path("17")).mkdir(parents=True)
    gitexec.run_git(str(seed.path("17")), "init", "-q", "-b", "main", check=True)
    (seed.path("17") / "page.md").write_text("x" * 1234)
    gitexec.run_git(str(seed.path("17")), "add", "-A", check=True)
    gitexec.run_git(str(seed.path("17")), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
                    "-m", "i", check=True)
    with seed.locks.hold("17") as held:
        seed.publish("17", held=held)
    run = store_boot.start(str(tmp_path / "cache"), redis, env={"VEXA_WORKSPACE_BACKEND": "s3"}, objects=bucket)
    try:
        stub = tmp_path / "cache/17"
        assert (stub / STUB_FLAG).exists()
        assert wpaths.stat_inside(stub, "page.md").st_size == 1234
        assert not (stub / STUB_FLAG).exists(), "the stat hydrated it"
    finally:
        run.stop()


# ── the stat gate: no bare size/mtime read of a work-tree path ─────────────────────────────────
#: Bare stat-family calls that are not on a work-tree path, each with why.
STAT_ALLOW = {
    "agent/control_plane/system_mounts.py::ensure_global_dir":
        "the store root's own `_global` entry (a stale empty FILE left by an old bind), not inside a tree",
    "agent/control_plane/legacy_git_store.py": "the retired .secrets store at the store root, never a tree",
    "workspaces/store/cache.py::tree_bytes": "the store's own cache measuring whole trees (lstat sizes)",
}
_PRIMITIVES = {"agent/shared/gitexec.py", "agent/llm/gitexec.py", "workspaces/shared/gitexec.py",
               "workspaces/shared/workspace_paths.py", "agent/llm/workspace_paths.py"}
_STAT_ATTRS = {"stat", "lstat"}
_STAT_FUNCS = {"getsize", "getmtime", "getctime", "getatime"}


def _stat_sites():
    out = []

    class Visit(ast.NodeVisitor):
        def __init__(self, rel):
            self.rel, self.stack = rel, []

        def _fn(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = _fn

        def visit_Call(self, c):
            if (isinstance(c.func, ast.Attribute) and (c.func.attr in _STAT_ATTRS or c.func.attr in _STAT_FUNCS)
                    and not any(k.arg == "dir_fd" for k in c.keywords)):
                out.append((self.rel, self.stack[0] if self.stack else "<module>", c.lineno))
            self.generic_visit(c)

    for root in (AGENT / "control_plane", AGENT / "shared", CORE / "workspaces"):
        for p in sorted(root.rglob("*.py")):
            rel = p.relative_to(CORE).as_posix()
            if "tests" in p.parts or rel in _PRIMITIVES:
                continue
            Visit(rel).visit(ast.parse(p.read_text()))
    return sorted(set(out))


def test_no_bare_size_or_mtime_of_a_work_tree_path():
    bad = [f"{rel}:{line} in {name}" for rel, name, line in _stat_sites()
           if f"{rel}::{name}" not in STAT_ALLOW and rel not in STAT_ALLOW]
    assert not bad, ("a stat by name outside workspace_paths — on the s3 backend an evicted workspace "
                     "is a stub whose placeholders are empty; use wpaths.stat_inside (it hydrates first), "
                     "or add the function to STAT_ALLOW with why it is not a work tree:\n  "
                     + "\n  ".join(bad))
