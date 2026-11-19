"""Where the signal model's key comes from: the credential broker, never this process's environment.

``secret_ref`` (from ``model_signals``) is ``broker:signals/<name>`` or ``none`` (a keyless endpoint,
e.g. a bank's own LiteLLM inside its network). A broker reference is read with the ``signals`` role
key (``VEXA_SIGNALS_BROKER_KEY_FILE``) from ``POST /api/internal/signals/model-key`` and kept in
memory for five minutes. Nothing here logs or returns the value anywhere but to the adapter.

DEV FALLBACK, ``VEXA_ENV=dev`` ONLY: when the broker is not configured here or holds no such name,
``SIGNALS_OPENROUTER_API_KEY`` is used. Any other ``VEXA_ENV`` (or none) never reads that variable,
so a production deployment cannot be keyed from its environment by accident.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from . import broker_assertion

log = logging.getLogger("signals.keys")

ROUTE = "/api/internal/signals/model-key"
CACHE_S = 300.0
DEV_FALLBACK = "SIGNALS_OPENROUTER_API_KEY"


class KeyUnavailable(RuntimeError):
    """No key could be had for the policy's secret_ref; the message never holds a value."""


class KeySource:
    def __init__(self, broker_url: str = "", key_file: str = "", *, env: Optional[Mapping[str, str]] = None,
                 transport=None, now: Callable[[], float] = time.monotonic):
        self.base = str(broker_url or "").strip().rstrip("/")
        self.key_file = str(key_file or "").strip()
        self.env = os.environ if env is None else env
        self.transport, self.now = transport, now
        self._cache: dict = {}

    @property
    def broker_configured(self) -> bool:
        return bool(self.base and self.key_file)

    def _dev_fallback(self) -> Optional[str]:
        env = self.env if self.env is not os.environ else {
            "VEXA_ENV": os.environ.get("VEXA_ENV", ""),
            DEV_FALLBACK: os.environ.get("SIGNALS_OPENROUTER_API_KEY", "")}
        if str(env.get("VEXA_ENV", "")).strip().lower() != "dev":
            return None
        value = str(env.get(DEV_FALLBACK, "")).strip()
        return value or None

    async def resolve(self, secret_ref: str) -> Optional[str]:
        """The key ``secret_ref`` names, None for ``none``; raises KeyUnavailable otherwise."""
        ref = str(secret_ref or "none")
        if ref == "none":
            return None
        if not ref.startswith("broker:signals/"):
            raise KeyUnavailable("secret_ref must be broker:signals/<name> or none")
        name = ref.split("/", 1)[1]
        hit = self._cache.get(name)
        if hit and self.now() - hit[1] < CACHE_S:
            return hit[0]
        value = await self._from_broker(name) if self.broker_configured else None
        if value is None:
            value = self._dev_fallback()
            if value is None:
                raise KeyUnavailable(
                    f"no signal-model key for {name!r}: store it in the credential broker "
                    f"(python -m credential_broker.signals_key put {name}) and give signals the "
                    "signals role key (VEXA_SIGNALS_BROKER_URL + VEXA_SIGNALS_BROKER_KEY_FILE)")
            log.warning("keys: using the dev fallback %s (VEXA_ENV=dev) for %r", DEV_FALLBACK, name)
        self._cache[name] = (value, self.now())
        return value

    def forget(self, secret_ref: str) -> None:
        """Drop a cached key (the model refused it), so the next call reads it afresh."""
        self._cache.pop(str(secret_ref or "").split("/", 1)[-1], None)

    async def _from_broker(self, name: str) -> Optional[str]:
        try:
            key = broker_assertion.load_key(Path(self.key_file))
        except Exception as exc:  # noqa: BLE001 — a missing/short key file is a configuration fault
            raise KeyUnavailable(f"the signals role key file is unusable ({type(exc).__name__})") from None
        body = json.dumps({"name": name}, separators=(",", ":")).encode()
        header = broker_assertion.sign(key, role="signals", actor="signals", session="signals-" + uuid.uuid4().hex,
                                       method="POST", path=ROUTE, body=body)
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=5.0) as client:
                r = await client.post(self.base + ROUTE, content=body,
                                      headers={broker_assertion.HEADER: header, "content-type": "application/json"})
        except httpx.HTTPError as exc:
            raise KeyUnavailable(f"the credential broker did not answer ({type(exc).__name__})") from None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise KeyUnavailable(f"the credential broker refused the signals role (HTTP {r.status_code})")
        try:
            value = r.json().get("value")
        except ValueError:
            value = None
        if not isinstance(value, str) or not value:
            raise KeyUnavailable("the credential broker answered without a key")
        return value
