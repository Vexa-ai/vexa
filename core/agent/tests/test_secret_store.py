"""secret_store — encryption at rest for every server-side credential (PATs and deploy keys alike).

What these hold: the round-trip works, the plaintext is genuinely absent from the bytes on disk, a
DIFFERENT key reads as "no secret" rather than as garbage, a tampered envelope is refused, the operator
key (``VEXA_SECRETS_KEY``) is honoured over the generated one, and names cannot traverse out of the store.
"""
import os

import pytest

from control_plane import secret_store as ss


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """The host may export VEXA_SECRETS_KEY; each test says which key it means."""
    monkeypatch.delenv(ss.ENV_KEY_NAME, raising=False)


def _disk(root) -> bytes:
    blob = b""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                blob += fh.read()
    return blob


def test_round_trip_and_nothing_readable_on_disk(tmp_path):
    assert ss.get(tmp_path, "pat/u1") is None
    assert ss.put(tmp_path, "pat/u1", "ghp_TOPSECRET_value") is True
    assert ss.get(tmp_path, "pat/u1") == "ghp_TOPSECRET_value"
    assert ss.has(tmp_path, "pat/u1")
    assert b"ghp_TOPSECRET_value" not in _disk(tmp_path)
    f = tmp_path / ".secrets" / "pat" / "u1.enc"
    assert f.read_text().startswith("v1.")
    assert oct(f.stat().st_mode)[-3:] == "600"


def test_the_same_value_seals_differently_each_time(tmp_path):
    """A fresh salt per write — two accounts with the same PAT must not produce identical files."""
    ss.put(tmp_path, "a", "same-value")
    first = (tmp_path / ".secrets" / "a.enc").read_text()
    ss.put(tmp_path, "a", "same-value")
    assert (tmp_path / ".secrets" / "a.enc").read_text() != first
    assert ss.get(tmp_path, "a") == "same-value"


def test_a_different_key_reads_as_absent_not_as_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv(ss.ENV_KEY_NAME, "operator-key-one")
    ss.put(tmp_path, "pat/u1", "ghp_value")
    assert ss.get(tmp_path, "pat/u1") == "ghp_value"
    monkeypatch.setenv(ss.ENV_KEY_NAME, "operator-key-two")
    assert ss.get(tmp_path, "pat/u1") is None
    assert ss.has(tmp_path, "pat/u1") is False


def test_a_tampered_envelope_is_refused(tmp_path):
    ss.put(tmp_path, "k", "value-to-protect")
    f = tmp_path / ".secrets" / "k.enc"
    version, salt, ct, mac = f.read_text().split(".")
    # TAMPER IN THE MIDDLE, never the last character. Base64 without padding encodes the final byte
    # in the high bits of its last character, so the low bits of that character are DISCARDED on
    # decode: 16 of the 64 alphabet symbols decode to the identical ciphertext, the MAC still
    # validates, and the test passed or failed by luck — ~25% flaky, which reads as an infrastructure
    # hiccup rather than as a test that is not testing anything. A character in the middle changes a
    # whole byte, so the envelope is genuinely different every time.
    i = len(ct) // 2
    flipped = ct[:i] + ("A" if ct[i] != "A" else "B") + ct[i + 1:]
    assert flipped != ct
    f.write_text(".".join((version, salt, flipped, mac)))
    assert ss.get(tmp_path, "k") is None, "the MAC must be checked before anything is decrypted"


def test_a_generated_key_is_used_when_the_operator_sets_none(tmp_path):
    ss.put(tmp_path, "k", "v")
    kf = tmp_path / ".secrets" / ss.MASTER_KEY_FILENAME
    assert kf.exists() and oct(kf.stat().st_mode)[-3:] == "600"
    assert ss.get(tmp_path, "k") == "v"       # stable across calls (the key is read back, not regenerated)


def test_the_operator_key_beats_the_generated_one(tmp_path, monkeypatch):
    monkeypatch.setenv(ss.ENV_KEY_NAME, "the-operators-key")
    ss.put(tmp_path, "k", "v")
    assert not (tmp_path / ".secrets" / ss.MASTER_KEY_FILENAME).exists(), \
        "with an operator key nothing should be generated into the data volume"
    assert ss.get(tmp_path, "k") == "v"


def test_delete_and_empty_value_clear(tmp_path):
    ss.put(tmp_path, "k", "v")
    assert ss.put(tmp_path, "k", "") is False
    assert ss.get(tmp_path, "k") is None
    ss.put(tmp_path, "k", "v")
    assert ss.delete(tmp_path, "k") is True
    assert ss.get(tmp_path, "k") is None


@pytest.mark.parametrize("name", ["../escape", "a/../../b", "", "a b", "/abs"])
def test_unsafe_names_never_reach_the_filesystem(tmp_path, name):
    assert ss.get(tmp_path, name) is None
    with pytest.raises(ValueError):
        ss.put(tmp_path, name, "x")
