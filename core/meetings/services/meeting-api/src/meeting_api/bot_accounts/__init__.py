"""bot_accounts — saved signed-in browser profiles a meeting bot joins as (ADR-0049).

Front door: import from here, never a deep module path.

  * ``BotAccounts`` — the brick's object: its ports (broker, session store, runtime, admin oracle,
    VNC upstream) and every rule the routes, the spawn and the lifecycle hook apply.
  * ``build_router(bot_accounts, meeting_repo=..., token_secret=...)`` — the public and internal routes.
  * ``build_bot_accounts_from_env(runtime, redis)`` — the production wiring.
  * ``BrowserBotsGate`` + ``feature`` — the ``VEXA_FEATURES`` switch ``browser_bots`` (off in a
    release install: every public route refuses ``feature_disabled``).
  * ports + adapters + fakes: ``BrokerClient`` / ``HttpBrokerClient`` / ``FakeBroker``,
    ``SessionStore`` / ``RedisSessionStore`` / ``InMemorySessionStore``, ``AdminOracle`` /
    ``HttpAdminOracle`` / ``FakeAdminOracle``, ``VncUpstream`` / ``HttpVncUpstream`` / ``FakeVncUpstream``.
"""
from __future__ import annotations

from typing import Any, Optional

from .broker import (
    NOT_CONFIGURED,
    BrokerClient,
    BrokerConflict,
    BrokerError,
    BrokerNotConfigured,
    BrokerNotFound,
    FakeBroker,
    HttpBrokerClient,
    broker_from_env,
)
from . import feature
from .feature import BrowserBotsGate
from .policy import AdminOracle, FakeAdminOracle, HttpAdminOracle, company_bot_use, usable
from .relay import FakeVncUpstream, HttpVncUpstream, VncUpstream, view_path_refusal
from .router import build_router
from .service import (
    MEETING_PLATFORM_ACCOUNT,
    PLATFORMS,
    BotAccountRefused,
    BotAccounts,
    Settings,
    userdata_key,
)
from .sessions import InMemorySessionStore, RedisSessionStore, SessionStore


def build_bot_accounts_from_env(runtime: Any, redis: Any = None, env: Optional[dict] = None) -> BotAccounts:
    """The production ``BotAccounts``: the broker from ``VEXA_BOT_ACCOUNTS_BROKER_URL`` +
    ``VEXA_BOT_ACCOUNTS_KEY_FILE`` (``None`` when unset — every route then answers 409), sessions in
    meeting-api's Redis, roles from admin-api, the live view over httpx + websockets."""
    return BotAccounts(
        broker=broker_from_env(env),
        runtime=runtime,
        store=RedisSessionStore(redis) if redis is not None else InMemorySessionStore(),
        admin=HttpAdminOracle.from_env(),
        upstream=HttpVncUpstream(),
        settings=Settings.from_env(env),
    )


__all__ = [
    "AdminOracle",
    "BotAccountRefused",
    "BotAccounts",
    "BrowserBotsGate",
    "BrokerClient",
    "BrokerConflict",
    "BrokerError",
    "BrokerNotConfigured",
    "BrokerNotFound",
    "FakeAdminOracle",
    "FakeBroker",
    "FakeVncUpstream",
    "HttpAdminOracle",
    "HttpBrokerClient",
    "HttpVncUpstream",
    "InMemorySessionStore",
    "MEETING_PLATFORM_ACCOUNT",
    "NOT_CONFIGURED",
    "PLATFORMS",
    "RedisSessionStore",
    "SessionStore",
    "Settings",
    "VncUpstream",
    "broker_from_env",
    "build_bot_accounts_from_env",
    "build_router",
    "feature",
    "company_bot_use",
    "usable",
    "userdata_key",
    "view_path_refusal",
]
