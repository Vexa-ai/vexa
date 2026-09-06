"""F96 — the rig writes a workspace through the door that authorizes it, not around it.

`workspace_write` used to run

    docker exec -i vexa-dogfood-agent-api-1 sh -c 'mkdir -p "$(dirname TARGET)" && cat > TARGET'

with the caller's `path` and `slug` interpolated unquoted into TARGET. Two failures in one line:

  * **the shell** — `workspace_write(path="x; id > /tmp/p; #")` executed as root inside the
    container that holds every workspace AND the secret store, reachable by any signed-in user;
  * **the door** — the write went to the volume, and a volume has no membership check, so any
    signed-in user could truncate any other person's or group's file. The READ side had gone
    through agent-api, which confines and authorizes, since the beginning.

Both are the same mistake — reaching around the service that owns the resource — and one fix
answers both: `PUT /api/workspace/file` on the caller's identity. The route applies the mount rules
(contributor+ on a shared workspace, the org-admin allowlist on `_global`), confines the path under
the workspace root, and commits.

Source-level, like `test_rig_stateless.py`: the rig is a standalone server with its own dependency
set, so these tests read it rather than import it. The path validator IS executed — extracted by
`ast` so the assertions run against the shipped function and not a copy of it.
"""
from __future__ import annotations

import ast
import pathlib
import posixpath
import re

import pytest

RIG = pathlib.Path(__file__).resolve().parents[1] / "vexa_control_mcp.py"


def _src() -> str:
    return RIG.read_text()


def _fn(name: str) -> str:
    """The CODE of one top-level function — docstring excluded.

    Excluded deliberately: these functions explain in prose what they no longer do, and a scan that
    cannot tell an explanation from an instruction would either fail on the explanation or force the
    explanation out of the file. The history is the most valuable line in there."""
    src = _src()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            return "\n".join(ast.get_source_segment(src, n) or "" for n in body)
    raise AssertionError(f"{name} is gone from the rig — re-read it before trusting this test")


def _path_validator():
    """Execute the SHIPPED `_safe_ws_path` (plus what it needs) without importing the whole rig."""
    src = _src()
    tree = ast.parse(src)
    wanted = {"_BadPath", "_PATH_FORBIDDEN", "_safe_ws_path"}
    body = []
    for node in tree.body:
        if getattr(node, "name", None) in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) in wanted for t in node.targets):
            body.append(node)
    assert len(body) == 3, f"the path validator moved or changed shape: {[getattr(n, 'name', n) for n in body]}"
    ns: dict = {"posixpath": posixpath}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(RIG), "exec"), ns)  # noqa: S102
    return ns["_safe_ws_path"], ns["_BadPath"]


# ── the door ─────────────────────────────────────────────────────────────────────────────────────

def test_no_write_shells_into_the_container_any_more():
    """The whole class, not the one call site: no `docker exec` may carry a write.

    `docker inspect` on admin-api's env survives — it is a READ of a container's own configuration
    and belongs to the seam inventory the core-mcp work closes, not to this hotfix."""
    src = _src()
    reaches = [ln.strip() for ln in src.splitlines()
               if '"docker", "exec"' in ln or "docker exec -i" in ln]
    assert reaches == [], f"a write still shells into the container: {reaches}"


def test_workspace_write_goes_through_agent_api_on_the_callers_identity():
    body = _fn("workspace_write")
    assert "/api/workspace/file" in body, "the write must use agent-api's own write route"
    assert re.search(r'_http\(\s*"PUT"', body), "the route is a PUT"
    assert '"X-User-Id": uid' in body, (
        "the write must carry the CALLER's identity — that header is what makes the route's "
        "membership check mean anything")
    assert "subprocess" not in body and "docker" not in body
    assert "_safe_ws_path" in body


def test_the_resume_queue_writes_through_the_same_door():
    body = _fn("_write_json")
    assert "/api/workspace/file" in body and '"X-User-Id": uid' in body
    assert "subprocess" not in body and "docker" not in body


# ── the path ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "../../etc/passwd",
    "a/../../etc/passwd",
    "/etc/passwd",
    "..",
    "",
    "   ",
    ".git/config",
    "x; id > /tmp/pwned; #",
    "$(id)",
    "`id`",
    "a|b",
    "a&b",
    "a\nb",
    'a"b',
    "a'b",
])
def test_a_path_that_climbs_or_carries_a_shell_metacharacter_is_refused(path):
    """Refused BEFORE anything runs. The shell is gone, so the metacharacter cases are belt as well
    as braces — deliberately: a validator that only defends against today's implementation defends
    against nothing after the next refactor."""
    safe, bad_path = _path_validator()
    with pytest.raises(bad_path):
        safe(path)


@pytest.mark.parametrize("path,expected", [
    ("kg/entities/person/olga.md", "kg/entities/person/olga.md"),
    ("README.md", "README.md"),
    ("_pending/claims.json", "_pending/claims.json"),
    ("./notes/today.md", "notes/today.md"),
    ("kg/entities/person/José Álvarez.md", "kg/entities/person/José Álvarez.md"),
    ("..notes.md", "..notes.md"),          # a NAME beginning with dots is not an escape
    ("a/.gitignore", "a/.gitignore"),      # `.git` is refused as a ROOT segment, not as a prefix
])
def test_the_paths_a_workspace_actually_holds_are_accepted(path, expected):
    safe, _ = _path_validator()
    assert safe(path) == expected


# ── the other two halves of the same surface (Vexa-ai/vexa#1621) ─────────────────────────────────
#
# `workspace_write` alone is not a write surface, it is the half of one that can only add. Friction
# `fr_a373e9448d2909a6`: told *"remove from personal"*, an agent holding only this verb overwrote
# seven pages of a customer dossier with one-line pointers and reported them as removed. The other
# two verbs go through the SAME door for the same reason F96 gave — the service that owns the
# resource is the one that authorizes it — and that is what these pin.

def test_removing_a_page_goes_through_agent_api_on_the_callers_identity():
    body = _fn("workspace_delete")
    assert "/api/workspace/remove" in body, "the removal must use agent-api's own route"
    assert re.search(r'_http\(\s*"POST"', body), (
        "a POST on its own path, not `DELETE /api/workspace/file` — that URL is also matched by "
        "`DELETE /api/workspace/{slug}`, which destroys a whole workspace")
    assert '"X-User-Id": uid' in body, (
        "the removal must carry the CALLER's identity — that header is what makes the route's "
        "membership check mean anything")
    assert "subprocess" not in body and "docker" not in body
    assert "_safe_ws_path" in body


def test_moving_a_page_goes_through_agent_api_and_validates_BOTH_paths():
    body = _fn("workspace_move")
    assert "/api/workspace/move" in body
    assert re.search(r'_http\(\s*"POST"', body)
    assert '"X-User-Id": uid' in body
    assert "_safe_ws_path(path), _safe_ws_path(to)" in body, (
        "a move has two caller-supplied paths and both are outside input — validating only the "
        "source is the traversal the destination walks through")
    assert "subprocess" not in body and "docker" not in body


def test_the_two_new_verbs_default_their_target_like_the_write_does():
    """`_TARGET_DEFAULTING` (Vexa-ai/vexa#1611): an omitted `slug` on a WRITE means "wherever this
    conversation is working". A removal that defaulted to the desk instead would take a page out of
    a workspace nobody named — the same failure the write had, and worse, because what it removes
    was not put there by this turn."""
    block = _src().split("_TARGET_DEFAULTING = ", 1)[1].split("\n\n", 1)[0]
    assert "workspace_delete" in block and "workspace_move" in block
