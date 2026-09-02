"""`GET /reactions` filters, so nobody has to open the database to answer a question about it.

The control MCP's `bot_schedule` answered "what is still booked for this meeting?" with
`psycopg.connect` straight into this service's Postgres — a URL read out of `~/.storm/dburl`, a
`SELECT … WHERE source_event_id LIKE 'sched-<mid>-%' AND status IN (…)` run past the owner of the
table (seam inventory B6.3). Two access paths to one store, in one tool.

That is not primarily a security finding, it is a MISSING FILTER finding: the route could not answer
the question, so somebody wrote the second path. The filter is the fix, and this file is what stops
it being removed as unused.

Asserted against the SOURCE, the same way `test_instance_gate.py`'s third concern is: importing
`flows_integrations.flows_api` opens a Postgres connection and mints an API key at import time, so
no test in this suite imports it.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
API = SRC / "flows_integrations" / "flows_api.py"


def _route(name: str) -> ast.FunctionDef:
    tree = ast.parse(API.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a route in flows_api.py any more")


def test_the_listing_takes_a_source_event_prefix():
    """The only handle on "the join I booked for THIS meeting": the listing carries no meeting
    reference, and the scheduled-join fact's id is `sched-<meeting>-<epoch>`."""
    args = {a.arg for a in _route("list_reactions").args.args}
    assert "source_event_prefix" in args
    assert "status" in args


def test_the_listing_takes_several_states_at_once():
    """"What is still live for this booking" is four states. Four round-trips to ask it is how a
    caller decides to read the table instead."""
    src = ast.unparse(_route("list_reactions"))
    assert "split(',')" in src.replace('", "', "','").replace('split(",")', "split(',')")
    assert "status IN" in src


def test_the_caller_s_text_is_bound_never_interpolated():
    """A LIKE pattern built by string interpolation is an injection with extra steps. The wildcard
    is appended HERE and the caller's own `%`/`_` are stripped, so a caller cannot supply a
    pattern — only a prefix."""
    fn = _route("list_reactions")
    src = ast.unparse(fn)
    assert ":sep" in src, "the prefix must reach SQL as a bound parameter"
    assert "source_event_prefix.replace" in src, "the caller's wildcards must be stripped"
    # Nothing derived from the caller may land in an f-string's SUBSTITUTED half. (The literal
    # half legitimately names the `status` COLUMN, which is why this looks at the `{...}` parts and
    # not at the whole string.)
    for node in ast.walk(fn):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    inner = ast.unparse(part.value)
                    assert "source_event_prefix" not in inner and "status" not in inner, (
                        "a caller value was interpolated into a SQL string: " + inner)


def test_reads_stay_open_and_the_route_is_still_authenticated():
    """READING stays open to an operator with the key (the module docstring's rule); adding a
    filter must not quietly change who may call it."""
    decs = [ast.unparse(d) for d in _route("list_reactions").decorator_list]
    assert any("dependencies=[Depends(auth)]" in d for d in decs)
