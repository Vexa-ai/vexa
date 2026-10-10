"""No path in agent-api, the worker or the workspace primitives is opened, listed, created, renamed
or removed BY NAME, except through ``workspace_paths`` or for a reason written down here.

These processes run as root over work trees the model's tools can write, and acting on a path by
name follows a link at the leaf and at every directory on the way — the class
``test_worktree_links_refused.py`` closes one site at a time. ``workspace_paths`` (vendored as
``llm.workspace_paths``, parity fact ``workspace-paths``) is the one place that reaches a work-tree
path, by descriptor and nofollow; ``gitexec`` is the other primitive (its own private directory, and
the repository directory it pins).

This scans the source of ``core/agent`` and ``core/workspaces`` (tests excepted) for, outside those
two:
* an open or read/write by name — ``open()``, ``Path.open``/``read_text``/``write_text``/
  ``read_bytes``/``write_bytes``;
* a removal, rename or creation by name — ``unlink``/``remove``/``rmdir``/``removedirs``, ``rename``/
  ``replace``/``renames``/``shutil.move``, ``mkdir``/``makedirs``/``touch``, ``symlink``/``link``;
* a listing by name — ``iterdir``/``glob``/``rglob``, ``os.listdir``/``os.scandir``/``os.walk``,
  ``glob.glob``;
* ``shutil`` ``rmtree``/``copytree``/``copy2``/``copyfile``.

A call made relative to a directory descriptor (``dir_fd=``, ``src_dir_fd=``, ``dst_dir_fd=``) is
not by name and is not reported. Changes of ownership or mode are NOT scanned: the one root-side
place that changes them under a work tree (``llm.ports`` grant, show and git-keeping) does it on
descriptors, after a descriptor walk, and its tests swap an entry for a link to prove it.

Each allowed entry names one function and why what it touches is not a work tree. An entry that no
longer matches anything fails, so the list cannot rot into a blanket exemption; no entry exempts a
whole module. The scanner proves it is not vacuous by catching every shape in a planted sample.
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
#: The two descriptor primitives, skipped whole: the helper, and gitexec (R5-1), whose only paths
#: are its own private directory and the repository directory it has pinned.
PRIMITIVES = HELPER | {AGENT / "shared" / "gitexec.py", AGENT / "llm" / "gitexec.py",
                       CORE / "workspaces" / "shared" / "gitexec.py"}
_SKIP_DIRS = {"tests", ".venv", "node_modules", "__pycache__"}

#: Trees outside the serving processes' reach of a work tree, skipped whole.
OUT_OF_SCOPE = {
    "agent/services": "separate services (the credential broker) with no workspace store mounted",
    "agent/eval": "benches and replays a developer runs by hand over fixtures, never in a server",
    "agent/contracts": "the contract schemas and their loader, read from the image",
}

_OPERATOR = "an operator-mounted key, token, credential or config file — never under the store"
_IMAGE = ("a file or folder this image ships (a manifest, a policy, a tool definition, a seed, the "
          "preset library, the platform skills)")
_STORE = ("control-plane state at the store's top level or in its root-owned dot-directories "
          "(.secrets, .attached, .attached-shared, .imports, .invites, .routines, .system, "
          ".onboarding-research): whole workspace trees moved, made or listed as units, never "
          "anything inside one a tool can write")
_PROCESS = "the process's own temp directory or container log — never under the store"
_HOME = ("the worker's own HOME (~/.claude, ~/.vexa-skills and its stages): the tools user may read "
         "it, never write it (`ports.show_tools`)")
_DESCRIPTOR = ("lists through a folder descriptor opened without following a link "
               "(`workspace_paths.open_dir_at` / `dir_fd_inside`), and writes only into the "
               "worker's own skill stage")
_URL_OPEN = "opens a URL through an urllib opener, not a file"
_NAMES_ONLY = ("lists names only; nothing listed is opened by name — each is located and checked "
               "(a plain file reached without a link), or removed through `unlink_inside`")


def _entries(reason: str, *keys: str) -> dict:
    return {k: reason for k in keys}


#: ``<path under core/>::<function qualname>`` → why what it touches is not a work tree.
ALLOW = {
    "agent/shared/meeting_bundle_codec.py::_read_bounded":
        "opens a member of an in-memory zip archive (zipfile.ZipFile.open), never a file by name",
    **_entries(_OPERATOR,
               "agent/control_plane/broker_assertion.py::load_key",
               "agent/control_plane/config_preflight.py::load_declaration",
               "agent/control_plane/config_preflight.py::_file_probe",
               "agent/control_plane/config_test.py::test_subscription_credentials",
               "agent/control_plane/identity_token.py::_read",
               "agent/control_plane/identity_token.py::_write"),
    **_entries(_IMAGE,
               "agent/control_plane/route_policy.py::load",
               "agent/control_plane/routers/health.py::build.mcp_tools_manifest",
               "agent/control_plane/scaffolds.py::read_preset",
               "agent/control_plane/global_seed.py::_shippable",
               "agent/control_plane/preset_library.py::_shippable",
               "agent/shared/seeding.py::list_templates",
               "agent/shared/seeding.py::_seed_pairs",
               "agent/shared/tools.py::ToolRegistry.from_dir",
               "agent/shared/meeting_bundle_codec.py::_schema",
               "agent/worker/engine.py::<module>"),
    **_entries(_STORE,
               "agent/control_plane/secret_store.py::read_key",
               "agent/control_plane/secret_store.py::master_key",
               "agent/control_plane/secret_store.py::put",
               "agent/control_plane/secret_store.py::_envelope",
               "agent/control_plane/secret_store.py::delete",
               "agent/control_plane/git_secret_store.py::get",
               "agent/control_plane/git_secret_store.py::put",
               "agent/control_plane/git_credentials.py::read_github_token",
               "agent/control_plane/git_credentials.py::set_github_token",
               "agent/control_plane/workspace_attach.py::_git_clone",
               "agent/control_plane/workspace_attach.py::_load_state",
               "agent/control_plane/workspace_attach.py::_save_state",
               "agent/control_plane/workspace_attach.py::_plain_state",
               "agent/control_plane/workspace_attach.py::swap_workspace",
               "agent/control_plane/workspace_attach.py::create_shared_workspace_dir",
               "agent/control_plane/workspace_attach.py::ensure_workspace_shareable",
               "agent/control_plane/workspace_attach.py::ensure_workspace_private",
               "agent/control_plane/workspace_attach.py::activate_workspace",
               "agent/control_plane/workspace_attach.py::create_workspace",
               "agent/control_plane/workspace_attach.py::_build_attached",
               "agent/control_plane/workspace_attach.py::_park",
               "agent/control_plane/workspace_attach.py::_backup_default",
               "agent/control_plane/workspace_attach.py::attach_repo_at",
               "agent/control_plane/workspace_ids.py::migrate",
               "agent/control_plane/workspace_import.py::_store",
               "agent/control_plane/workspace_import.py::status",
               "agent/control_plane/workspace_import.py::start",
               "agent/control_plane/meeting_bundle.py::restore",
               "agent/control_plane/workspace_membership.py::read_invites",
               "agent/control_plane/workspace_membership.py::_write_invites",
               "agent/control_plane/workspace_membership.py::drop_invites",
               "agent/control_plane/workspace_membership.py::workspaces_with_invites",
               "agent/control_plane/workspace_membership.py::list_memberships",
               "agent/control_plane/workspace_routines.py::_read_approvals",
               "agent/control_plane/workspace_routines.py::_write_approvals",
               "agent/control_plane/workspace_routines.py::_record_existing_approvals",
               "agent/control_plane/workspace_routines.py::scan_workspace_subjects",
               "agent/control_plane/routine_resign.py::resign_unsigned_routines",
               "agent/control_plane/onboarding_research.py::Research.locked",
               "agent/control_plane/onboarding_research.py::Research._write_audit",
               "agent/control_plane/system_mounts.py::ensure_global_dir",
               "agent/control_plane/system_mounts.py::ensure_system_workspace",
               "agent/shared/atomic_json.py::write_json_atomic",
               "agent/shared/adapters.py::workspace_write_lock",
               "agent/shared/adapters.py::RealGitWorkspace.clone",
               "agent/shared/seeding.py::seed_workspace"),
    **_entries(_PROCESS,
               "agent/control_plane/deploy_keys.py::fingerprint",
               "agent/control_plane/deploy_keys.py::ensure",
               "agent/control_plane/deploy_keys.py::ssh_env",
               "agent/worker/friction.py::report"),
    **_entries(_HOME,
               "agent/llm/claude_code.py::clear_deployment_credential",
               "agent/llm/claude_skills.py::_link_skills_into_home",
               "agent/llm/claude_skills.py::_stage_workspace_skill"),
    **_entries(_DESCRIPTOR,
               "agent/llm/claude_skills.py::_assemble_skills",
               "agent/llm/claude_skills.py::_copy_entries"),
    **_entries(_URL_OPEN,
               "agent/control_plane/config_test.py::_post",
               "agent/control_plane/config_test.py::_get"),
    **_entries(_NAMES_ONLY,
               "agent/llm/openai_agent.py::run_builtin",
               "agent/llm/ports.py::_current_policy_entries"),
}

_PATH_ATTRS = {"read_text", "write_text", "read_bytes", "write_bytes", "unlink", "rmdir", "rename",
               "mkdir", "iterdir", "glob", "rglob", "symlink_to", "hardlink_to"}
_OS_FUNCS = {"unlink", "remove", "rmdir", "removedirs", "rename", "renames", "replace", "mkdir",
             "makedirs", "listdir", "scandir", "walk", "symlink", "link"}
_SHUTIL_FUNCS = {"rmtree", "copytree", "copy2", "copyfile", "move"}
_DESCRIPTOR_KEYWORDS = {"dir_fd", "src_dir_fd", "dst_dir_fd"}
#: ``<module>.open`` that opens no file by path name.
_NOT_A_FILE_OPEN = {"webbrowser", "tarfile"}


def _what(call: ast.Call) -> str:
    """The by-name file operation ``call`` makes, or ``""``."""
    if any(k.arg in _DESCRIPTOR_KEYWORDS for k in call.keywords):
        return ""                                        # relative to a descriptor: not by name
    f = call.func
    if isinstance(f, ast.Name):
        return f.id if f.id in {"open"} | _SHUTIL_FUNCS else ""
    if not isinstance(f, ast.Attribute):
        return ""
    mod = f.value.id if isinstance(f.value, ast.Name) else None
    if mod == "os":
        return f"os.{f.attr}" if f.attr in _OS_FUNCS else ""
    if mod == "shutil":
        return f"shutil.{f.attr}" if f.attr in _SHUTIL_FUNCS else ""
    if mod == "glob":
        return f"glob.{f.attr}" if f.attr in ("glob", "iglob") else ""
    if mod == "io" and f.attr == "open":
        return "io.open"
    if mod in _NOT_A_FILE_OPEN:
        return ""
    if f.attr in _PATH_ATTRS:
        return f.attr
    if f.attr == "open":
        return ".open"
    # `Path.replace(target)` / `Path.touch()` — not `str.replace(old, new)` / a method with arguments
    if f.attr == "replace" and len(call.args) == 1 and not call.keywords:
        return ".replace"
    if f.attr == "touch" and not call.args:
        return ".touch"
    return ""


def bare_io_calls(source: str, filename: str = "<src>") -> list[tuple[str, int, str]]:
    """Every by-name file operation in ``source``, as ``(enclosing qualname, line, what)``."""
    found: list[tuple[str, int, str]] = []

    def visit(node: ast.AST, stack: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = (*stack, node.name)
        if isinstance(node, ast.Call):
            what = _what(node)
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
            if set(path.relative_to(root).parts) & _SKIP_DIRS or path in PRIMITIVES:
                continue
            if any(rel.startswith(d + "/") for d in OUT_OF_SCOPE):
                continue
            yield path, rel


PLANTED = '''
import glob, io, os, shutil
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
def remover(ws):
    (ws / "j").unlink()
    os.unlink(ws / "k")
    os.remove(ws / "l")
    (ws / "m").rmdir()
    (ws / "n").rename(ws / "o")
    os.replace(ws / "p", ws / "q")
    (ws / "r").replace(ws / "s")
    shutil.move(ws / "t", ws / "u")
def maker(ws):
    (ws / "v").mkdir(parents=True)
    os.makedirs(ws / "w")
    (ws / "x").touch()
    (ws / "y").symlink_to("/etc")
def lister(ws):
    list((ws / "z").iterdir())
    list(ws.glob("*.md"))
    list(ws.rglob("*"))
    os.listdir(ws)
    os.scandir(ws)
    os.walk(ws)
    glob.glob(str(ws / "*"))
'''


def test_the_scanner_catches_every_shape():
    hits = bare_io_calls(PLANTED)
    lines = {line for _q, line, _w in hits}
    assert lines == {6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28,
                     30, 31, 32, 33, 35, 36, 37, 38, 39, 40, 41}, sorted(lines)
    assert ("Writer.write", 15, "shutil.rmtree") in hits and ("reader", 6, "open") in hits
    assert ("remover", 27, ".replace") in hits and ("lister", 39, "os.scandir") in hits


def test_the_scanner_lets_the_helper_and_descriptor_calls_through():
    ok = '''
import os
from workspaces.shared import workspace_paths as wpaths
def f(ws, dfd):
    wpaths.read_text_inside(ws, "a")
    wpaths.write_text_inside(ws, "b", "x")
    wpaths.remove_tree(ws / "c")
    wpaths.unlink_inside(ws, "d")
    fd = os.open("d", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=3)
    os.unlink("e", dir_fd=dfd)
    os.replace("f", "g", src_dir_fd=dfd, dst_dir_fd=dfd)
    os.mkdir("h", 0o755, dir_fd=dfd)
    "a-b".replace("-", "_")
    log.touch(1, 2)
    with os.fdopen(fd, "rb") as fh:
        fh.read()
'''
    assert bare_io_calls(ok) == []


def test_no_work_tree_path_is_acted_on_by_name_outside_workspace_paths():
    offenders = []
    for path, rel in _sources():
        for qualname, line, what in bare_io_calls(path.read_text(encoding="utf-8"), rel):
            if f"{rel}::{qualname}" not in ALLOW:
                offenders.append(f"{rel}:{line} {what} in {qualname}")
    assert offenders == [], (
        "a path acted on by name outside workspace_paths — if it can be under a work tree, go "
        "through workspace_paths (read_text_inside, write_text_inside, unlink_inside, "
        "list_files_inside, walk_files_inside, dir_fd_inside, remove_tree, …); if it cannot, add "
        "the function to ALLOW with the reason:\n  " + "\n  ".join(offenders))


def test_every_allow_entry_still_names_a_by_name_call():
    used = set()
    for path, rel in _sources():
        for qualname, _line, _what in bare_io_calls(path.read_text(encoding="utf-8"), rel):
            key = f"{rel}::{qualname}"
            if key in ALLOW:
                used.add(key)
    stale = sorted(set(ALLOW) - used)
    assert stale == [], f"allow-list entries that no longer match a by-name call — remove them: {stale}"


def test_no_entry_exempts_a_whole_module():
    assert not [k for k in ALLOW if k.endswith("::*")]


@pytest.mark.parametrize("copy", sorted(HELPER - {CORE / "workspaces" / "shared" / "workspace_paths.py"}))
def test_the_vendored_helper_is_the_canonical_bytes(copy):
    assert copy.read_bytes() == (CORE / "workspaces" / "shared" / "workspace_paths.py").read_bytes()
