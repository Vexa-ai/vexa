"""The words an agent reads before it decides to call `whats_waiting`.

Ten ordinary sessions with the tools loaded never called it. The description they were served was
this route's IMPLEMENTATION NOTES — `X-User-Id` versus `?subject=` precedence, operator keys,
403/400 shapes, `VEXA_FLOWS_TIMELINE_KEY` — about 250 words that answer *how do I call this
correctly*, a question an agent that never calls it does not have. Nothing anywhere said WHEN.

So the route now carries an explicit `summary` written for the agent, and NO docstring: the MCP
edge derives a tool's description from this operation, preferring the docstring and falling back
to the summary (`core/meetings/services/mcp/src/vexa_mcp/bind.py::_describe`), so a docstring here
is served to every agent in front of every call, forever. The maintainer's half moved to a comment
block above the route, where no agent pays for it by the token.

Asserted against the SOURCE, not by importing the module: importing `flows_integrations.flows_api`
opens a Postgres connection and mints an API key at import time, which is why no test in this
suite imports it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
API = SRC / "flows_integrations" / "flows_api.py"


def _module() -> ast.Module:
    return ast.parse(API.read_text(encoding="utf-8"))


def _route(name: str) -> ast.FunctionDef:
    for node in _module().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a route in flows_api.py any more")


def _summary_constant() -> str:
    """`WHATS_WAITING_SUMMARY`, evaluated from the source without importing the module."""
    for node in _module().body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "WHATS_WAITING_SUMMARY" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("WHATS_WAITING_SUMMARY is gone — the agent-facing description with it")


def test_the_route_declares_an_explicit_agent_facing_summary():
    """Without one, FastAPI synthesises `summary` from the function name and the tool ships
    described as "Queue Waiting" — a title, printed where instructions belong."""
    route = _route("queue_waiting")
    decorators = [d for d in route.decorator_list if isinstance(d, ast.Call)]
    assert decorators, "queue_waiting lost its route decorator"
    kwargs = {k.arg: k.value for d in decorators for k in d.keywords}
    assert "summary" in kwargs, "the route must set `summary=` itself, not let FastAPI title it"
    assert isinstance(kwargs["summary"], ast.Name)
    assert kwargs["summary"].id == "WHATS_WAITING_SUMMARY"


def test_the_summary_says_WHEN_to_call_it():
    """The one thing the route's own mechanics could never say, and the whole reason it exists."""
    summary = _summary_constant().lower()
    assert "call it" in summary
    assert "start of a session" in summary
    # `say` is the field an agent reads out; a description that omits it leaves the agent holding
    # a queue it does not know how to voice.
    assert "say" in summary


def test_the_summary_is_one_short_paragraph():
    """An agent reads this in front of every call. ≤60 words, one paragraph."""
    summary = _summary_constant()
    assert "\n" not in summary.strip(), "one paragraph — a second one is the description's job"
    assert len(summary.split()) <= 60, f"{len(summary.split())} words is a document, not a lede"


def test_the_route_carries_no_docstring():
    """A docstring here becomes the OpenAPI `description`, which the MCP edge prefers over the
    summary — so the operator mechanics would be back in front of every agent, silently."""
    assert ast.get_docstring(_route("queue_waiting")) is None


def test_no_operator_or_transport_mechanics_reach_the_agent():
    """The 250 words that were served instead of instructions. Each of these is a maintainer's
    concern; an agent calling this tool with its own credential meets none of them."""
    summary = _summary_constant()
    for leak in ("X-User-Id", "?subject=", "VEXA_FLOWS_TIMELINE_KEY", "403", "400",
                 "operator", "header", "credential"):
        assert leak.lower() not in summary.lower(), f"{leak!r} is maintainer's prose, not the lede"


def test_the_agent_facing_summary_is_billing_free():
    """Money is a different seam. Nothing about plans or price belongs in the first tool an agent
    is told to call."""
    summary = _summary_constant().lower()
    for word in ("plan", "price", "pricing", "billing", "paid", "quota", "upgrade",
                 "subscription", "credit", "trial", "$"):
        assert word not in summary, f"{word!r} puts money in the queue's description"


def test_the_maintainers_half_survived_the_move():
    """Moved, not deleted. Every sentence the docstring carried is still beside the route — as a
    comment, which reaches a reader of this file and no agent anywhere."""
    src = API.read_text(encoding="utf-8")
    head = src[:src.index("def queue_waiting")]
    comments = "\n".join(re.findall(r"^\s*#.*$", head, flags=re.M))
    for kept in ("X-User-Id", "?subject=", "VEXA_FLOWS_TIMELINE_KEY",
                 "THE SUBJECT IS THE AUTHENTICATED CALLER'S", "issue #1468"):
        assert kept in comments, f"{kept!r} was dropped rather than moved"
