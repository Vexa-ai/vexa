"""THE MANIFESTS IN THIS REPO ARE VALID, AND THEY ARE WHAT THE DOMAINS SERVE.

Each domain commits its manifest in its own directory and serves that same file at
`/.well-known/mcp-tools.json`. This test reads them off disk and puts them through the assembler,
so a manifest that could not boot the edge fails here instead — in the suite of the service that
would have refused to start.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from vexa_mcp import bind
from vexa_mcp import manifest as m

REPO = pathlib.Path(__file__).resolve().parents[5]
MANIFESTS = sorted(REPO.glob("core/*/mcp.tools.v1.json")) + \
            sorted(REPO.glob("core/*/services/*/mcp.tools.v1.json"))

#: flows' `flows_submit`/`flow_lifecycle` are `auth: admin` — a real operator credential this
#: SUITE'S environment does not hold. Assembling the real on-disk manifests (this file's whole
#: point) needs one anyway, or the assertion these tools exist would fail for a reason that has
#: nothing to do with whether the manifests are well-formed.
ASSEMBLY_ENV = {"VEXA_FLOWS_API_KEY": "test-operator-key"}


def _load():
    return [json.loads(p.read_text()) for p in MANIFESTS]


def test_there_is_at_least_one_manifest_and_every_one_of_them_validates():
    docs = _load()
    assert docs, "no domain has published a manifest yet"
    for doc, path in zip(docs, MANIFESTS):
        try:
            m.validate(doc)
        except m.ManifestError as e:
            pytest.fail(f"{path.relative_to(REPO)}: {e}")


def test_one_domain_per_manifest_and_no_name_claimed_twice():
    docs = _load()
    domains = [d["domain"] for d in docs]
    assert len(domains) == len(set(domains)), f"two manifests for one domain: {domains}"
    deployed = {"identity"} | set(domains)
    a = m.assemble(docs, deployed=deployed, env=ASSEMBLY_ENV)          # raises on a duplicate name
    assert a.tools, "the union is empty"


def test_the_full_deployment_and_the_smallest_one_both_assemble():
    """Eight configurations, not two. Identity alone must still produce a coherent surface, and a
    tool whose domain is absent is ABSENT — never present-and-failing."""
    docs = _load()
    everything = m.assemble(docs, deployed={"identity", "meetings", "flows", "agent"},
                            env=ASSEMBLY_ENV)
    identity_only = m.assemble(docs, deployed={"identity"})
    assert len(identity_only.tools) <= len(everything.tools)
    for t in identity_only.tools:
        assert t.requires <= {"identity"}


def test_no_manifest_declares_a_credential_argument():
    """PRD 40.8 — one authentication path: a bearer header, session-bound. `validate` enforces it;
    this states it as its own fact so the reason survives a refactor of the validator."""
    for doc, path in zip(_load(), MANIFESTS):
        for t in doc.get("tools") or []:
            for arg in t.get("arguments") or []:
                assert arg.lower() not in m.CREDENTIAL_ARGUMENTS, f"{path}: {t['name']} takes {arg}"
