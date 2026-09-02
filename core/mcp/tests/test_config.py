"""Config is a declared contract, and this package reads nothing it has not declared.

`gate:config-contract` already scans `src/` for literal env reads and fails on an undeclared one.
This file is the same rule from the other side, and it runs in `gate:python` where the package
lives: it fails if a DECLARED key is never read (a declaration nobody consumes is a promise about
behaviour that has stopped being true — the dead-key shape the seam inventory found four times in
`config.v1.json` files), and it pins the two facts about this service's shape that a future edit is
most likely to reverse by accident.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DECL = json.loads((ROOT / "config.v1.json").read_text())
SRC = (ROOT / "src" / "vexa_mcp")
READS = set()
for f in SRC.rglob("*.py"):
    if f.name == "config_preflight.py":       # vendored verbatim; not this service's own reads
        continue
    for m in re.finditer(r'os\.environ(?:\.get)?[\(\[]\s*["\']([A-Z][A-Z0-9_]*)["\']', f.read_text()):
        READS.add(m.group(1))

DECLARED = {k["key"] for k in DECL["keys"]}


def test_the_declaration_names_this_service_and_conforms_in_shape():
    assert DECL["contract"] == "config.v1"
    assert DECL["service"] == "mcp-control"
    for k in DECL["keys"]:
        assert k["description"].strip(), f"{k['key']} has no description"
        if k["class"] == "defaulted":
            assert "default" in k, f"{k['key']} is defaulted with no documented default"
        if k["class"] == "capability":
            assert k["capability"] in DECL["capabilities"], f"{k['key']} names an unknown capability"


def test_every_key_this_package_reads_is_declared():
    assert READS - DECLARED == set(), f"undeclared env reads: {sorted(READS - DECLARED)}"


def test_every_declared_key_is_actually_read():
    """The gate checks reads → declaration and not the reverse, so a key declared and never read
    passes silently forever. Two such keys are already dead in this repo's other declarations."""
    assert DECLARED - READS == set(), f"declared but never read: {sorted(DECLARED - READS)}"


def test_there_is_no_docker_socket_no_container_name_and_no_database_url():
    """The four deployment inputs that made the predecessor un-packageable. This is the config-side
    half of `test_thin_forward.py`: that one says no tool REACHES them, this one says none of them
    is a thing a deployment can even hand us."""
    # KEYS AND DEFAULTS, not descriptions: several descriptions name what was removed and why,
    # which is the point of writing them down.
    blob = " ".join([k["key"] for k in DECL["keys"]]
                    + [str(k.get("default", "")) for k in DECL["keys"]]).lower()
    for forbidden in ("docker", "vexa-dogfood-", "postgres", "dburl", "flows_db", ".sock"):
        assert forbidden not in blob, f"{forbidden!r} is a deployment input of this service again"


def test_the_credential_in_a_query_string_is_off_by_default():
    """`VEXA_RIG_MODE` enables the `token=` call-argument fallback and the `GET /do` bridge. The rig
    defaulted it ON; the product defaults it OFF, and a deployment turns it on deliberately."""
    from vexa_mcp import config
    rig = next(k for k in DECL["keys"] if k["key"] == "VEXA_RIG_MODE")
    assert rig["default"] == "0"
    assert config.RIG_MODE is False or "VEXA_RIG_MODE" in __import__("os").environ


def test_the_only_writable_directory_is_declared_as_one_key():
    from vexa_mcp import config
    assert "VEXA_HOME" in DECLARED
    for name in ("TOKENS_FILE", "USER_KEYS_FILE", "EMAIL_CODES", "LOGINS", "REGIMES",
                 "REVOKED_FILE", "FRICTION_LOG", "CAPS_DIR"):
        p = getattr(config, name)
        assert str(p).startswith(str(config.VEXA_HOME)), f"{name} escapes VEXA_HOME"
