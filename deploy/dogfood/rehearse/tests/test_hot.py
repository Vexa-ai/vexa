"""NO STATE NEEDS AN IMAGE — PRD decision 38.4, asserted rather than promised.

    "presets and mail templates are read hot from `_global`, flow versions load hot; the tool
     writes data only. Code changes still swap; a state change never does."

A README claiming this would go stale the first time somebody reached for `docker` to make one
awkward state work — which is exactly how a rig acquires a build step. So the claim is a test over
the source: every verb's door is a running service, no recipe writes a preset or a template, and
the only subprocess calls in the whole package are the three named exceptions in `doors.py`'s
module docstring.
"""
from __future__ import annotations

import ast
import pathlib
import re

from rehearse import catalogue as cat

PKG = pathlib.Path(__file__).resolve().parent.parent

#: Everything a recipe may talk to. Each is a service that is ALREADY RUNNING, or the mail double.
#: Nothing here is a build step, an image tag, or a compose file.
RUNNING_SERVICES = {"admin-api", "agent-api", "gateway", "flows-api", "terminal", "smtp",
                    "mailpit"}

#: The ONLY functions in the package allowed to shell out, and why. `doors.py`'s docstring lists
#: the same three; this set is the enforced half. Adding a fourth means writing the reason in both
#: places, which is the point — the cost of the exception should be visible.
MAY_SHELL_OUT = {
    "live_meetings",        # the fail-closed guard: no instance-wide live-meeting route exists
    "_redis_cli", "_redis", "_redis_del",   # per-subject session/scaffold keys agent-api owns
    "friction_delete_for", "_flow_lanes",   # per-subject friction rows no route removes
    "lane_rows_delete_for",                 # per-subject reactions/receipts/threads, ditto
    "_admin_key",           # reading this deployment's own admin token, when it is not in the env
}


def test_every_door_a_recipe_uses_is_a_service_that_is_already_running():
    assert {v.door for v in cat.VERBS.values()} <= RUNNING_SERVICES


def test_no_recipe_writes_a_preset_a_mail_template_or_the_company_layer():
    """`_global` is read at click time and written by the admin, in the product. A state that
    edited a preset would be changing the deployment to enter a state, which is a swap wearing a
    recipe's clothes.

    Asserted over the PARSED recipe, never over the file's text: a prose line in `artefacts` that
    happens to contain the word "composed" is not a compose file, and a rule that cannot tell the
    difference gets deleted the first time it cries wolf.
    """
    for name, st in cat.load().states.items():
        for step in st.steps:
            said = " ".join(str(v) for v in step.args.values())
            for forbidden in ("_global", "asks/", "mail/", "docker", "compose", "image"):
                assert forbidden not in said, f"{name} step {step.index} names {forbidden!r}"


def test_the_package_never_builds_runs_or_swaps_an_image():
    for py in sorted(PKG.glob("*.py")):
        src = py.read_text()
        for forbidden in ("docker build", "docker compose", "docker run", "up -d", "docker rm",
                          "docker restart", "docker stop", "IMAGE_TAG"):
            assert forbidden not in src, f"{py.name} reaches for {forbidden!r}"


def test_only_the_three_named_exceptions_shell_out():
    """Every other write goes through a route or SMTP. This is the invariant the whole package
    exists to keep, so it is asserted structurally rather than by reading."""
    tree = ast.parse((PKG / "doors.py").read_text())
    offenders = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        src = ast.dump(node)
        if "subprocess" in src and node.name not in MAY_SHELL_OUT:
            offenders.add(node.name)
    assert not offenders, (
        f"{sorted(offenders)} shell out. A state is entered through the product's own doors; if a "
        f"new exception is genuinely needed, name it in doors.py's docstring AND in MAY_SHELL_OUT "
        f"so the cost stays visible.")


def test_nothing_writes_the_database_or_the_workspace_volume():
    src = (PKG / "doors.py").read_text()
    assert "INSERT" not in src and "UPDATE " not in src
    # One DELETE, and it is the per-subject friction rows the docstring names.
    # The per-subject lane tables the reset owns — named, in FK order, and nothing else. The
    #  `{table}` is `LANE_TABLES`, which the docstring above it lists and bounds.
    deletes = sorted(set(re.findall(r"DELETE FROM (\{?\w+\}?)", src)))
    assert deletes == ["friction", "{table}"], deletes
    assert "cat > /workspaces" not in src and "/workspaces/" not in src


def test_the_only_flows_writes_are_facts_never_definitions():
    """`flows_submit` / `flow_lifecycle` rewrite what the whole instance reacts to. A rehearsal
    injects FACTS and nothing else — decision 38.4's "the tool writes data only", enforced.

    Matched on the ROUTE and the verb names, not on the substring "/flows": the flows-api key file
    is `~/.storm/flows-api-key`, and a rule that reads a key path as a definition write is a rule
    that gets deleted for crying wolf.
    """
    src = (PKG / "doors.py").read_text()
    for definition_verb in ("flows_submit", "flow_lifecycle", "/flows/", "/flows\"", "/flows}"):
        assert definition_verb not in src, definition_verb
    assert "/events" in src, "the fact intake is the only flows door a rehearsal uses"
