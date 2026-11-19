"""The authenticated-bot session config — one reading for the spawn and for the session write-back.

``BOT_AUTHENTICATED`` is a deployment property (#724): every spawn restores the browser session
stored at ``BOT_USERDATA_S3_PATH`` with the ``BOT_S3_*`` key pair. That pair rides each bot's
invocation into its container, so it is the bots' key and nothing more: READ-ONLY on the userdata
prefix (Compose's storage-init grants it Get + List there and nothing else). A bot's rotated session
comes back through meeting-api (``session_profile``), which writes it with meeting-api's own storage
credentials.

Two refusals live here, so the spawn and the write-back route apply the same ones:

* the knob is on but the store is incomplete (``AuthSessionNotConfigured`` — 503);
* the bots' pair reuses half of a storage root pair (``MINIO_*`` or ``S3_*``) — the bots would then
  carry a key that can read and write every object in the store. Refused at every spawn, before any
  row is written or any bot started; the message names the variables, never a value.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Mapping, Optional

from .env_flags import env_flag
from .ports import AuthSessionNotConfigured

#: The storage root pairs a deployment may define: the bundled storage's (``MINIO_*``) and an
#: operator S3's (``S3_*``). Neither half of either may be a bot's.
STORAGE_ROOT_PAIRS: tuple[tuple[str, str], ...] = (
    ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"),
    ("S3_ACCESS_KEY", "S3_SECRET_KEY"),
)


@dataclass(frozen=True)
class AuthSessionConfig:
    """Where the deployment's authenticated session lives, and the bots' read-only key for it."""

    userdata_path: str
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: Optional[str]
    s3_secret_key: Optional[str]


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def storage_root_reuse(env: Optional[Mapping[str, str]] = None) -> list[str]:
    """Each equality between a half of the bots' pair and the same half of a storage root pair, as
    ``"BOT_S3_ACCESS_KEY = MINIO_ACCESS_KEY"``. Empty values never match. Names only, never values."""
    env = os.environ if env is None else env
    bot = {
        "access": ("BOT_S3_ACCESS_KEY", (env.get("BOT_S3_ACCESS_KEY") or "").strip()),
        "secret": ("BOT_S3_SECRET_KEY", (env.get("BOT_S3_SECRET_KEY") or "").strip()),
    }
    found: list[str] = []
    for access_name, secret_name in STORAGE_ROOT_PAIRS:
        for half, root_name in (("access", access_name), ("secret", secret_name)):
            bot_name, bot_value = bot[half]
            root_value = (env.get(root_name) or "").strip()
            if bot_value and root_value and _same(bot_value, root_value):
                found.append(f"{bot_name} = {root_name}")
    return found


def auth_session_config(env: Optional[Mapping[str, str]] = None) -> Optional[AuthSessionConfig]:
    """The authenticated-session config, ``None`` when ``BOT_AUTHENTICATED`` is off.

    Raises ``AuthSessionNotConfigured`` when the knob is on and the store is incomplete, or when the
    bots' pair reuses a storage root key or secret."""
    env = os.environ if env is None else env
    if not env_flag("BOT_AUTHENTICATED", False, raw=env.get("BOT_AUTHENTICATED") or ""):
        return None
    userdata_path = env.get("BOT_USERDATA_S3_PATH") or None
    endpoint = env.get("BOT_S3_ENDPOINT") or None
    bucket = env.get("BOT_S3_BUCKET") or None
    if not (userdata_path and endpoint and bucket):
        raise AuthSessionNotConfigured(
            "BOT_AUTHENTICATED is set but the userdata store is incomplete — set "
            "BOT_USERDATA_S3_PATH + BOT_S3_ENDPOINT + BOT_S3_BUCKET (and scoped "
            "BOT_S3_ACCESS_KEY/BOT_S3_SECRET_KEY); provision the session with `make login`"
        )
    reused = storage_root_reuse(env)
    if reused:
        raise AuthSessionNotConfigured(
            "the bots' storage key pair reuses the storage root pair (" + "; ".join(reused) + ") — "
            "give BOT_S3_ACCESS_KEY / BOT_S3_SECRET_KEY their own read-only pair; no bot was spawned"
        )
    return AuthSessionConfig(
        userdata_path=userdata_path,
        s3_endpoint=endpoint,
        s3_bucket=bucket,
        s3_access_key=env.get("BOT_S3_ACCESS_KEY") or None,
        s3_secret_key=env.get("BOT_S3_SECRET_KEY") or None,
    )
