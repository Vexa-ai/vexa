"""event.v2's sealed fact envelope (PRD 0.13.4 D8) — the canonical helper and its goldens.

Offline, stdlib only. The vendored copy under `src/flows/` is the one imported; parity
(`scripts/parity.json`, `fact-envelope`) holds it byte-identical to the contract's canonical copy,
and the last test here says so too, so a drift fails this suite before it fails the gate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import fact_envelope as fe  # noqa: E402

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "event.v2"
SECRET = "test-seal-secret"
CARD = {"v": 2, "producer": "crm", "object": "card", "owner": "11", "visibility": "members",
        "audience": ["21", "11", "ws:sales"],
        "lifecycle": {"kind": "commitment", "state": "proposed", "next_actor": "11",
                      "next_check_at": "2026-10-18T09:00:00+00:00", "handoff": "offered"}}


def test_seal_then_verify_round_trips_to_the_envelope_without_its_seal():
    sealed = fe.seal(CARD, SECRET)
    assert len(sealed["sig"]) == 64
    assert fe.verify(sealed, SECRET, {"crm"}) == CARD


def test_the_canonical_form_is_sorted_compact_utf8():
    assert fe.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode("utf-8")


def test_a_wrong_secret_or_a_producer_off_the_allow_list_does_not_verify():
    sealed = fe.seal(CARD, SECRET)
    assert fe.verify(sealed, "another-secret", {"crm"}) is None
    assert fe.verify(sealed, SECRET, {"meetings", "agent"}) is None
    assert fe.verify(sealed, "", {"crm"}) is None


def test_an_envelope_sealed_as_one_producer_does_not_verify_as_another():
    """The key is per producer: relabelling a crm envelope `meetings` breaks the seal."""
    sealed = fe.seal(CARD, SECRET)
    assert fe.verify({**sealed, "producer": "meetings"}, SECRET, {"crm", "meetings"}) is None
    assert fe.producer_key(SECRET, "crm") != fe.producer_key(SECRET, "meetings")


@pytest.mark.parametrize("tamper", [
    lambda e: {**e, "audience": e["audience"] + ["99"]},
    lambda e: {**e, "audience": ["99"]},
    lambda e: {**e, "owner": "99"},
    lambda e: {**e, "visibility": "workspace"},
    lambda e: {**e, "lifecycle": {**e["lifecycle"], "next_actor": "99"}},
    lambda e: {**e, "sig": "0" * 64},
    lambda e: {k: v for k, v in e.items() if k != "sig"},
], ids=["audience-added", "audience-replaced", "owner", "visibility", "next-actor", "sig", "no-sig"])
def test_a_tampered_envelope_does_not_verify(tamper):
    assert fe.verify(tamper(fe.seal(CARD, SECRET)), SECRET, {"crm"}) is None


@pytest.mark.parametrize("bad", [
    {**CARD, "title": "Send the contract"},
    {**CARD, "lifecycle": {**CARD["lifecycle"], "summary": "words"}},
    {**CARD, "v": 1},
    {**CARD, "visibility": "everyone"},
    {**CARD, "audience": [str(i) for i in range(201)]},
    {**CARD, "audience": ["x" * 201]},
    {**CARD, "audience": "11"},
    {**CARD, "owner": ""},
    {**CARD, "lifecycle": {"kind": 3}},
    {k: v for k, v in CARD.items() if k != "audience"},
], ids=["content-field", "extra-lifecycle-key", "v1", "visibility", "audience-201", "long-entry",
        "audience-not-list", "no-owner", "lifecycle-non-string", "no-audience"])
def test_an_envelope_outside_the_enumeration_is_neither_sealed_nor_verified(bad):
    with pytest.raises(ValueError):
        fe.seal(bad, SECRET)
    # …and a hand-made seal over it is refused anyway: verify checks shape before the seal.
    body = {k: v for k, v in bad.items() if k != "sig"}
    forged = {**body, "sig": fe._sig(body, SECRET)} if "producer" in body else body
    assert fe.verify(forged, SECRET, {"crm"}) is None


def test_null_lifecycle_values_are_dropped_before_sealing():
    sealed = fe.seal({**CARD, "lifecycle": {"kind": "commitment", "next_check_at": None}}, SECRET)
    assert sealed["lifecycle"] == {"kind": "commitment"}
    assert fe.verify(sealed, SECRET, {"crm"}) is not None


def test_no_secret_refuses_to_seal():
    with pytest.raises(ValueError):
        fe.seal(CARD, "")


def test_verify_never_raises_on_garbage():
    for junk in (None, 3, "x", [], {"sig": 5}, {"v": 2, "producer": ["crm"], "sig": "ab"}):
        assert fe.verify(junk, SECRET, {"crm"}) is None


def test_the_secret_is_the_seal_env_else_the_internal_secret():
    assert fe.secret_from_env({"VEXA_FACT_SEAL_SECRET": "own", "INTERNAL_API_SECRET": "i"}) == "own"
    assert fe.secret_from_env({"VEXA_FACT_SEAL_SECRET": "", "INTERNAL_API_SECRET": "i"}) == "i"
    assert fe.secret_from_env({}) == ""


def test_every_golden_envelope_carries_a_seal_the_python_helper_verifies():
    """The goldens are sealed with `golden-seal-secret`; a change to the canonical form or to the
    key derivation breaks them here instead of passing silently."""
    seen = 0
    for path in sorted((CONTRACT / "golden").glob("*.json")):
        doc = json.loads(path.read_text())
        env = doc.get("envelope") if path.name.startswith("Event.") else doc
        if not env:
            continue
        ok = fe.verify(env, "golden-seal-secret", {"crm"}) is not None
        assert ok == (".reject-" not in path.name), path.name
        seen += 1
    assert seen >= 4


def test_the_vendored_copy_is_the_canonical_copy():
    canonical = (CONTRACT / "fact_envelope.py").read_bytes()
    assert (Path(fe.__file__)).read_bytes() == canonical
    crm = Path(__file__).resolve().parents[2] / "crm" / "src" / "crm" / "fact_envelope.py"
    assert crm.read_bytes() == canonical
