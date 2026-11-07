"""event.v2's sealed fact envelope (PRD 0.13.4 D8) — the canonical helper and its goldens.

Offline. The vendored copies under `src/` are the ones imported; parity (`scripts/parity.json`,
`fact-envelope`, `fact-keys`) holds them byte-identical to the contract's canonical copies, and the
last test here says so too, so a drift fails this suite before it fails the gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from flows import fact_envelope as fe  # noqa: E402
from flows_integrations import fact_keys as fk  # noqa: E402

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "event.v2"
#: The goldens' TEST signing key is the Ed25519 key whose seed is sha256 of this — published, so it
#: is never a deployment's key; only its public half is a file (`golden/golden-crm-verify-key.pem`).
GOLDEN_SEED = b"vexa event.v2 golden seal key"


def _pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


CRM_PEM, MEETINGS_PEM = _pem(Ed25519PrivateKey.generate()), _pem(Ed25519PrivateKey.generate())
SIGN = fk.load_signer(CRM_PEM)
CHECKS = {"crm": fk.load_check(fk.public_pem(CRM_PEM)),
          "meetings": fk.load_check(fk.public_pem(MEETINGS_PEM))}
NOW = 1792310400
FACT = {"event_type": "crm.record.changed", "source_event_id": "e-1",
        "refs": {"org_id": "org-acme", "record_id": "rec-42", "revision": 3, "uid": "21"}}
CARD = {"v": 2, "producer": "crm", "object": "card", "owner": "11", "visibility": "members",
        "audience": ["21", "11", "ws:sales"],
        "lifecycle": {"kind": "commitment", "state": "proposed", "next_actor": "11",
                      "next_check_at": "2026-10-18T09:00:00+00:00", "handoff": "offered"}}


def _seal(env=CARD, sign=SIGN, fact=FACT, now=NOW):
    return fe.seal(env, sign, fact, now=now)


def _verify(env, checks=CHECKS, producers=("crm",), fact=FACT, now=NOW):
    return fe.verify(env, checks, set(producers), fact, now=now)


def _forge(body, sign=SIGN, fact=FACT):
    """A hand-made seal over `body` with a real key — what a producer holding the key could make."""
    return {**body, "sig": sign(fe.message(body, fact)).hex()}


def test_seal_then_verify_round_trips_to_the_envelope_without_its_seal():
    sealed = _seal()
    assert len(sealed["sig"]) == 128
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
    assert _verify(_forge(body)) is None


def test_the_canonical_form_is_sorted_compact_utf8():
    assert fe.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode("utf-8")


def test_a_wrong_key_or_a_producer_off_the_allow_list_does_not_verify():
    sealed = _seal()
    assert _verify(sealed, checks={"crm": CHECKS["meetings"]}) is None
    assert _verify(sealed, producers=("meetings", "agent")) is None
    assert _verify(sealed, checks={}) is None
    assert _verify(sealed, checks={"crm": None}) is None


def test_deny_a1_one_producers_key_cannot_forge_another_producers_fact():
    """A1: every producer signs with its OWN key and flows verifies under the named producer's public
    key. The crm key — leaked, or held by a compromised crm outbox — signs an envelope that says
    `producer: meetings`, and it does not verify as meetings; meetings' own key does (positive)."""
    as_meetings = {**CARD, "producer": "meetings"}
    forged = _seal(as_meetings, sign=SIGN)
    assert _verify(forged, producers=("crm", "meetings")) is None
    genuine = _seal(as_meetings, sign=fk.load_signer(MEETINGS_PEM))
    assert _verify(genuine, producers=("crm", "meetings")) is not None


def test_deny_a1_the_verifier_holds_nothing_that_can_seal():
    """What flows-api is given (public keys) cannot be loaded as a signer, and a seal it could
    attempt with no signer is refused at the producer."""
    with pytest.raises(ValueError):
        fk.load_signer(fk.public_pem(CRM_PEM))
    with pytest.raises(ValueError):
        _seal(sign=None)


def test_an_envelope_sealed_as_one_producer_does_not_verify_as_another():
    """Relabelling a crm envelope `meetings` breaks the seal: it is checked under meetings' key."""
    sealed = _seal()
    assert _verify({**sealed, "producer": "meetings"}, producers=("crm", "meetings")) is None


@pytest.mark.parametrize("tamper", [
    lambda e: {**e, "audience": e["audience"] + ["99"]},
    lambda e: {**e, "audience": ["99"]},
    lambda e: {**e, "owner": "99"},
    lambda e: {**e, "visibility": "workspace"},
    lambda e: {**e, "lifecycle": {**e["lifecycle"], "next_actor": "99"}},
    lambda e: {**e, "exp": e["exp"] - 1},
    lambda e: {**e, "sig": "0" * 128},
    lambda e: {**e, "sig": e["sig"][:64]},
    lambda e: {k: v for k, v in e.items() if k != "sig"},
], ids=["audience-added", "audience-replaced", "owner", "visibility", "next-actor", "exp", "sig",
        "short-sig", "no-sig"])
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
    forged = _forge(body) if "producer" in body else body
    assert _verify(forged) is None


def test_null_lifecycle_values_are_dropped_before_sealing():
    sealed = _seal({**CARD, "lifecycle": {"kind": "commitment", "next_check_at": None}})
    assert sealed["lifecycle"] == {"kind": "commitment"}
    assert _verify(sealed) is not None


def test_no_key_refuses_to_seal():
    with pytest.raises(ValueError):
        _seal(sign=None)
    with pytest.raises(ValueError):
        _seal(sign=lambda m: b"short")


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
    assert fe.verify(_seal(), CHECKS, {"crm"}, None) is None
    assert fe.verify(_seal(), {"crm": lambda m, s: 1 / 0}, {"crm"}, FACT, now=NOW) is None


def test_deny_flows_verifies_from_its_own_key_directory_and_nothing_else(tmp_path, monkeypatch):
    """flows' checks are the `<producer>.pem` files in `VEXA_FACT_VERIFY_KEYS_DIR` and nothing else:
    no internal-tier secret, no shared seal secret. A file that is not a key, or a producer name
    that would leave the directory, verifies nothing."""
    from flows_integrations import flows_api as fa_mod
    assert fa_mod.FACT_VERIFY_KEYS_DIR_ENV == "VEXA_FACT_VERIFY_KEYS_DIR"
    pub = tmp_path / "verify"
    pub.mkdir()
    (pub / "crm.pem").write_bytes(fk.public_pem(CRM_PEM))
    (pub / "agent.pem").write_bytes(b"not a key")
    checks = fk.checks_from_dir(pub, {"crm", "meetings", "agent", "../crm"})
    assert set(checks) == {"crm"}
    assert _verify(_seal(), checks=checks) is not None
    assert fk.checks_from_dir("", {"crm"}) == {} and fk.checks_from_dir(None, {"crm"}) == {}
    key = tmp_path / "crm-key.pem"
    key.write_bytes(CRM_PEM)
    assert fk.signer_from_file(key) is not None
    for bad in ("", None, tmp_path / "none", pub / "crm.pem"):
        assert fk.signer_from_file(bad) is None, bad


def test_deny_a_key_that_is_not_ed25519_is_refused_both_ways():
    from cryptography.hazmat.primitives.asymmetric import ec
    other = ec.generate_private_key(ec.SECP256R1())
    pem = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    with pytest.raises(ValueError):
        fk.load_signer(pem)
    with pytest.raises(ValueError):
        fk.load_check(other.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(ValueError):
        fk.load_signer(b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")


def test_keygen_mints_once_then_keeps_the_key_and_heals_its_public_half(tmp_path):
    priv, pub = tmp_path / "s" / "crm.pem", tmp_path / "p" / "crm.pem"
    assert fk.keygen(priv, pub) is True
    first = priv.read_bytes()
    assert (priv.stat().st_mode & 0o777) == 0o600
    pub.unlink()
    assert fk.keygen(priv, pub) is False
    assert priv.read_bytes() == first
    sealed = _seal(sign=fk.load_signer(first))
    assert _verify(sealed, checks={"crm": fk.load_check(pub.read_bytes())}) is not None


def test_every_golden_envelope_carries_a_seal_the_python_helper_verifies():
    """The goldens are sealed with the TEST key `GOLDEN_SEED` at NOW over their
    own fact (the card fact for the `Envelope.*` cases) and verified under its public half; a change
    to the canonical form, the fact binding or the signature scheme breaks them here instead of
    passing silently. Ed25519 is deterministic, so re-sealing reproduces them byte for byte."""
    seen = 0
    golden = CONTRACT / "golden"
    key = _pem(Ed25519PrivateKey.from_private_bytes(hashlib.sha256(GOLDEN_SEED).digest()))
    checks = {"crm": fk.load_check((golden / "golden-crm-verify-key.pem").read_bytes())}
    assert fk.public_pem(key) == (golden / "golden-crm-verify-key.pem").read_bytes()
    card = json.loads((golden / "Event.card-fact.json").read_text())
    for path in sorted(golden.glob("*.json")):
        doc = json.loads(path.read_text())
        env = doc.get("envelope") if path.name.startswith("Event.") else doc
        if not env:
            continue
        src = doc if path.name.startswith("Event.") else card
        the_fact = fe.fact(src["event_type"], src["source_event_id"], src["refs"])
        ok = fe.verify(env, checks, {"crm"}, the_fact, now=NOW) is not None
        assert ok == (".reject-" not in path.name), path.name
        if ok:
            body = {k: v for k, v in env.items() if k not in ("sig", "exp")}
            assert fe.seal(body, fk.load_signer(key), the_fact, now=NOW) == env, path.name
        seen += 1
    assert seen >= 4


def test_the_vendored_copy_is_the_canonical_copy():
    canonical = (CONTRACT / "fact_envelope.py").read_bytes()
    assert (Path(fe.__file__)).read_bytes() == canonical
    crm = Path(__file__).resolve().parents[2] / "crm" / "src" / "crm"
    assert (crm / "fact_envelope.py").read_bytes() == canonical
    keys = (CONTRACT / "fact_keys.py").read_bytes()
    assert Path(fk.__file__).read_bytes() == keys
    assert (crm / "fact_keys.py").read_bytes() == keys
