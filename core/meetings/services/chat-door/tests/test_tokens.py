"""Magic-link tokens: issue · verify · expiry · single-use · tamper · wrong key."""
from __future__ import annotations

import time

import pytest

from chat_door.config import SigningKey, load_config
from chat_door.tokens import (
    REASON_BAD_SIGNATURE,
    REASON_EXPIRED,
    REASON_MALFORMED,
    REASON_REPLAYED,
    REASON_WRONG_KIND,
    TokenError,
    TokenSigner,
    build_magic_link,
)

KEY = b"key-under-test"


def issue(signer: TokenSigner, **kw):
    base = dict(kind="link", subject="a@example.test", meeting_id="126", scope="guest",
                ttl_seconds=600)
    base.update(kw)
    return signer.issue(**base)


def test_round_trip_carries_every_claim():
    s = TokenSigner(KEY)
    claims = s.verify(issue(s), expect_kind="link")
    assert (claims.subject, claims.meeting_id, claims.scope) == ("a@example.test", "126", "guest")
    assert claims.expires_at > int(time.time())


def test_link_is_single_use():
    s = TokenSigner(KEY)
    token = issue(s)
    s.verify(token, expect_kind="link")
    with pytest.raises(TokenError) as exc:
        s.verify(token, expect_kind="link")
    assert exc.value.reason == REASON_REPLAYED


def test_session_token_is_reusable():
    s = TokenSigner(KEY)
    token = issue(s, kind="session")
    for _ in range(3):
        assert s.verify(token, expect_kind="session").subject == "a@example.test"


def test_expiry_is_enforced():
    s = TokenSigner(KEY)
    token = issue(s, ttl_seconds=1, now=int(time.time()) - 10)
    with pytest.raises(TokenError) as exc:
        s.verify(token, expect_kind="link")
    assert exc.value.reason == REASON_EXPIRED


def test_expired_token_is_not_burned_before_expiry_check():
    """An expired token must report expiry, not consume the jti and report replay."""
    s = TokenSigner(KEY)
    token = issue(s, ttl_seconds=1, now=int(time.time()) - 10)
    for _ in range(2):
        with pytest.raises(TokenError) as exc:
            s.verify(token, expect_kind="link")
        assert exc.value.reason == REASON_EXPIRED


def test_tampered_payload_fails_signature():
    s = TokenSigner(KEY)
    body, mac = issue(s).split(".")
    other = TokenSigner(KEY).issue(kind="link", subject="b@example.test", meeting_id="126",
                                   scope="guest", ttl_seconds=600)
    with pytest.raises(TokenError) as exc:
        s.verify(f"{other.split('.')[0]}.{mac}", expect_kind="link")
    assert exc.value.reason == REASON_BAD_SIGNATURE
    assert body  # the original payload half is what we swapped away from


def test_another_key_cannot_verify():
    token = issue(TokenSigner(KEY))
    with pytest.raises(TokenError) as exc:
        TokenSigner(b"a-different-key").verify(token, expect_kind="link")
    assert exc.value.reason == REASON_BAD_SIGNATURE


def test_kind_confusion_is_rejected():
    s = TokenSigner(KEY)
    with pytest.raises(TokenError) as exc:
        s.verify(issue(s, kind="session"), expect_kind="link")
    assert exc.value.reason == REASON_WRONG_KIND


@pytest.mark.parametrize("garbage", ["", "nodot", "a.b.c", "!!!.???"])
def test_garbage_is_malformed(garbage: str):
    with pytest.raises(TokenError) as exc:
        TokenSigner(KEY).verify(garbage, expect_kind="link")
    assert exc.value.reason in (REASON_MALFORMED, REASON_BAD_SIGNATURE)


def test_magic_link_shape():
    assert build_magic_link("http://door.test/", "tok").endswith("/door/verify?t=tok")


# -- the key never renders ------------------------------------------------------

def test_signing_key_never_renders_its_material():
    key = SigningKey(b"super-secret-material")
    for rendered in (repr(key), str(key), f"{key}"):
        assert "super-secret-material" not in rendered
        assert key.fingerprint in rendered


def test_config_repr_hides_key_and_api_key():
    cfg = load_config({
        "CHAT_DOOR_SIGNING_KEY": "super-secret-material",
        "CHAT_DOOR_MEETINGS_API_KEY": "secret-api-key",
    })
    rendered = repr(cfg)
    assert "super-secret-material" not in rendered
    assert "secret-api-key" not in rendered
    assert cfg.signing_key.generated is False


def test_missing_key_generates_an_ephemeral_one_and_says_so():
    cfg = load_config({})
    assert cfg.signing_key.generated is True
    assert len(bytes(cfg.signing_key)) >= 32
