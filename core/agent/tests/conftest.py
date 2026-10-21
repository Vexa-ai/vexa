"""Test fixtures — the committed transcript.v1 goldens are the spec (P8).

Goldens are loaded BY PATH from ``meetings/contracts/transcript.v1/golden/`` (the published seam),
never by importing meetings code — the same ``meetings ⊥ agent`` boundary the production code keeps.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from shared.gitenv import GIT_REPO_DISCOVERY_VARS

# The suite runs under `pnpm gates`, which the pre-push HOOK runs — and git exports GIT_DIR (and
# friends) into hook descendants. Any test git subprocess inheriting those operates on the REAL
# repo with its tmp cwd as the work tree: `git add -A && git commit` then REWRITES THE BRANCH BEING
# PUSHED (this destroyed a feature branch once — see shared/gitenv.py). Library code scrubs its own
# child envs; this session-level scrub protects the tests' OWN git helpers (the `subprocess.run
# (["git", ...])` lambdas) wholesale. Tests that need one of these vars set it explicitly
# (monkeypatch.setenv), which happens after this runs at import time.
for _var in GIT_REPO_DISCOVERY_VARS:
    os.environ.pop(_var, None)

# Every switchable feature is ON for the suite (`control_plane/features.py`; the release default is
# off). The tests that are about the switch itself pass their own env.
os.environ.setdefault("VEXA_FEATURES", "all")
# The model price table never reads the network in a test: the bundled snapshot prices every call.
os.environ.setdefault("VEXA_MODEL_PRICES_URL", "off")


def _register_workspaces_package() -> None:
    """Make ``workspaces.shared.<module>`` importable (PRD decision 47, step 2) WITHOUT putting
    ``core/`` on ``sys.path``. control_plane, worker and several tests now read the portable
    workspace primitives (workspace_id/workspace_paths/entities/links) as
    ``workspaces.shared.<module>`` — inside the Docker images this is free (``PYTHONPATH=/app``
    holds only this domain's own tree plus ``workspaces/``, nothing else). Here, run against the
    monorepo checkout, ``core/meetings``, ``core/flows`` etc. sit RIGHT BESIDE ``core/workspaces``,
    so the obvious fix — add ``core/`` to ``pythonpath`` — makes every sibling domain an
    importable (if empty) namespace package too, and ``test_no_meetings_internals_imported``
    exists precisely to catch that: ``import meetings`` must raise, not silently succeed on an
    empty namespace. So: inject ONLY ``workspaces`` into ``sys.modules`` by file location, the
    same by-path mechanism ``contracts/loader.py`` already uses for schemas — no ``sys.path``
    entry ever gains a sibling domain."""
    if "workspaces" in sys.modules:
        return
    marker = Path("meetings/contracts/transcript.v1/golden")
    for parent in Path(__file__).resolve().parents:
        if (parent / marker).is_dir():
            workspaces_dir = parent / "workspaces"
            break
    else:
        raise FileNotFoundError("monorepo root with transcript.v1 goldens not found")
    spec = importlib.util.spec_from_file_location(
        "workspaces", workspaces_dir / "__init__.py",
        submodule_search_locations=[str(workspaces_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["workspaces"] = module
    spec.loader.exec_module(module)


_register_workspaces_package()


def _repo_root() -> Path:
    marker = Path("meetings/contracts/transcript.v1/golden")
    for parent in Path(__file__).resolve().parents:
        if (parent / marker).is_dir():
            return parent
    raise FileNotFoundError("monorepo root with transcript.v1 goldens not found")


def _golden(name: str) -> dict:
    path = _repo_root() / "meetings/contracts/transcript.v1/golden" / name
    return json.loads(path.read_text())


@pytest.fixture
def transcription_golden() -> dict:
    """A confirmed transcript.v1 Transcription envelope (the agent's input)."""
    return _golden("Transcription.confirmed.json")


@pytest.fixture(autouse=True)
def _mandatory_global_workspace(monkeypatch, tmp_path_factory):
    """Dispatch tests obey production's mandatory _global invariant by default."""
    global_dir = tmp_path_factory.mktemp("global-system")
    monkeypatch.setenv("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH", str(global_dir))


#: The unit input key every worker test serves with (shared/unit_input.py): a worker runs only the
#: input entries signed with it, so tests enqueue through :func:`signed_turn`.
TEST_UNIT_IN_KEY = "11" * 32


def signed_turn(body: dict) -> dict:
    """The stream fields agent-api writes for one message to a unit whose key is TEST_UNIT_IN_KEY."""
    from shared import unit_input

    return unit_input.signed_entry(TEST_UNIT_IN_KEY, body)


@pytest.fixture(autouse=True)
def _unit_input_key(monkeypatch):
    monkeypatch.setenv("VEXA_UNIT_IN_KEY", TEST_UNIT_IN_KEY)


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path_factory):
    """Point HOME at a per-test temp dir. The claude-code harness rewires ``$HOME/.claude/projects``
    into the workspace (``_link_chat_into_workspace``) — designed for the disposable per-turn
    container HOME. Run on a host, it operated on the developer's real ``~/.claude`` and once
    destroyed every Claude Code session transcript. No test may ever see the real HOME.

    (Git is unaffected: workspaces set ``user.name``/``user.email`` per-repo, never globally.)"""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture(autouse=True)
def _tool_calling_check_passes(request, monkeypatch):
    """The openai-agent harness's forced tool-calling check (``_check_tool_calling``) passes by default.

    It is one extra request on a turn whose first round offered tools and got none back, and most
    harness tests script exactly that turn against a stub that cannot tell the check from the
    conversation — so they would count, or answer, a request they never meant to see. The tests of
    the check itself opt out with ``@pytest.mark.real_tool_check``."""
    if request.node.get_closest_marker("real_tool_check"):
        return
    try:
        from llm import openai_agent
    except ImportError:                       # a test environment without the llm package
        return
    monkeypatch.setattr(openai_agent.OpenAIAgentHarness, "_check_tool_calling",
                        lambda self, model: None)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_tool_check: run the openai-agent harness's real "
                                       "tool-calling check instead of the conftest's passing stub")
    config.addinivalue_line("markers", "real_principal_resolver: ask identity for a workspace's "
                                       "principal through the real resolver instead of the conftest's")


#: The first id the fake identity gives a workspace principal: far from any person id a test names.
FAKE_PRINCIPAL_BASE = 9_000_000


@pytest.fixture(autouse=True)
def fake_workspace_principals(request, monkeypatch):
    """IDENTITY'S ``principals`` table, in memory: a room's turn runs as its workspace's own principal
    (``VEXA_ROOM_WS_PRINCIPAL``, on by default), and agent-api asks identity for that id
    (``workspace_principal.resolve``). The suite has no identity, so this answers as identity does —
    one stable numeric id per workspace, the first payer it was asked with as ``bills_to`` — and
    returns the table (``{workspace: {"id", "bills_to"}}``) for a test that needs the id. The tests
    of the resolver itself opt out with ``@pytest.mark.real_principal_resolver``."""
    table: dict = {}
    if request.node.get_closest_marker("real_principal_resolver"):
        return table
    from control_plane import workspace_principal

    def resolve(settings, workspace, bills_to, *, post=None):
        ws = str(workspace or "").strip()
        if not ws or not str(bills_to or "").strip():
            return None
        row = table.setdefault(ws, {"id": FAKE_PRINCIPAL_BASE + len(table) + 1, "bills_to": str(bills_to)})
        return row["id"]

    monkeypatch.setattr(workspace_principal, "resolve", resolve)
    return table


@pytest.fixture
def fake_admin_accounts(monkeypatch):
    """Identity's admin-account answer for one test (`tests/admin_accounts.py`): nobody until a
    helper names somebody, and nobody again for the next test."""
    from tests import admin_accounts
    from control_plane import global_layer

    admin_accounts.NAMED.clear()
    monkeypatch.setattr(global_layer, "is_admin_account", admin_accounts.is_admin_account)
    yield admin_accounts
    admin_accounts.NAMED.clear()


@pytest.fixture
def git_broker(monkeypatch):
    """The credential broker's Git store (credential-broker.v1 `/api/internal/git-secret`), in
    memory: what `git_secret_store` talks to. Returns the dict of what the broker holds."""
    from control_plane import git_secret_store

    monkeypatch.setenv("VEXA_GIT_STORE_BROKER_URL", "http://credential-broker:8100")
    monkeypatch.delenv("VEXA_SECRETS_KEY", raising=False)
    values: dict = {}

    def call(name, action, value=None):
        if action == "put" or action == "migrate" and name not in values:
            values[name] = value
        return {"found": name in values, "value": values.get(name)}
    monkeypatch.setattr(git_secret_store, "_call", call)
    return values


@pytest.fixture(autouse=True)
def _home_cards_on_in_tests(monkeypatch):
    """The home's cards are OFF by default for the release (founder 2026-10-15); the suites that
    exercise the composition switch them on, and the switch's own test switches them back off."""
    monkeypatch.setenv("VEXA_HOME_CARDS", "on")


class FakePresenceIdentity:
    """Identity's presence doors, in memory (`control_plane.identity_presence._transport`):
    ``GET /internal/presence?ids=…`` and ``PUT /internal/users/{id}/settings``. Every person answers
    "never chosen" unless ``choices`` says otherwise (``known_only`` makes an unlisted id unknown);
    ``down`` makes identity unreachable, ``refuse_writes`` fails a write."""

    def __init__(self):
        self.choices: dict = {}
        self.reads: list = []
        self.writes: list = []
        self.down = False
        self.refuse_writes = False
        self.known_only = False

    def __call__(self, method, path, body=None):
        from urllib.parse import parse_qs, urlsplit
        if self.down:
            return 0, {}
        parts = urlsplit(path)
        if method == "GET" and parts.path == "/internal/presence":
            ids = [i for i in (parse_qs(parts.query).get("ids") or [""])[0].split(",") if i]
            self.reads.append(ids)
            return 200, {"presence": {i: self.choices.get(i) for i in ids
                                      if i in self.choices or not self.known_only}}
        if method == "PUT" and parts.path.startswith("/internal/users/") and parts.path.endswith("/settings"):
            if self.refuse_writes:
                return 500, {}
            who = parts.path.split("/")[3]
            self.writes.append((who, dict(body or {})))
            if "presence" in (body or {}):
                self.choices[who] = bool(body["presence"])
            return 200, {"presence": self.choices.get(who, True)}
        return 404, {}


@pytest.fixture(autouse=True)
def presence_identity(monkeypatch):
    """Every agent test has an identity that answers presence (a deployment always does); the
    tests about identity being unreachable or unconfigured say so themselves."""
    from control_plane import identity_presence
    fake = FakePresenceIdentity()
    monkeypatch.setattr(identity_presence, "_transport", fake)
    identity_presence.reset()
    yield fake
    identity_presence.reset()


@pytest.fixture(autouse=True)
def _privacy_store():
    """The privacy mark and the held store (D6) over a fresh fake Redis per test: the mark is
    persisted in production, and a test never shares one with another. A test about the store being
    absent or unreadable installs its own."""
    import fakeredis

    from control_plane import held_store
    from shared import taint

    client = fakeredis.FakeRedis(decode_responses=True)
    taint.configure(taint.TaintStore(client))
    held_store.configure(held_store.HeldStore(client, "test-internal-secret"))
    yield client
    taint.configure(None)
    held_store.configure(None)


# ── WS-S3: run any part of the suite with the s3 workspace backend's write path switched on ─────
# VEXA_TEST_WORKSPACE_BACKEND=s3 installs the durable-store ref observer for the whole session over
# pytest's temp root: every ref-moving git command any test makes, in any repository it creates,
# takes the workspace lock, checks freshness and publishes a generation to an in-memory bucket before
# `run_git` returns, and every file written outside a repository through workspace_paths is PUT — so a deny test that still passes proves the refusal holds on the s3 write path.
@pytest.fixture(scope="session", autouse=True)
def _s3_workspace_backend(tmp_path_factory):
    if os.environ.get("VEXA_TEST_WORKSPACE_BACKEND") != "s3":
        yield None
        return
    from workspaces.store.client import install_observer, install_path_observer
    from workspaces.store.lock import WorkspaceLocks
    from workspaces.store.objects import MemoryObjects
    from workspaces.store.observer import CacheSync, StoreObserver
    from workspaces.store.repostore import RepoStore

    store = RepoStore(objects=MemoryObjects(conditional=False), locks=WorkspaceLocks(None),
                      cache_root=Path(os.path.realpath(tmp_path_factory.getbasetemp())))
    observer = StoreObserver(store, sync=CacheSync(store))   # writes outside git published too
    install_observer(observer)
    install_path_observer(observer)
    yield store
    install_observer(None)
    install_path_observer(None)
    assert store.repos(), "the s3 run published nothing: the observer was not on the write path"
