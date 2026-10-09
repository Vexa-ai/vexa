"""Deployment configuration, read once from the environment (config.v1.json is the declaration).

`load()` refuses a configuration the broker cannot honour — no role keys, no store, an unknown
backend, no gateway identity key — with one ConfigError naming each problem, so a bad deployment stops at boot instead of
at the first request. Keys are read by path on every use (a rotated key file takes effect without
a restart); only their presence is checked here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

from .config_preflight import ConfigError
from .store import address_allowed


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    key_files: Mapping[str, str]
    store: str = "local"
    store_key_file: str = ""
    openbao_addr: str = ""
    openbao_token_file: str = ""
    openbao_mount: str = "connections"
    google_client_id: str = ""
    google_client_secret: str = ""
    product_redirect: str = ""
    # gateway-identity.v1: the gateway's Ed25519 PUBLIC key. Every agent-role call must carry the
    # gateway's signature over the person it acts for; the broker can check it and cannot make one.
    identity_public_key_file: str = ""


def _env(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def load(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    settings = Settings(
        state_dir=Path(_env(env, "VEXA_CONNECTIONS_STATE_DIR", "/state")),
        key_files={
            "agent": _env(env, "VEXA_CONNECTIONS_AGENT_KEY_FILE"),
            "human": _env(env, "VEXA_CONNECTIONS_HUMAN_KEY_FILE"),
            "git": _env(env, "VEXA_CONNECTIONS_GIT_KEY_FILE"),
        },
        store=_env(env, "VEXA_CONNECTIONS_STORE", "local"),
        store_key_file=_env(env, "VEXA_CONNECTIONS_STORE_KEY_FILE"),
        openbao_addr=_env(env, "VEXA_CONNECTIONS_OPENBAO_ADDR"),
        openbao_token_file=_env(env, "VEXA_CONNECTIONS_OPENBAO_TOKEN_FILE"),
        openbao_mount=_env(env, "VEXA_CONNECTIONS_OPENBAO_MOUNT", "connections"),
        google_client_id=_env(env, "VEXA_CONNECTIONS_GOOGLE_CLIENT_ID"),
        google_client_secret=_env(env, "VEXA_CONNECTIONS_GOOGLE_CLIENT_SECRET"),
        product_redirect=_env(env, "VEXA_CONNECTIONS_PRODUCT_REDIRECT"),
        identity_public_key_file=_env(env, "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE"),
    )
    problems = []
    if not settings.identity_public_key_file:
        problems.append("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE must name the gateway's identity public key "
                        "(no agent-role call can be verified without it)")
    if settings.store == "local" and not settings.store_key_file:
        problems.append("VEXA_CONNECTIONS_STORE=local needs VEXA_CONNECTIONS_STORE_KEY_FILE")
    elif settings.store == "openbao" and not (settings.openbao_addr and settings.openbao_token_file):
        problems.append("VEXA_CONNECTIONS_STORE=openbao needs VEXA_CONNECTIONS_OPENBAO_ADDR and "
                        "VEXA_CONNECTIONS_OPENBAO_TOKEN_FILE")
    elif settings.store == "openbao" and not address_allowed(settings.openbao_addr):
        problems.append("VEXA_CONNECTIONS_OPENBAO_ADDR must be https:// (plain http:// only to a loopback "
                        "address on this host), with no credentials, query or fragment")
    elif settings.store not in ("local", "openbao"):
        problems.append("VEXA_CONNECTIONS_STORE must be `local` or `openbao`")
    if settings.product_redirect:
        parsed = urlsplit(settings.product_redirect)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.path != "/api/auth/callback/google"
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            problems.append("VEXA_CONNECTIONS_PRODUCT_REDIRECT must be https://<terminal>/api/auth/callback/google")
    if problems:
        raise ConfigError("credential-broker refuses to boot: " + "; ".join(problems))
    return settings


def google_client(settings: Settings) -> Optional[dict]:
    """The operator's Google OAuth application, or None when this deployment has none."""
    if settings.google_client_id and settings.google_client_secret:
        return {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret}
    return None
