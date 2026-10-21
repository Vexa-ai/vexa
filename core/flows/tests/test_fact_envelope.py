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
NOW = 1792310400
FACT = {"event_type": "crm.record.changed", "source_event_id": "e-1",
        "refs": {"org_id": "org-acme", "record_id": "rec-42", "revision": 3, "uid": "21"}}
CARD = {"v": 2, "producer": "crm", "object": "card", "owner": "11", "visibility": "members",
        "audience": ["21", "11", "ws:sales"],
        "lifecycle": {"kind": "commitment", "state": "proposed", "next_actor": "11",
                      "next_check_at": "2026-10-18T09:00:00+00:00", "handoff": "offered"}}


def _seal(env=CARD, secret=SECRET, fact=FACT, now=NOW):
    return fe.seal(env, secret, fact, now=now)


def _verify(env, secret=SECRET, producers=("crm",), fact=FACT, now=NOW):
    return fe.verify(env, secret, set(producers), fact, now=now)


def test_seal_then_verify_round_trips_to_the_envelope_without_its_seal():
    sealed = _seal()
    assert len(sealed["sig"]) == 64
    assert sealed["exp"] == NOW + fe.SEAL_TTL_S
    assert _verify(sealed) == {**CARD, "exp": NOW + fe.SEAL_TTL_S}


def test_fact_names_exactly_the_three_bound_fields():
    assert fe.fact("t", "id", {"uid": "1"}) == {"event_type": "t", "source_event_id": "id",
                                                 "refs": {"uid": "1"}}


@pytest.mark.parametrize("other", [
    {**FACT, "event_type": "crm.record.deleted"},
    {**FACT, "source_event_id": "e-2"},
    {**FACT, "refs": {**FACT["refs"], "uid": "99"}},
    {**FACT, "refs": {**FACT["refs"], "record_id": "rec-43"}},
    {**FACT, "refs": {**FACT["refs"], "revision": 4}},
    {**FACT, "refs": {**FACT["refs"], "org_id": "org-other"}},
    {**FACT, "refs": {**FACT["refs"], "extra": "x"}},
    {**FACT, "refs": {k: v for k, v in FACT["refs"].items() if k != "uid"}},
], ids=["event-type", "source-event-id", "subject", "object-id", "revision", "org", "added-ref",
        "dropped-subject"])
def test_deny_a_seal_lifted_onto_another_fact_does_not_verify(other):
    """The seal covers the fact it rides on: the same envelope over a different event, event id,
    subject or object is unverified."""
    assert _verify(_seal(), fact=other) is None


def test_deny_an_expired_seal_does_not_verify():
    sealed = _seal()
    assert _verify(sealed, now=NOW + fe.SEAL_TTL_S - 1) is not None
    assert _verify(sealed, now=NOW + fe.SEAL_TTL_S) is None
    assert _verify(sealed, now=NOW + 10 * fe.SEAL_TTL_S) is None


def test_deny_an_expiry_further_ahead_than_a_seal_sets_is_refused():
    """A producer clock up to CLOCK_SKEW_S ahead verifies; an `exp` a seal could not have set does not."""
    assert _verify(_seal(now=NOW + fe.CLOCK_SKEW_S)) is not None
    assert _verify(_seal(now=NOW + fe.CLOCK_SKEW_S + 1)) is None
    sealed = _seal()
    assert _verify({**sealed, "exp": sealed["exp"] + 3600}) is None      # and exp is under the seal


def test_a_producer_cannot_choose_its_own_expiry():
    with pytest.raises(ValueError):
        _seal({**CARD, "exp": NOW + 10 ** 6})


def test_deny_an_envelope_without_exp_does_not_verify():
    body = {k: v for k, v in _seal().items() if k not in ("sig", "exp")}
    forged = {**body, "sig": fe._sig(body, FACT, SECRET)}
    assert _verify(forged) is None


def test_the_canonical_form_is_sorted_compact_utf8():
    assert fe.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode("utf-8")


def test_a_wrong_secret_or_a_producer_off_the_allow_list_does_not_verify():
    sealed = _seal()
    assert _verify(sealed, secret="another-secret") is None
    assert _verify(sealed, producers=("meetings", "agent")) is None
    assert _verify(sealed, secret="") is None


def test_an_envelope_sealed_as_one_producer_does_not_verify_as_another():
    """The key is per producer: relabelling a crm envelope `meetings` breaks the seal."""
    sealed = _seal()
    assert _verify({**sealed, "producer": "meetings"}, producers=("crm", "meetings")) is None
    assert fe.producer_key(SECRET, "crm") != fe.producer_key(SECRET, "meetings")


@pytest.mark.parametrize("tamper", [
    lambda e: {**e, "audience": e["audience"] + ["99"]},
    lambda e: {**e, "audience": ["99"]},
    lambda e: {**e, "owner": "99"},
    lambda e: {**e, "visibility": "workspace"},
    lambda e: {**e, "lifecycle": {**e["lifecycle"], "next_actor": "99"}},
    lambda e: {**e, "exp": e["exp"] - 1},
    lambda e: {**e, "sig": "0" * 64},
    lambda e: {k: v for k, v in e.items() if k != "sig"},
], ids=["audience-added", "audience-replaced", "owner", "visibility", "next-actor", "exp", "sig",
        "no-sig"])
def test_a_tampered_envelope_does_not_verify(tamper):
    assert _verify(tamper(_seal())) is None


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
    {**CARD, "exp": "soon"},
    {k: v for k, v in CARD.items() if k != "audience"},
], ids=["content-field", "extra-lifecycle-key", "v1", "visibility", "audience-201", "long-entry",
        "audience-not-list", "no-owner", "lifecycle-non-string", "exp-not-int", "no-audience"])
def test_an_envelope_outside_the_enumeration_is_neither_sealed_nor_verified(bad):
    with pytest.raises(ValueError):
        _seal(bad)
    # …and a hand-made seal over it is refused anyway: verify checks shape before the seal.
    body = {"exp": NOW + 60, **{k: v for k, v in bad.items() if k != "sig"}}
    forged = {**body, "sig": fe._sig(body, FACT, SECRET)} if "producer" in body else body
    assert _verify(forged) is None


def test_null_lifecycle_values_are_dropped_before_sealing():
    sealed = _seal({**CARD, "lifecycle": {"kind": "commitment", "next_check_at": None}})
    assert sealed["lifecycle"] == {"kind": "commitment"}
    assert _verify(sealed) is not None


def test_no_secret_refuses_to_seal():
    with pytest.raises(ValueError):
        _seal(secret="")


@pytest.mark.parametrize("bad", [
    {"event_type": "t", "source_event_id": "id"},
    {**FACT, "refs": "uid=21"},
    {**FACT, "event_type": ""},
    {**FACT, "extra": 1},
], ids=["no-refs", "refs-not-object", "empty-type", "extra-key"])
def test_a_malformed_fact_is_neither_sealed_nor_verified(bad):
    with pytest.raises(ValueError):
        _seal(fact=bad)
    assert _verify(_seal(), fact=bad) is None


def test_verify_never_raises_on_garbage():
    for junk in (None, 3, "x", [], {"sig": 5}, {"v": 2, "producer": ["crm"], "sig": "ab"}):
        assert _verify(junk) is None
    assert fe.verify(_seal(), SECRET, {"crm"}, None) is None


def test_deny_the_secret_is_the_seal_env_and_never_the_internal_secret():
    assert fe.secret_from_env({"VEXA_FACT_SEAL_SECRET": "own", "INTERNAL_API_SECRET": "i"}) == "own"
    assert fe.secret_from_env({"VEXA_FACT_SEAL_SECRET": "", "INTERNAL_API_SECRET": "i"}) == ""
    assert fe.secret_from_env({"INTERNAL_API_SECRET": "i"}) == ""
    assert fe.secret_from_env({}) == ""


def test_every_golden_envelope_carries_a_seal_the_python_helper_verifies():
    """The goldens are sealed with `golden-seal-secret` at NOW over their own fact (the card fact for
    the `Envelope.*` cases); a change to the canonical form, the fact binding or the key derivation
    breaks them here instead of passing silently."""
    seen = 0
    golden = CONTRACT / "golden"
    card = json.loads((golden / "Event.card-fact.json").read_text())
    for path in sorted(golden.glob("*.json")):
        doc = json.loads(path.read_text())
        env = doc.get("envelope") if path.name.startswith("Event.") else doc
        if not env:
            continue
        src = doc if path.name.startswith("Event.") else card
        the_fact = fe.fact(src["event_type"], src["source_event_id"], src["refs"])
        ok = fe.verify(env, "golden-seal-secret", {"crm"}, the_fact, now=NOW) is not None
        assert ok == (".reject-" not in path.name), path.name
        seen += 1
    assert seen >= 4


def test_the_vendored_copy_is_the_canonical_copy():
    canonical = (CONTRACT / "fact_envelope.py").read_bytes()
    assert (Path(fe.__file__)).read_bytes() == canonical
    crm = Path(__file__).resolve().parents[2] / "crm" / "src" / "crm" / "fact_envelope.py"
    assert crm.read_bytes() == canonical
