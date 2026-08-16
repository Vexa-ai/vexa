"""L1 — the config.v1 declaration is the SSOT for this service's environment.

`gate:config-contract` enforces this for the three adopted services by checking the declaration
against compose/helm/lite. The mailroom is not adopted (it is compose-profile-only and ships no
helm or lite surface), so the same discipline is kept here instead of skipped: every literal env
read in the shipped source must name a declared key, and every declared key must be read. A
declaration nobody checks is documentation that goes stale on the first commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "vexa_mailroom"
DECL = json.loads((SRC / "config.v1.json").read_text("utf-8"))

# The spellings the scanner recognizes — the same shapes gate:config-contract looks for.
READS = [re.compile(r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
         re.compile(r"""os\.environ(?:\.get)?\(?\s*\[?\s*["']([A-Z][A-Z0-9_]*)["']"""),
         re.compile(r"""\be\.get\(\s*["']([A-Z][A-Z0-9_]*)["']""")]


def _declared() -> set[str]:
    return {k["key"] for k in DECL["keys"]}


def _read_in_source() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text("utf-8")
        for pattern in READS:
            found |= set(pattern.findall(text))
    return found


def test_declaration_conforms_to_the_contract_shape():
    assert DECL["contract"] == "config.v1"
    assert DECL["service"] == "mailroom"
    for key in DECL["keys"]:
        assert key["class"] in ("required-explicit", "defaulted", "capability")
        assert key["description"].strip()
        if key["class"] == "defaulted":
            assert "default" in key, f"{key['key']} is defaulted with no documented default"


def test_every_env_read_is_declared():
    """A new env read must land in the declaration — that is what makes it the SSOT."""
    undeclared = sorted(_read_in_source() - _declared())
    assert not undeclared, f"undeclared env reads: {undeclared}"


def test_every_declared_key_is_read():
    """And the reverse: a declared key nobody reads is a lie about what the service consumes."""
    # MAILROOM_ICS_CORPUS is read by the eval, not the service — declared so the scan stays tight.
    test_only = {"MAILROOM_ICS_CORPUS"}
    unread = sorted(_declared() - _read_in_source() - test_only)
    assert not unread, f"declared but never read: {unread}"


def test_compose_sets_only_declared_keys():
    """The one deploy surface this service has must not set anything undeclared."""
    compose = (Path(__file__).resolve().parents[5] / "deploy" / "compose" /
               "docker-compose.yml").read_text("utf-8")
    tail = compose.split("\n  mailroom:\n", 1)[1] if "\n  mailroom:\n" in compose else ""
    block = re.split(r"\n(?=[a-z ]{0,2}\S)", tail, maxsplit=1)[0]   # up to the next top/service key
    keys = set(re.findall(r"^\s+-\s+([A-Z][A-Z0-9_]*)=", block, flags=re.M))
    assert keys, "the compose mailroom service sets no environment — did the block move?"
    assert not keys - _declared(), f"compose sets undeclared keys: {sorted(keys - _declared())}"
