"""invocation.v1 boot config — read + validate ``VEXA_BOT_CONFIG`` at boot (ADR-0002, fail-fast).

Mirrors ``services/bot/src/config.ts``'s ``loadInvocation``/``InvocationError`` for this platform
lane's own env-var contract: one JSON env var, validated against the PUBLISHED schema before
anything else runs. A parse/validation failure is fatal — the caller maps it to a lifecycle.v1
``failed``/``validation_error`` (see ``bot.py``'s ``run``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import jsonschema

from discord_bot.contracts import conforms_invocation


class InvocationError(Exception):
    """Raised when ``VEXA_BOT_CONFIG`` is missing / not JSON / off-contract, or names a platform
    other than ``discord``. The composition root maps this to a lifecycle.v1
    ``failed(validation_error, failure_stage=requested)``."""


def parse_invocation(raw: Optional[str]) -> dict[str, Any]:
    """Parse + validate a raw JSON string against invocation.v1, or raise ``InvocationError``."""
    if not raw or not raw.strip():
        raise InvocationError("invocation.v1: VEXA_BOT_CONFIG env is missing or empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvocationError(f"invocation.v1: VEXA_BOT_CONFIG is not valid JSON — {e}") from e
    if not isinstance(data, dict):
        raise InvocationError("invocation.v1: VEXA_BOT_CONFIG must decode to a JSON object")
    try:
        conforms_invocation(data)
    except jsonschema.ValidationError as e:
        raise InvocationError(f"invocation.v1: VEXA_BOT_CONFIG failed validation — {e.message}") from e
    # The schema's Platform enum is shared by every meeting-bot kind; this service only ever
    # speaks for "discord" (a stray dispatch to the wrong profile's image is a config bug, not
    # something to silently join anyway).
    if data.get("platform") != "discord":
        raise InvocationError(f"invocation.v1: platform {data.get('platform')!r} is not 'discord'")
    return data


def load_invocation(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Boot helper — read ``VEXA_BOT_CONFIG`` from the environment and validate it (P7: config by
    env). Falls back to the legacy ``BOT_CONFIG`` alias ``build_workload_spec`` also emits for the
    0.11-derived bot image, so this service boots identically under either name."""
    env = os.environ if env is None else env
    raw = env.get("VEXA_BOT_CONFIG") or env.get("BOT_CONFIG")
    return parse_invocation(raw)
