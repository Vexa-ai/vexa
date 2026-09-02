"""THE THIN-FORWARD RULE, enforced on the AST — the test that stops this file regrowing.

`deploy/dogfood/rig/vexa_control_mcp.py` was 5,033 lines with 64 tools and no tests. It did not get
there by anyone deciding to write a 5,000-line file; it got there one reasonable-looking addition at
a time — a docker exec "just for now", a psycopg read "because there is no route", a sys.path insert
"because the module is right there". Each was locally sensible and none of them failed loudly. What
was missing was a rule with teeth.

This is the rule. A TOOL BODY MAY ONLY: build a request, call the HTTP client, and shape the
response. Concretely, and this is what the four checks below assert:

  1. NO REACH BUT HTTP — no ``subprocess``, no ``docker``, no ``psycopg``/``psql``, no ``sys.path``
     mutation, anywhere in the tools package. These are the four mechanisms that made the rig
     un-packageable (seam inventory B6): they need a docker socket, two hardcoded container names,
     a Postgres URL, and source checkouts of two other trees on the filesystem.
  2. ONE SERVICE PER TOOL — a tool names AT MOST ONE service base URL. A tool that talks to two
     services is a composition, and a composition belongs in the service that owns the composite.
  3. NO WRITE OUTSIDE ``VEXA_HOME`` — a tool may write the small state this edge holds (a token it
     was handed, a fixture it just converted) and nothing else.
  4. A BUDGET, AND NO PRODUCT COPY — a tool body stays under a statement budget, and carries no
     string literal over 400 characters that is not its own docstring. Product copy belongs in
     ``_global``, read hot, where an admin can change it without a deploy — never in an image.

ALLOWLIST, NOT EXCEPTIONS. Every relaxation below is one entry naming the tool and the reason, in
the shape ``gateDbBudget`` uses. An entry is a piece of backlog with a test attached, and it is
supposed to be uncomfortable to add one.
"""
from __future__ import annotations

import ast
import pathlib

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "vexa_mcp" / "tools"

FORBIDDEN_IMPORTS = {"subprocess", "docker", "psycopg", "psycopg2", "sh", "pexpect", "sqlalchemy"}
FORBIDDEN_ATTR_ROOTS = {"subprocess", "psycopg", "psycopg2", "docker"}
SERVICE_URLS = {"AGENT_API", "ADMIN_API", "FLOWS_API", "GATEWAY", "MAILPIT", "UI_BASE"}
# Narrow on purpose: these are pathlib/file writes. `replace` and `rename` are excluded because
# they are also string and datetime methods, and a check that fires on `isoformat().replace()` is a
# check nobody trusts.
WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch"}
# Names a write may be rooted at: the edge's own state directory and the files inside it.
WRITABLE_ROOTS = {"CAPS_DIR", "VEXA_HOME", "TOKENS_FILE", "USER_KEYS_FILE", "EMAIL_CODES",
                  "LOGINS", "REGIMES", "REVOKED_FILE", "FRICTION_LOG"}

STATEMENT_BUDGET = 45

# tool → why it is over budget / off the rule. Each entry is BACKLOG, and the reason says whose.
ALLOW_BUDGET = {
    # A multi-step import against ONE service (create the row, then import the transcript onto it),
    # plus the date arithmetic that decides the occurrence window. Both gateway calls; the arithmetic
    # is what the route would need as an argument either way.
    "meeting_seed": "two gateway calls plus the occurrence-window arithmetic (PRD decision 38 double)",
    # Two independent transcript parsers. They read a file the caller already has and write segments
    # under VEXA_HOME; there is no service that takes an unparsed caption track today, so there is no
    # route to forward to. BACKLOG: a `POST /meetings/{id}/transcript-import` that accepts the raw
    # formats would delete both.
    "captions_to_segments": "a local caption parser: no service takes an unparsed track (backlog)",
    "zoom_transcript_to_segments": "a local transcript parser: same gap as captions_to_segments",
    # The URL grammar. It reaches no service at all — it composes a link out of the terminal base and
    # a validated preset NAME. BACKLOG: `flows_steps/common.py:ui_link` mints the same grammar
    # (seam inventory B5 row 3); one minter needs a module both images can import.
    "deeplink": "the deeplink URL grammar — second minter, unified when a shared module exists",
    # Meeting-ref parsing, duplicate-bot detection and the three state sentences. One service.
    "bot_send": "one gateway create plus one status read, and the three state sentences",
    # The `since` cursor, tail windowing and the live/finished branch. One service.
    "meeting_transcript": "one gateway read plus cursor/tail windowing",
    # Time parsing in the person's own clock, then one fact through the flows intake.
    "bot_schedule": "at_local parsing in the person's zone, then the flows intake",
    # Argument shaping and the links-missing follow-up around one agent-api route.
    "entity_upsert": "argument shaping around POST /api/workspace/entity",
    # The sign-in pair. BACKLOG: the code store and the durable token belong in admin-api, which
    # owns identity; that needs a schema change and is not this change.
    "start_onboarding": "the sign-in code store is still the edge's own (identity backlog)",
    "confirm_login": "the sign-in code store is still the edge's own (identity backlog)",
    "auth_claim": "the one-time login handle store is still the edge's own (identity backlog)",
}
ALLOW_TWO_SERVICES = {
    # Creates the account on admin-api, then seeds the workspace on agent-api. BACKLOG: this is one
    # act — "sign this person up" — and it belongs behind one admin-api route that does both, which
    # is the same identity backlog the two entries above name.
    "start_onboarding": "admin-api account create + agent-api workspace init (identity backlog)",
}
ALLOW_BIG_LITERAL = {
    # whats_waiting's welcome copy is GONE from here (it moved to agent-api with the queue), and the
    # rest of these are the agent-facing instructions a result carries, not product copy shown to a
    # person. BACKLOG: PRD §3.2 wants them resolved from `_global` per call.
    "auth_claim": "the persist/skill instructions handed to the AGENT, not to the person",
    "confirm_login": "the persist/skill instructions handed to the AGENT, not to the person",
}


def _tools_in(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any(
                isinstance(d, ast.Name) and d.id == "tool" for d in node.decorator_list):
            yield node


def _all_tools():
    for f in sorted(TOOLS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        for node in _tools_in(f):
            yield f, node


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.value if not isinstance(node, ast.Call) else node.func
    return node.id if isinstance(node, ast.Name) else None


def test_no_forbidden_reach_anywhere_in_the_tools_package():
    """1. The four mechanisms that made the rig un-packageable, gone and staying gone."""
    bad = []
    for f in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        bad.append(f"{f.name}:{node.lineno} imports {a.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                    bad.append(f"{f.name}:{node.lineno} imports from {node.module}")
            elif isinstance(node, ast.Attribute):
                root = _root_name(node)
                if root in FORBIDDEN_ATTR_ROOTS:
                    bad.append(f"{f.name}:{node.lineno} touches {root}.{node.attr}")
                if root == "sys" and node.attr == "path":
                    bad.append(f"{f.name}:{node.lineno} mutates sys.path")
    assert not bad, (
        "a tool module reached past HTTP — this is exactly how the rig became unpackageable:\n  "
        + "\n  ".join(bad))


def test_each_tool_names_at_most_one_service():
    """2. A tool talks to ONE service. Two is a composition, and a composition has an owner."""
    bad = []
    for f, node in _all_tools():
        seen = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in SERVICE_URLS:
                seen.add(sub.id)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id == "_gw_http":
                    seen.add("GATEWAY")
        seen.discard("UI_BASE")           # a link is not a call
        if len(seen) > 1 and node.name not in ALLOW_TWO_SERVICES:
            bad.append(f"{f.name}:{node.name} reaches {sorted(seen)}")
    assert not bad, (
        "a tool fanned out across services — move the composition into the service that owns the "
        "composite and forward to it:\n  " + "\n  ".join(bad))


def _writable_locals(node) -> set:
    """Local names assigned from something rooted at the edge's state directory.

    `out = config.CAPS_DIR / f"{video_id}.segments.json"` makes `out` writable; nothing else does.
    Without this the check either misses every real write (they all go through a local) or fires on
    all of them."""
    names = set(WRITABLE_ROOTS)
    for _ in range(3):                      # a short fixpoint: b = a / x; c = b / y
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                src = ast.dump(sub.value)
                if any(w in src for w in names):
                    names.add(sub.targets[0].id)
    return names


def test_no_filesystem_write_outside_the_edge_state_dir():
    """3. A tool writes the edge's own state or nothing."""
    bad = []
    for f, node in _all_tools():
        WRITABLE = _writable_locals(node)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr in WRITE_CALLS:
                    src = ast.dump(sub.func.value)
                    if not any(w in src for w in WRITABLE):
                        bad.append(f"{f.name}:{sub.lineno} {node.name} writes outside VEXA_HOME")
                if sub.func.attr == "open":
                    mode = next((a.value for a in sub.args
                                 if isinstance(a, ast.Constant) and isinstance(a.value, str)), "r")
                    src = ast.dump(sub.func.value)
                    if any(m in mode for m in "wax") and not any(w in src for w in WRITABLE):
                        bad.append(f"{f.name}:{sub.lineno} {node.name} opens a write outside VEXA_HOME")
    assert not bad, "\n  ".join(["a tool wrote outside the edge's state directory:"] + bad)


def test_tool_bodies_stay_within_budget():
    """4a. A statement budget, ratcheted by lowering the number — never by adding an allowlist row
    without a reason."""
    over = []
    for f, node in _all_tools():
        stmts = sum(1 for s in ast.walk(node) if isinstance(s, ast.stmt))
        if stmts > STATEMENT_BUDGET and node.name not in ALLOW_BUDGET:
            over.append(f"{f.name}:{node.name} — {stmts} statements (budget {STATEMENT_BUDGET})")
    assert not over, (
        "a tool body outgrew the budget. Move the logic into the owning service's route and forward "
        "to it; if it genuinely cannot move yet, add an allowlist entry NAMING THE BACKLOG:\n  "
        + "\n  ".join(over))


def test_no_product_copy_baked_into_a_tool():
    """4b. Product copy belongs in `_global`, read hot — never in an image (PRD §3.2, seam B2)."""
    big = []
    for f, node in _all_tools():
        doc = ast.get_docstring(node, clean=False)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value is doc or sub.value == doc:
                    continue
                if len(sub.value) > 400 and node.name not in ALLOW_BIG_LITERAL:
                    big.append(f"{f.name}:{sub.lineno} {node.name} — a {len(sub.value)}-char literal")
    assert not big, (
        "a tool carries a large string literal. If it is copy a person reads, it belongs in "
        "`_global` where an admin can change it without a deploy:\n  " + "\n  ".join(big))


def test_the_allowlists_have_no_dead_entries():
    """An allowlist row that no longer matches a tool is a claim about the code that has stopped
    being true — the same failure mode as a stale comment, with a green test in front of it."""
    names = {node.name for _, node in _all_tools()}
    dead = sorted((set(ALLOW_BUDGET) | set(ALLOW_TWO_SERVICES) | set(ALLOW_BIG_LITERAL)) - names)
    assert not dead, f"allowlist entries name tools that do not exist: {dead}"
