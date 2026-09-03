"""The rig's workspace tools have nowhere to put a secret, and refuse one on the way in.

The credential MODEL — which credential, in what order, and what an agent may be told — is
`core/agent`'s and is asserted there (`core/agent/tests/test_workspace_credentials.py`). These
three read THE RIG'S OWN SOURCE, so they live beside it: a test under `core/` that parses a file
under `deploy/` makes the domains and the lane inseparable.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parents[1] / "vexa_control_mcp.py"


# ── the rig's own tools: nowhere to put a secret, and a refusal on the way in ──────────────────────

def _rig_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(RIG.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_the_rig_workspace_tools_have_no_credential_parameter():
    """`token` on a rig tool is the caller's VEXA session token (the identity convention every tool in
    that file uses) — there must be no git-credential parameter beside it for an agent to fill in."""
    banned = {"pat", "github_token", "repo_token", "password", "ssh_key", "private_key", "credential"}
    fns = _rig_functions()
    for name in ("workspace_attach", "workspace_push", "workspace_pull"):
        assert name in fns, f"{name} is missing from the rig"
        params = {a.arg for a in fns[name].args.args}
        assert not (params & banned), f"{name} exposes a credential parameter: {params & banned}"


def test_the_rig_refuses_a_credential_smuggled_into_an_argument():
    """The rig's detector, executed as it actually ships (parsed out of the file, so this cannot drift
    from the deployed code) — a PAT typed into the repo URL is refused with a sentence that points at
    the key instead of asking again."""
    tree = ast.parse(RIG.read_text(encoding="utf-8"))
    wanted = ("_CREDENTIAL_REFUSAL", "_refuse_credentials", "_OUR_CREDENTIAL_PREFIXES")
    picked = [n for n in tree.body
              if (isinstance(n, ast.FunctionDef) and n.name in wanted)
              or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in wanted for t in n.targets))]
    assert len(picked) == 3, "the rig's refusal helper moved — this test must follow it"
    # The detector now IMPORTS the scrubber instead of re-implementing it (R-D14: the copy had
    # drifted past `glpat-`, the generic run, and bare-userinfo URLs). `rig_secrets` re-exports
    # `shared.git_redaction`, so the lifted-out copy needs it in its namespace.
    ns: dict = {"rig_secrets": _rig_secrets()}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(RIG), "exec"), ns)  # noqa: S102

    refuse = ns["_refuse_credentials"]
    assert refuse("https://ghp_AAAAAAAAAAAAAAAAAAAA@github.com/acme/kg.git")
    assert refuse("git@github.com:acme/kg.git", "main", "", "ghp_BBBBBBBBBBBBBBBBBBBB")
    assert refuse("https://user:secret@github.com/acme/kg.git")
    assert refuse("https://glpat-AAAAAAAAAAAAAAAAAAAA@gitlab.com/acme/kg.git"), \
        "the drifted six-prefix copy is back — a GitLab PAT in a remote URL walked through it"
    assert refuse("git@github.com:acme/kg.git", "main", "grp-a1b2c3", "") == ""
    assert "will not take a token in chat" in ns["_CREDENTIAL_REFUSAL"]


def _rig_secrets():
    """The rig's own module, imported the way the rig imports it."""
    import importlib
    import sys
    rig_dir = str(RIG.parent)
    if rig_dir not in sys.path:
        sys.path.insert(0, rig_dir)
    return importlib.import_module("rig_secrets")


def test_the_rig_calls_that_refusal_before_it_does_anything():
    src = RIG.read_text(encoding="utf-8")
    fns = _rig_functions()
    for name in ("workspace_attach", "workspace_push", "workspace_pull"):
        body = ast.get_source_segment(src, fns[name]) or ""
        assert "_refuse_credentials(" in body, f"{name} does not screen its arguments"
