"""boot.py — agent-api's workspace store, from its environment.

``VEXA_WORKSPACE_BACKEND`` is ``volume`` (the default: the tree on the mounted volume is the truth,
nothing here runs) or ``s3``. With ``s3`` the boot

1. builds the bucket adapter from ``VEXA_WORKSPACE_S3_*`` and runs the self-test (write, read and
   delete a probe object, then probe conditional PUT and versioning) — a failure stops the boot with
   a sentence naming the endpoint, the bucket and the failed call;
2. hydrates every repository and platform file into ``VEXA_WORKSPACES_DIR``, which is now a cache;
3. installs the ref observer on every copy of ``gitexec`` and returns the platform-file sync.

The lock service is the deployment's Redis (``REDIS_URL``); without one the boot refuses, because
the lock is the guard that works on every S3.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .cache import CacheManager
from .client import install_observer, install_path_observer
from .lock import WorkspaceLocks
from .objects import S3Objects, S3Settings, SelfTest, self_test
from .observer import CacheSync, StoreObserver
from .repostore import RepoStore

logger = logging.getLogger("workspaces.store")

BACKENDS = ("volume", "s3")


class StoreMisconfigured(RuntimeError):
    """The s3 backend cannot run as configured; the message says what to fix."""


def backend(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    value = (env.get("VEXA_WORKSPACE_BACKEND") or "volume").strip().lower()
    if value not in BACKENDS:
        raise StoreMisconfigured(f"VEXA_WORKSPACE_BACKEND must be one of {', '.join(BACKENDS)} (got {value!r})")
    return value


def _flag(raw: Optional[str], default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def s3_settings(env: Optional[Mapping[str, str]] = None) -> S3Settings:
    env = os.environ if env is None else env
    s = S3Settings(
        bucket=(env.get("VEXA_WORKSPACE_S3_BUCKET") or "").strip(),
        endpoint=(env.get("VEXA_WORKSPACE_S3_ENDPOINT") or "").strip(),
        region=(env.get("VEXA_WORKSPACE_S3_REGION") or "").strip(),
        access_key=(env.get("VEXA_WORKSPACE_S3_ACCESS_KEY") or "").strip(),
        secret_key=(env.get("VEXA_WORKSPACE_S3_SECRET_KEY") or "").strip(),
        path_style=_flag(env.get("VEXA_WORKSPACE_S3_PATH_STYLE"), True),
        ca_bundle=(env.get("VEXA_WORKSPACE_S3_CA_BUNDLE") or "").strip(),
        prefix=(env.get("VEXA_WORKSPACE_S3_PREFIX") or "workspaces/").strip(),
    )
    missing = [k for k, v in (("VEXA_WORKSPACE_S3_BUCKET", s.bucket),
                              ("VEXA_WORKSPACE_S3_ACCESS_KEY", s.access_key),
                              ("VEXA_WORKSPACE_S3_SECRET_KEY", s.secret_key)) if not v]
    if missing:
        raise StoreMisconfigured("VEXA_WORKSPACE_BACKEND=s3 needs " + ", ".join(missing)
                                 + " (a static key scoped to the workspaces bucket)")
    if s.endpoint and not s.endpoint.lower().startswith(("http://", "https://")):
        raise StoreMisconfigured(f"VEXA_WORKSPACE_S3_ENDPOINT must be a full http(s) URL (got {s.endpoint!r})")
    if s.ca_bundle and not Path(s.ca_bundle).is_file():
        raise StoreMisconfigured(f"VEXA_WORKSPACE_S3_CA_BUNDLE names {s.ca_bundle}, which is not a file")
    return s


@dataclass
class Running:
    store: RepoStore
    observer: StoreObserver
    sync: CacheSync
    probe: SelfTest
    settings: Optional[S3Settings]
    cache: Optional[CacheManager] = None

    def stop(self) -> None:
        install_observer(None)
        install_path_observer(None)

    def health(self) -> dict:
        s = self.settings
        return {"backend": "s3", "bucket": s.bucket if s else "", "endpoint": s.endpoint if s else "",
                "conditional_put": self.probe.conditional_put, "versioning": self.probe.versioning,
                "guard": "redis-lock+fencing+generation-election",
                "cache": ({"bytes": self.cache.total(), "max_bytes": self.cache.max_bytes,
                           "hydrations": self.cache.hydrations, "evictions": self.cache.evictions}
                          if self.cache is not None else None),
                "last_error": self.store.last_error}


def start(cache_root: str, redis_client, *, env: Optional[Mapping[str, str]] = None,
          objects=None) -> Optional[Running]:
    """Start the s3 backend, or return None for ``volume``. Raises :class:`StoreMisconfigured`."""
    env = os.environ if env is None else env
    if backend(env) != "s3":
        return None
    settings = None
    if objects is None:
        settings = s3_settings(env)
        objects = S3Objects(settings)
    where = f"workspace store {settings.endpoint or 'AWS'}/{settings.bucket}" if settings else "workspace store"
    if redis_client is None:
        raise StoreMisconfigured("VEXA_WORKSPACE_BACKEND=s3 needs Redis (REDIS_URL) for the per-workspace lock")
    probe = self_test(objects, where=where)
    if not probe.ok:
        raise StoreMisconfigured(probe.error)
    logger.info("%s: self-test passed; conditional PUT %s, versioning %s", where,
                "supported" if probe.conditional_put else "not supported (lock + generation guard)",
                probe.versioning)
    try:
        ttl = int(env.get("VEXA_WORKSPACE_LOCK_TTL_MS") or 60000)
    except ValueError as exc:
        raise StoreMisconfigured("VEXA_WORKSPACE_LOCK_TTL_MS must be an integer") from exc
    try:
        max_bytes = int(env.get("VEXA_WORKSPACE_CACHE_MAX_BYTES") or 0)
        min_idle = float(env.get("VEXA_WORKSPACE_CACHE_MIN_IDLE_S") or 60)
    except ValueError as exc:
        raise StoreMisconfigured("VEXA_WORKSPACE_CACHE_MAX_BYTES / _MIN_IDLE_S must be numbers") from exc
    locks = WorkspaceLocks(redis_client, ttl_ms=ttl)
    root = Path(cache_root)
    store = RepoStore(objects=objects, locks=locks, cache_root=root, conditional=probe.conditional_put)
    # LAZY: platform files now, `_global` now (every request reads it), every other workspace as a
    # stub hydrated on first use — never the whole store at boot.
    counts = store.hydrate_all(lazy=True, eager=("_global",))
    logger.info("%s: %d repositories (%d left as stubs until first use), %d platform files, into %s",
                where, counts["repos"], counts["stubs"], counts["files"], root)
    sync = CacheSync(store)
    sync.baseline()
    cache = CacheManager(store, max_bytes=max_bytes, min_idle_s=min_idle)
    cache.refresh_sizes()
    observer = StoreObserver(store, cache=cache, sync=sync)
    install_observer(observer)
    install_path_observer(observer)
    return Running(store=store, observer=observer, sync=sync, probe=probe, settings=settings, cache=cache)
