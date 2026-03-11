"""Tests for token prefix scoping."""

import pytest
from shared_models.token_scope import (
    generate_prefixed_token,
    parse_token_scope,
    check_token_scope,
    VALID_SCOPES,
    TOKEN_PATTERN,
)


class TestGeneratePrefixedToken:
    def test_format(self):
        token = generate_prefixed_token("user")
        assert token.startswith("vxa_user_")
        parts = token.split("_", 2)
        assert len(parts) == 3
        assert parts[0] == "vxa"
        assert parts[1] == "user"
        assert len(parts[2]) == 32  # default length

    def test_all_scopes(self):
        for scope in VALID_SCOPES:
            token = generate_prefixed_token(scope)
            assert token.startswith(f"vxa_{scope}_")

    def test_custom_length(self):
        token = generate_prefixed_token("bot", length=16)
        random_part = token.split("_", 2)[2]
        assert len(random_part) == 16

    def test_invalid_scope(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            generate_prefixed_token("invalid")

    def test_uniqueness(self):
        tokens = {generate_prefixed_token("user") for _ in range(100)}
        assert len(tokens) == 100


class TestParseTokenScope:
    def test_prefixed_tokens(self):
        assert parse_token_scope("vxa_user_abc123") == "user"
        assert parse_token_scope("vxa_bot_xyz789") == "bot"
        assert parse_token_scope("vxa_tx_def456") == "tx"
        assert parse_token_scope("vxa_admin_ghi012") == "admin"

    def test_legacy_tokens(self):
        # Alphanumeric (admin-api style)
        assert parse_token_scope("AbCdEfGhIjKlMnOpQrStUvWxYz01234567890123") is None
        # URL-safe base64 (gateway style)
        assert parse_token_scope("dGhpcyBpcyBhIHRlc3QgdG9rZW4") is None

    def test_malformed_prefix(self):
        assert parse_token_scope("vxa_") is None
        assert parse_token_scope("vxa__abc") is None
        assert parse_token_scope("VXA_user_abc") is None  # uppercase
        assert parse_token_scope("vxb_user_abc") is None  # wrong prefix

    def test_roundtrip(self):
        for scope in VALID_SCOPES:
            token = generate_prefixed_token(scope)
            assert parse_token_scope(token) == scope


class TestCheckTokenScope:
    def test_matching_scope(self):
        assert check_token_scope("vxa_user_abc", {"user", "admin"}) is True
        assert check_token_scope("vxa_bot_abc", {"bot", "user", "admin"}) is True
        assert check_token_scope("vxa_tx_abc", {"tx"}) is True

    def test_wrong_scope(self):
        assert check_token_scope("vxa_tx_abc", {"bot"}) is False
        assert check_token_scope("vxa_bot_abc", {"tx"}) is False
        assert check_token_scope("vxa_user_abc", {"tx", "bot"}) is False

    def test_legacy_always_allowed(self):
        # Legacy tokens without prefix always pass
        assert check_token_scope("some_old_token_without_prefix", {"bot"}) is True
        assert check_token_scope("AbCdEf123456", {"tx"}) is True
        assert check_token_scope("dGhpcyBpcyBh", set()) is True

    def test_admin_scope_access(self):
        # Admin tokens should work where admin is allowed
        assert check_token_scope("vxa_admin_abc", {"admin"}) is True
        assert check_token_scope("vxa_admin_abc", {"user", "admin"}) is True
        # But not where only specific scopes are allowed
        assert check_token_scope("vxa_admin_abc", {"bot"}) is False


class TestTokenPattern:
    def test_regex_matches_valid(self):
        assert TOKEN_PATTERN.match("vxa_user_abc123")
        assert TOKEN_PATTERN.match("vxa_tx_a")

    def test_regex_rejects_invalid(self):
        assert not TOKEN_PATTERN.match("vxa_USER_abc")  # uppercase scope
        assert not TOKEN_PATTERN.match("vxa__abc")  # empty scope
        assert not TOKEN_PATTERN.match("abc_user_def")  # wrong prefix
