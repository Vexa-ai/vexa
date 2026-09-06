"""Calendar connections that carry an OAuth refresh token instead of a secret feed URL.

The tests that matter are the leak tests. A refresh token is ongoing, silent read access to a
customer's whole calendar, so the interesting questions are: can it be stored in the clear (no),
can it reach a user-facing response (no), and does one unreadable token take down everybody else's
sync (no).
"""
from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from admin_api.app.calendars import (
    OAUTH_PROVIDERS,
    PROVIDER_ICS,
    REFRESH_TOKEN_FIELD,
    connections_from_data,
    internal_connections,
    masked_connection,
    new_connection,
    new_oauth_connection,
    store_connections,
)
from admin_api.app.field_crypto import KEK_ENV, SecretCipher

TOKEN = "1//0g-a-real-looking-refresh-token"
KEK = base64.urlsafe_b64encode(b"k" * 32).decode()
OTHER_KEK = base64.urlsafe_b64encode(b"j" * 32).decode()


def box_for(user_id: int, data: dict, kek: str = KEK):
    return SecretCipher.from_env({KEK_ENV: kek}).for_user(user_id, data)


def google(data: dict, user_id: int = 7, **over) -> dict:
    kwargs = dict(name="Work", provider="google", refresh_token=TOKEN,
                  account_email="Ann@Example.com", box=box_for(user_id, data))
    kwargs.update(over)
    return new_oauth_connection(**kwargs)


# ------------------------------------------------------------------------------------ at rest

def test_the_refresh_token_is_never_stored_in_the_clear():
    data: dict = {}
    connection = google(data)

    assert TOKEN not in repr(connection)
    assert connection["refresh_token"].startswith("enc:v1:")


def test_a_deployment_with_no_encryption_key_refuses_to_create_the_connection():
    """The whole point of doing #876 before this: no KEK means no OAuth calendar, not a plaintext
    token. A feed URL in the clear is one calendar's contents; this is ongoing access."""
    with pytest.raises(HTTPException) as exc:
        new_oauth_connection(name="Work", provider="google", refresh_token=TOKEN, box=None)

    assert exc.value.status_code == 503
    assert "never stored unencrypted" in exc.value.detail


def test_an_empty_or_unknown_provider_is_refused():
    data: dict = {}
    for provider in ("", "ics", "yahoo", "GOOGLE"):
        with pytest.raises(HTTPException):
            google(data, provider=provider)


def test_an_empty_refresh_token_is_refused():
    data: dict = {}
    with pytest.raises(HTTPException):
        google(data, refresh_token="   ")


# ------------------------------------------------------------------------------ the user-facing shape

def test_the_masked_shape_cannot_carry_the_token():
    data: dict = {}
    masked = masked_connection(google(data))

    assert "refresh_token" not in masked
    assert TOKEN not in repr(masked)
    assert masked["provider"] == "google"
    assert masked["connected"] is True
    assert masked["account_email"] == "ann@example.com"


def test_masking_is_an_allowlist_so_a_future_secret_is_invisible_by_default():
    """A denylist leaks whatever anybody adds next. This is the regression that matters more than
    any single field."""
    data: dict = {}
    connection = google(data)
    connection["some_new_credential_nobody_thought_about"] = "leak-me"

    assert "leak-me" not in repr(masked_connection(connection))


def test_an_ics_connection_still_masks_exactly_as_before():
    connection = new_connection(name="Work", ics_url="https://calendar.google.com/x/private-a/basic.ics")
    masked = masked_connection(connection)

    assert masked["provider"] == PROVIDER_ICS
    assert masked["ics_url_set"] is True
    assert masked["ics_url_masked"].startswith("calendar.google.com/")
    assert "account_email" not in masked and "connected" not in masked


# --------------------------------------------------------------------------------- the internal hop

def test_the_internal_edge_hands_over_a_decrypted_token():
    data: dict = {}
    data = store_connections(data, [google(data)])

    (entry,) = internal_connections(data, 7, cipher=SecretCipher.from_env({KEK_ENV: KEK}))

    assert entry["provider"] == "google"
    assert entry["refresh_token"] == TOKEN
    assert entry["provider_calendar_id"] == "primary"
    assert entry["auto_join"] is True


def test_an_unreadable_token_skips_that_calendar_and_never_stalls_the_others():
    """A rotated KEK or a restored-from-backup row must not blank the whole config list — that
    would stop every other user's calendar in the same sweep."""
    data: dict = {}
    good = new_connection(name="Feed", ics_url="https://calendar.google.com/x/private-a/basic.ics")
    data = store_connections(data, [google(data), good])

    entries = internal_connections(data, 7, cipher=SecretCipher.from_env({KEK_ENV: OTHER_KEK}))

    oauth = next(e for e in entries if e.get("provider") == "google")
    assert oauth["unreadable"] is True
    assert "refresh_token" not in oauth
    assert any(e.get("ics_url") for e in entries), "the healthy feed still syncs"


def test_no_box_at_all_marks_the_connection_unreadable_rather_than_syncing_nothing_silently():
    data: dict = {}
    data = store_connections(data, [google(data)])

    (entry,) = internal_connections(data, 7, cipher=None)

    assert entry["unreadable"] is True


def test_a_paused_oauth_connection_is_a_tombstone_like_any_other():
    data: dict = {}
    connection = google(data)
    connection["enabled"] = False
    data = store_connections(data, [connection])

    (entry,) = internal_connections(data, 7, cipher=SecretCipher.from_env({KEK_ENV: KEK}))

    assert entry["paused"] is True
    assert "refresh_token" not in entry


# ------------------------------------------------------------------------------- legacy mirroring

def test_an_oauth_connection_never_writes_the_legacy_feed_url_keys():
    """`calendar_ics_url` means 'a feed URL' to every reader that predates OAuth. Mirroring an
    OAuth connection into it would hand them something they cannot fetch."""
    data: dict = {}
    out = store_connections({}, [google(data)])

    assert "calendar_ics_url" not in out


def test_an_ics_connection_alongside_an_oauth_one_still_mirrors():
    data: dict = {}
    feed = new_connection(name="Feed", ics_url="https://calendar.google.com/x/private-a/basic.ics")
    out = store_connections({}, [google(data), feed])

    assert out["calendar_ics_url"] == feed["ics_url"]


def test_existing_connections_without_a_provider_still_load():
    """Every row written before this change has no `provider` key."""
    legacy = {"id": "abc", "name": "Calendar", "ics_url": "https://x/basic.ics", "enabled": True}
    (loaded,) = connections_from_data({"calendar_connections": [legacy]}, 7)

    assert masked_connection(loaded)["provider"] == PROVIDER_ICS


def test_the_field_name_is_shared_with_the_cipher_so_aad_matches():
    """field_crypto binds ciphertext to (user, field). Encrypting under one field name and
    decrypting under another fails authentication — so both sides must use this constant."""
    assert REFRESH_TOKEN_FIELD == "calendar_refresh_token"
    assert OAUTH_PROVIDERS == {"google", "microsoft"}
