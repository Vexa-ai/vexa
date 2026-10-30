"""field_crypto — envelope encryption for secret values in users.data (#876).

The tests that matter are the refusals: a missing KEK must stop the service rather than serve
plaintext, and a ciphertext must not decrypt in a place it does not belong.
"""
from __future__ import annotations

import base64
import os

import pytest

from admin_api.app.field_crypto import (
    DEK_FIELD,
    KEK_ENV,
    SecretCipher,
    SecretCryptoError,
    is_encrypted,
    require_readable,
)

KEK = base64.urlsafe_b64encode(b"k" * 32).decode()
OTHER_KEK = base64.urlsafe_b64encode(b"j" * 32).decode()
SECRETS = ("stt_token", "webhook_secret", "calendar_refresh_token")


def cipher(kek: str = KEK) -> SecretCipher:
    return SecretCipher.from_env({KEK_ENV: kek})


# ------------------------------------------------------------------------------- the round trip

def test_a_secret_survives_the_round_trip():
    data: dict = {}
    box = cipher().for_user(7, data)
    stored = box.encrypt("calendar_refresh_token", "1//0gRefreshTokenValue")

    assert stored != "1//0gRefreshTokenValue"
    assert is_encrypted(stored)
    assert box.decrypt("calendar_refresh_token", stored) == "1//0gRefreshTokenValue"


def test_the_plaintext_never_appears_in_the_stored_form():
    """A1's shape at the unit level: a dump of what we persist contains no credential."""
    data: dict = {}
    box = cipher().for_user(7, data)
    data["calendar_refresh_token"] = box.encrypt("calendar_refresh_token", "super-secret-token")

    blob = repr(data)
    assert "super-secret-token" not in blob
    assert "secret" not in blob.replace("_secret_dek", "")


def test_the_same_value_encrypts_differently_every_time():
    """A fresh nonce per encryption — equal ciphertexts would leak which users share a secret."""
    box = cipher().for_user(7, {})
    assert box.encrypt("stt_token", "same") != box.encrypt("stt_token", "same")


def test_absence_is_not_a_secret():
    box = cipher().for_user(7, {})
    assert box.encrypt("stt_token", None) is None
    assert box.encrypt("stt_token", "") == ""
    assert box.decrypt("stt_token", None) is None


# --------------------------------------------------------------------------------- the envelope

def test_the_data_key_is_stored_wrapped_never_raw():
    data: dict = {}
    cipher().for_user(7, data)

    assert data[DEK_FIELD].startswith("wrap:v1:")
    assert b"k" * 32 not in data[DEK_FIELD].encode()


def test_a_users_data_key_is_minted_once_and_reused():
    data: dict = {}
    c = cipher()
    stored = c.for_user(7, data).encrypt("stt_token", "value")
    wrapped = data[DEK_FIELD]

    assert c.for_user(7, data).decrypt("stt_token", stored) == "value"
    assert data[DEK_FIELD] == wrapped, "re-minting the DEK would orphan everything already written"


def test_rotating_the_kek_is_a_rewrap_not_a_reencrypt():
    """The entire reason for the middle key. Re-wrap the DEK under a new KEK and every existing
    ciphertext stays valid and untouched."""
    data: dict = {}
    old = cipher()
    stored = old.for_user(7, data).encrypt("stt_token", "value")

    dek = old.unwrap_dek(data[DEK_FIELD], 7)
    data[DEK_FIELD] = cipher(OTHER_KEK).wrap_dek(dek, 7)

    assert cipher(OTHER_KEK).for_user(7, data).decrypt("stt_token", stored) == "value"


# ------------------------------------------------------------------------------------- refusals

def test_a_missing_kek_stops_the_service_rather_than_serving_plaintext():
    """#876 A4. The failure this exists to prevent is a KEK that vanishes in one environment and
    a service that carries on."""
    data = {"stt_token": "enc:v1:AAAA:BBBB"}
    with pytest.raises(SecretCryptoError) as exc:
        require_readable(None, data, SECRETS)
    assert "stt_token" in str(exc.value)


def test_plaintext_rows_are_fine_without_a_kek():
    """Dual-read: a database still holding plaintext keeps working during migration."""
    require_readable(None, {"stt_token": "plain-old-token"}, SECRETS)


def test_an_unencrypted_value_reads_back_unchanged():
    box = cipher().for_user(7, {})
    assert box.decrypt("stt_token", "plain-old-token") == "plain-old-token"


def test_the_wrong_kek_raises_rather_than_returning_garbage():
    data: dict = {}
    cipher().for_user(7, data)
    with pytest.raises(SecretCryptoError):
        cipher(OTHER_KEK).for_user(7, data)


def test_a_tampered_ciphertext_is_refused():
    data: dict = {}
    box = cipher().for_user(7, data)
    stored = box.encrypt("stt_token", "value")
    tampered = stored[:-4] + ("aaaa" if not stored.endswith("aaaa") else "bbbb")

    with pytest.raises(SecretCryptoError):
        box.decrypt("stt_token", tampered)


def test_a_ciphertext_cannot_be_moved_to_another_user():
    """AAD binds it. Lifting a row between users must fail, not decrypt into the wrong account."""
    data_a: dict = {}
    c = cipher()
    stolen = c.for_user(7, data_a).encrypt("stt_token", "user-7-token")

    data_b: dict = {}
    box_b = c.for_user(9, data_b)
    with pytest.raises(SecretCryptoError):
        box_b.decrypt("stt_token", stolen)


def test_a_ciphertext_cannot_be_moved_to_another_field():
    """A webhook secret pasted into the STT token slot must not become a working STT token."""
    data: dict = {}
    box = cipher().for_user(7, data)
    stored = box.encrypt("webhook_secret", "signing-key")

    with pytest.raises(SecretCryptoError):
        box.decrypt("stt_token", stored)


# ----------------------------------------------------------------------------------- key loading

def test_no_kek_configured_is_a_supported_deployment_not_an_error():
    """Self-hosters who have not turned this on keep today's behaviour."""
    assert SecretCipher.from_env({}) is None
    assert SecretCipher.from_env({KEK_ENV: "   "}) is None


def test_a_hex_kek_works_too():
    assert SecretCipher.from_env({KEK_ENV: ("ab" * 32)}) is not None


def test_a_short_or_mistyped_kek_is_a_hard_stop():
    """A truncated key must not quietly become a weak one."""
    for bad in ("too-short", base64.urlsafe_b64encode(b"k" * 16).decode(), "zz" * 32):
        with pytest.raises(SecretCryptoError):
            SecretCipher.from_env({KEK_ENV: bad})


def test_the_error_says_how_to_generate_a_key():
    """An operator hitting this at boot should not have to search for the incantation."""
    with pytest.raises(SecretCryptoError) as exc:
        SecretCipher.from_env({KEK_ENV: "nope"})
    assert "urandom(32)" in str(exc.value)


def test_a_real_random_key_from_the_documented_recipe_loads():
    generated = base64.urlsafe_b64encode(os.urandom(32)).decode()
    assert SecretCipher.from_env({KEK_ENV: generated}) is not None
