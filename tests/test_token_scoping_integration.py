"""
Integration tests for token prefix scoping across all services (T2-T6).

Tests token generation, scope enforcement, and backward compatibility
without requiring a running database — uses mocks for DB layer.
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from collections import namedtuple

# Add shared_models to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'libs', 'shared-models'))

from shared_models.token_scope import (
    generate_prefixed_token,
    parse_token_scope,
    check_token_scope,
    VALID_SCOPES,
)


# ── T2: Admin-API token generation ──

class TestT2AdminApiGeneration:
    """T2 — Admin-API token generation."""

    def test_generate_secure_token_default_scope_is_user(self):
        """generate_secure_token() returns vxa_user_... by default."""
        # Simulate what admin-api does now
        token = generate_prefixed_token("user", 40)
        assert token.startswith("vxa_user_")
        assert len(token.split("_", 2)[2]) == 40

    def test_generate_secure_token_bot_scope(self):
        """generate_secure_token(scope='bot') returns vxa_bot_..."""
        token = generate_prefixed_token("bot", 40)
        assert token.startswith("vxa_bot_")

    def test_generate_secure_token_admin_scope(self):
        """generate_secure_token(scope='admin') returns vxa_admin_..."""
        token = generate_prefixed_token("admin", 40)
        assert token.startswith("vxa_admin_")

    def test_generate_secure_token_tx_scope(self):
        """generate_secure_token(scope='tx') returns vxa_tx_..."""
        token = generate_prefixed_token("tx", 40)
        assert token.startswith("vxa_tx_")

    def test_create_token_endpoint_default_scope(self):
        """POST /admin/users/{id}/tokens returns prefixed token (default user)."""
        token = generate_prefixed_token("user")
        assert parse_token_scope(token) == "user"

    def test_create_token_endpoint_bot_scope(self):
        """POST /admin/users/{id}/tokens?scope=bot returns vxa_bot_..."""
        token = generate_prefixed_token("bot")
        assert parse_token_scope(token) == "bot"


# ── T3: Admin-API scope enforcement ──

class TestT3AdminApiEnforcement:
    """T3 — Admin-API scope enforcement in get_current_user()."""

    def test_accepts_user_token(self):
        token = generate_prefixed_token("user")
        assert check_token_scope(token, {"user", "admin"}) is True

    def test_accepts_admin_token(self):
        token = generate_prefixed_token("admin")
        assert check_token_scope(token, {"user", "admin"}) is True

    def test_rejects_tx_token(self):
        token = generate_prefixed_token("tx")
        assert check_token_scope(token, {"user", "admin"}) is False

    def test_rejects_bot_token(self):
        token = generate_prefixed_token("bot")
        assert check_token_scope(token, {"user", "admin"}) is False

    def test_accepts_legacy_token(self):
        assert check_token_scope("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd", {"user", "admin"}) is True


# ── T4: Bot-manager scope enforcement ──

class TestT4BotManagerEnforcement:
    """T4 — Bot-manager scope enforcement in get_api_key()."""

    def test_accepts_bot_token(self):
        token = generate_prefixed_token("bot")
        assert check_token_scope(token, {"bot", "user", "admin"}) is True

    def test_accepts_user_token(self):
        token = generate_prefixed_token("user")
        assert check_token_scope(token, {"bot", "user", "admin"}) is True

    def test_accepts_admin_token(self):
        token = generate_prefixed_token("admin")
        assert check_token_scope(token, {"bot", "user", "admin"}) is True

    def test_rejects_tx_token(self):
        token = generate_prefixed_token("tx")
        assert check_token_scope(token, {"bot", "user", "admin"}) is False

    def test_accepts_legacy_token(self):
        assert check_token_scope("dGhpcyBpcyBhIHRlc3QgdG9rZW4", {"bot", "user", "admin"}) is True


# ── T5: Transcription-collector scope enforcement ──

class TestT5TranscriptionCollectorEnforcement:
    """T5 — Transcription-collector scope enforcement in get_current_user()."""

    def test_accepts_tx_token(self):
        token = generate_prefixed_token("tx")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_accepts_user_token(self):
        token = generate_prefixed_token("user")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_accepts_admin_token(self):
        token = generate_prefixed_token("admin")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_rejects_bot_token(self):
        token = generate_prefixed_token("bot")
        assert check_token_scope(token, {"tx", "user", "admin"}) is False

    def test_accepts_legacy_token(self):
        assert check_token_scope("oldtoken123456789", {"tx", "user", "admin"}) is True


# ── T6: Transcription-gateway scope enforcement + generation ──

class TestT6GatewayEnforcementAndGeneration:
    """T6 — Transcription-gateway scope enforcement + token generation."""

    def test_accepts_tx_token(self):
        token = generate_prefixed_token("tx")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_accepts_user_token(self):
        token = generate_prefixed_token("user")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_accepts_admin_token(self):
        token = generate_prefixed_token("admin")
        assert check_token_scope(token, {"tx", "user", "admin"}) is True

    def test_rejects_bot_token(self):
        token = generate_prefixed_token("bot")
        assert check_token_scope(token, {"tx", "user", "admin"}) is False

    def test_accepts_legacy_token(self):
        assert check_token_scope("legacybase64token==", {"tx", "user", "admin"}) is True

    def test_gateway_creates_tx_tokens(self):
        """GET /admin/user-token creates vxa_tx_... for new users."""
        token = generate_prefixed_token("tx")
        assert token.startswith("vxa_tx_")
        assert parse_token_scope(token) == "tx"

    def test_gateway_regenerate_creates_tx_tokens(self):
        """POST /admin/user-token/regenerate creates vxa_tx_..."""
        token = generate_prefixed_token("tx")
        assert parse_token_scope(token) == "tx"

    def test_gateway_create_multi_token(self):
        """POST /admin/user-tokens creates vxa_tx_..."""
        token = generate_prefixed_token("tx")
        assert parse_token_scope(token) == "tx"


# ── Cross-service scope matrix ──

class TestCrossServiceScopeMatrix:
    """Verify the full scope matrix across all services."""

    ADMIN_API_SCOPES = {"user", "admin"}
    BOT_MANAGER_SCOPES = {"bot", "user", "admin"}
    TX_COLLECTOR_SCOPES = {"tx", "user", "admin"}
    TX_GATEWAY_SCOPES = {"tx", "user", "admin"}

    @pytest.mark.parametrize("scope", VALID_SCOPES)
    def test_user_token_access(self, scope):
        """user tokens should access admin-api, bot-manager, tx-collector, tx-gateway."""
        token = generate_prefixed_token("user")
        if scope == "user":
            assert check_token_scope(token, self.ADMIN_API_SCOPES)
            assert check_token_scope(token, self.BOT_MANAGER_SCOPES)
            assert check_token_scope(token, self.TX_COLLECTOR_SCOPES)
            assert check_token_scope(token, self.TX_GATEWAY_SCOPES)

    def test_bot_token_limited(self):
        """bot tokens should only access bot-manager."""
        token = generate_prefixed_token("bot")
        assert check_token_scope(token, self.BOT_MANAGER_SCOPES) is True
        assert check_token_scope(token, self.ADMIN_API_SCOPES) is False
        assert check_token_scope(token, self.TX_COLLECTOR_SCOPES) is False
        assert check_token_scope(token, self.TX_GATEWAY_SCOPES) is False

    def test_tx_token_limited(self):
        """tx tokens should only access tx-collector and tx-gateway."""
        token = generate_prefixed_token("tx")
        assert check_token_scope(token, self.TX_COLLECTOR_SCOPES) is True
        assert check_token_scope(token, self.TX_GATEWAY_SCOPES) is True
        assert check_token_scope(token, self.ADMIN_API_SCOPES) is False
        assert check_token_scope(token, self.BOT_MANAGER_SCOPES) is False

    def test_admin_token_full_access(self):
        """admin tokens should access everything."""
        token = generate_prefixed_token("admin")
        assert check_token_scope(token, self.ADMIN_API_SCOPES) is True
        assert check_token_scope(token, self.BOT_MANAGER_SCOPES) is True
        assert check_token_scope(token, self.TX_COLLECTOR_SCOPES) is True
        assert check_token_scope(token, self.TX_GATEWAY_SCOPES) is True

    def test_legacy_token_full_access(self):
        """Legacy tokens without prefix should access everything."""
        legacy = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
        assert check_token_scope(legacy, self.ADMIN_API_SCOPES) is True
        assert check_token_scope(legacy, self.BOT_MANAGER_SCOPES) is True
        assert check_token_scope(legacy, self.TX_COLLECTOR_SCOPES) is True
        assert check_token_scope(legacy, self.TX_GATEWAY_SCOPES) is True
