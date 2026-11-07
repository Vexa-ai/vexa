"""The one page grammar (PRD 0.13.4 D17/D18): parse rules, the merged verb registry, anchors, the
render sentinel. The shared cases file is run by the client's parser too, so the two cannot drift."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from control_plane import page_grammar as g

CASES = json.loads((Path(g.__file__).with_name("page_grammar.cases.json")).read_text())["cases"]


def _verbs(md):
    out = []
    for n in g.parse(md):
        items = [n.item] if n.t == "block" else n.parts
        out += [i.verb for i in items if isinstance(i, g.Action)]
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["src"][:40] for c in CASES])
def test_shared_cases(case):
    assert [r.key for r in g.refs_in(case["src"])] == case["refs"]
    assert [b.key for b in g.blocks_in(case["src"])] == case["blocks"]
    assert _verbs(case["src"]) == case["verbs"]
    if case.get("raw") is False:
        md = "\n".join(n.text for n in g.parse(case["src"]) if n.t == "md")
        assert not re.search(r"\{\{[^{}\n]*\}\}", md)
        assert "[[action:" not in md


def test_one_verb_registry_holds_both_old_sets():
    action_verbs = ["run-routine", "start-call", "join-call", "new-chat", "accept-proposal", "prepare", "move-card", "schedule-call"]
    do_verbs = ["new-chat", "new-room", "schedule", "invite", "ask", "run"]
    for v in action_verbs + do_verbs:
        assert g.canonical_verb(v) in g.VERBS, v
    assert g.canonical_verb("run-routine") == "run"
    assert g.canonical_verb("schedule-call") == "schedule"
    assert g.canonical_verb("rm-rf") is None


def test_roles_gate_verbs():
    assert g.verb_allowed("new-chat", "reader")
    assert not g.verb_allowed("new-room", "reader")
    assert g.verb_allowed("new-room", "contributor")
    assert not g.verb_allowed("run", None)


def test_fences_are_text():
    nodes = g.parse("```\n{{rooms}}\n```\n{{people}}")
    assert nodes[0].t == "md" and "{{rooms}}" in nodes[0].text
    assert nodes[1].t == "block" and nodes[1].item.name == "people"


def test_inline_action_stays_in_its_line():
    nodes = g.parse("Start here: {{new-chat prompt=\"Plan the week\"}} then read [[room:s-1]].")
    assert [n.t for n in nodes] == ["line"]
    kinds = [type(p).__name__ for p in nodes[0].parts]
    assert kinds == ["str", "Action", "str", "Ref", "str"]
    assert nodes[0].parts[1].args == {"prompt": "Plan the week"}


def test_anchor_round_trip():
    r = g.parse_ref_parts("meeting", "153@0:12:30")
    assert r.key == "meeting:153#t=00:12:30"
    assert g.ref_chip(r) == "[[meeting:153@00:12:30]]"
    assert g.ref_url(r) == "/r/meeting/153#t=00:12:30"
    assert g.parse_ref_parts("meeting", "153@99:99") is None or g.parse_ref_parts("meeting", "153@99:99").anchor is None


def test_resolved_sentinel():
    assert g.contains_resolved("x <!-- vx-resolved block=rooms --> y")
    assert g.contains_resolved('<div data-vx-resolved="1">')
    assert not g.contains_resolved("{{rooms}} [[room:s-1]]")
