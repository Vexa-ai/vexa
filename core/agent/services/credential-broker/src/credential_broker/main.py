"""Composition root: environment → preflight → settings → store → app.

`uvicorn credential_broker.main:app` resolves `app` lazily (PEP 562), so importing this package in
a test never boots a broker from the test process's environment.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping, Optional

from fastapi import FastAPI

from . import assertion, identity_token
from .app import Broker, create_app
from .config_preflight import ConfigError, preflight
from .obs import log_event
from .settings import Settings, load
from .store import LocalEncryptedStore, OpenBaoStore, Store, StoreUnavailable, load_store_key


def build_store(settings: Settings) -> Store:
    if settings.store == "openbao":
        return OpenBaoStore(settings.openbao_addr, Path(settings.openbao_token_file), settings.openbao_mount)
    Path(settings.state_dir).mkdir(parents=True, exist_ok=True)
    try:
        key = load_store_key(settings.store_key_file)
    except StoreUnavailable:
        raise ConfigError("credential-broker refuses to boot: VEXA_CONNECTIONS_STORE_KEY_FILE must name a "
                          "readable file holding 64 hex characters (`openssl rand -hex 32`)") from None
    return LocalEncryptedStore(Path(settings.state_dir) / "secrets.sqlite", key)


def build_app(env: Optional[Mapping[str, str]] = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    # Files this process creates (metadata, the encrypted store, the PKCE key) are its own alone.
    os.umask(0o077)
    preflight(env)
    settings = load(env)
    # gateway-identity.v1: read per use like the role keys, but a file that is not the gateway's
    # Ed25519 public key (its private key included — a broker holding it could sign) stops the boot.
    try:
        identity_token.read_verify_key(settings.identity_public_key_file)
    except identity_token.KeyUnavailable as e:
        raise ConfigError(f"credential-broker refuses to boot: VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE — {e}") from None
    # credential-broker.v1: a role key file holding a fixture key from the contract's goldens would
    # accept assertions anybody can sign, so it stops the boot. Any other unusable role key is still
    # refused per use, as before.
    for role, path in settings.key_files.items():
        try:
            assertion.load_key(path)
        except assertion.PublishedKey as e:
            raise ConfigError(f"credential-broker refuses to boot: VEXA_CONNECTIONS_{role.upper()}_KEY_FILE — {e}; "
                              "generate this deployment's own key (`openssl rand -hex 32`)") from None
        except assertion.KeyUnavailable:
            pass
    store = build_store(settings)
    log_event("broker_started", fields={"store": store.name, "git_store": bool(settings.key_files.get("git")),
                                        "google_oauth": bool(settings.google_client_id and settings.product_redirect)})
    return create_app(Broker(settings, store))


def __getattr__(name: str):
    if name == "app":
        built = build_app()
        globals()["app"] = built
        return built
    raise AttributeError(name)
