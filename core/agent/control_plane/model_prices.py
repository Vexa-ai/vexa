"""model_prices.py — what a model's tokens cost, from a maintained market price table.

The spend ledger (``llm_budget.py``) counts a call at the provider's own ``cost`` when the call
reports one (OpenRouter and the Claude CLI do). A call that reports only tokens — a self-hosted
OpenAI-compatible server, for one — is priced HERE, per model, at the market price:

    1. THE LIVE TABLE: OpenRouter's public ``GET /api/v1/models`` (no key), read at most once a day
       and kept in redis (``vexa:llm:prices``) so every replica shares one read. A read that fails
       keeps the last table. ``VEXA_MODEL_PRICES_URL`` points it elsewhere; ``off`` never fetches
       (an air-gapped install), and then:
    2. THE SNAPSHOT bundled with this release (``model_prices.snapshot.json``): the same prices,
       read on a known date. It is the fallback and is never above the market's price.
    3. A MODEL NEITHER TABLE NAMES is counted at the operator's ``llm_price_*`` policy rates when
       ``_global/POLICIES.md`` sets them, else at the snapshot's ``unmatched`` model — the base
       open-weight model's market price, since a model no market lists is in practice a self-hosted
       open-weight one. It is logged once per model (``llm_price_unmatched``).

Model ids are matched by their last path segment, lowercased, with ``.`` and ``_`` read as ``-``:
the catalog's ``claude-haiku-5-5`` and a server's ``Qwen/Qwen3.8-27B`` find OpenRouter's
``anthropic/claude-haiku-5.5`` and ``qwen/qwen3.8-27b``. A ``:variant`` (``:free``, ``:batch``) is
never a match for the base model.

Pricing never blocks a turn and never raises: a stale table is used while a fresh one is read in
the background.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger("agent_api.model_prices")

URL_ENV = "VEXA_MODEL_PRICES_URL"
DEFAULT_URL = "https://openrouter.ai/api/v1/models"
REDIS_KEY = "vexa:llm:prices"
REFRESH_S = 24 * 3600.0
RETRY_S = 3600.0               # after a failed read, try again no sooner than this
REDIS_TTL_S = 14 * 86400
FETCH_TIMEOUT_S = 10.0
SNAPSHOT = Path(__file__).with_name("model_prices.snapshot.json")


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""
    prompt: float
    completion: float
    cache_read: Optional[float] = None
    source: str = ""

    def cost(self, usage: Mapping[str, Any]) -> float:
        def n(key: str) -> float:
            try:
                return max(0.0, float(usage.get(key) or 0))
            except (TypeError, ValueError):
                return 0.0
        prompt, cached = n("prompt_tokens"), min(n("cache_read_tokens"), n("prompt_tokens"))
        rate_cached = self.cache_read if self.cache_read is not None else self.prompt
        return ((prompt - cached) * self.prompt + cached * rate_cached
                + n("completion_tokens") * self.completion) / 1e6


def norm(model: str) -> str:
    """The match key of a model id (see the module doc)."""
    m = str(model or "").strip().lower()
    if ":" in m:
        return ""
    m = m.rsplit("/", 1)[-1]
    return re.sub(r"[._]", "-", m)


def parse_openrouter(body: Any) -> dict[str, dict]:
    """``GET /api/v1/models`` → ``{id: {prompt, completion, cache_read}}`` in USD per 1M tokens.
    OpenRouter prices per token, as strings. A model without both prices, or with a negative one
    (OpenRouter's ``-1`` for a router whose price varies), is left out."""
    out: dict[str, dict] = {}
    for m in (body or {}).get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        p = m.get("pricing") or {}
        try:
            prompt, completion = float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if prompt < 0 or completion < 0:
            continue
        row = {"prompt": round(prompt, 6), "completion": round(completion, 6)}
        try:
            if p.get("input_cache_read") is not None and float(p["input_cache_read"]) >= 0:
                row["cache_read"] = round(float(p["input_cache_read"]) * 1e6, 6)
        except (TypeError, ValueError):
            pass
        out[str(m["id"])] = row
    return out


def _index(models: Mapping[str, Mapping[str, Any]], source: str) -> dict[str, Price]:
    idx: dict[str, Price] = {}
    for mid, row in (models or {}).items():
        key = norm(mid)
        if not key:
            continue
        try:
            price = Price(float(row["prompt"]), float(row["completion"]),
                          float(row["cache_read"]) if row.get("cache_read") is not None else None, source)
        except (KeyError, TypeError, ValueError):
            continue
        # two ids with one key (``openai/x`` and ``other/x``): the cheaper one, never the dearer
        if key not in idx or price.prompt + price.completion < idx[key].prompt + idx[key].completion:
            idx[key] = price
    return idx


def load_snapshot(path: Path = SNAPSHOT) -> tuple[dict[str, Price], Optional[Price]]:
    try:
        doc = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a missing snapshot leaves the live table and the policy
        logger.error("model prices: snapshot unreadable (%s)", path)
        return {}, None
    idx = _index(doc.get("models") or {}, "snapshot")
    return idx, idx.get(norm(doc.get("unmatched") or ""))


def _http_get(url: str) -> Any:
    import urllib.request  # noqa: PLC0415
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "vexa-agent-api"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:  # noqa: S310 — a fixed https URL
        return json.loads(r.read(20_000_000))


class PriceTable:
    """The live table over the snapshot. ``redis_client`` may be None (no shared cache: this process
    reads for itself). ``fetch(url)`` returns the parsed JSON body; ``background=False`` refreshes in
    the caller's thread (tests)."""

    def __init__(self, redis_client=None, *, env: Optional[Mapping[str, str]] = None,
                 fetch: Callable[[str], Any] = _http_get, clock: Callable[[], float] = time.time,
                 background: bool = True, snapshot: Path = SNAPSHOT) -> None:
        self._r, self._fetch, self._clock, self._background = redis_client, fetch, clock, background
        e = env if env is not None else os.environ
        raw = str(e.get(URL_ENV) or "").strip()
        self.url = "" if raw.lower() in {"off", "none", "0", "false"} else (raw or DEFAULT_URL)
        self._snapshot, self.unmatched = load_snapshot(snapshot)
        self._live: dict[str, Price] = {}
        self.fetched_at = 0.0
        self._tried_at = 0.0
        self._lock = threading.Lock()
        self._busy = False
        self._warned: set[str] = set()

    # ── reading ────────────────────────────────────────────────────────────────────────────────
    def price(self, model: str) -> Optional[Price]:
        """The market price of ``model``, or None when no table names it."""
        self._maybe_refresh()
        key = norm(model)
        if not key:
            return None
        return self._live.get(key) or self._snapshot.get(key)

    def unmatched_price(self, model: str) -> Optional[Price]:
        if model and model not in self._warned:
            self._warned.add(model)
            logger.warning("llm_price_unmatched %s", json.dumps({"model": model}))
        return self.unmatched

    def status(self) -> dict:
        return {"source": self.url or "snapshot only", "models": len(self._live) or len(self._snapshot),
                "fetched_at": self.fetched_at or None, "unmatched_seen": sorted(self._warned)}

    # ── refreshing ─────────────────────────────────────────────────────────────────────────────
    def _maybe_refresh(self) -> None:
        now = self._clock()
        if self.fetched_at and now - self.fetched_at < REFRESH_S:
            return
        if not self._live:
            self._from_redis()
            if self.fetched_at and now - self.fetched_at < REFRESH_S:
                return
        if not self.url or now - self._tried_at < RETRY_S:
            return
        with self._lock:
            if self._busy:
                return
            self._busy, self._tried_at = True, now
        if self._background:
            threading.Thread(target=self.refresh, daemon=True, name="model-prices").start()
        else:
            self.refresh()

    def _from_redis(self) -> None:
        if self._r is None:
            return
        try:
            raw = self._r.get(REDIS_KEY)
            doc = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return
        if isinstance(doc, dict) and doc.get("models"):
            self._live = _index(doc["models"], "live")
            self.fetched_at = float(doc.get("fetched_at") or 0)

    def refresh(self) -> bool:
        """Read the live table now. True when a new table was taken; a failure keeps the last one."""
        try:
            models = parse_openrouter(self._fetch(self.url))
            if not models:
                raise ValueError("no priced model in the answer")
            now = self._clock()
            self._live, self.fetched_at = _index(models, "live"), now
            if self._r is not None:
                try:
                    self._r.set(REDIS_KEY, json.dumps({"fetched_at": now, "url": self.url, "models": models}),
                                ex=REDIS_TTL_S)
                except Exception:  # noqa: BLE001 — this process keeps it anyway
                    logger.warning("model prices: not cached in redis")
            logger.info("model prices: %d models read from %s", len(models), self.url)
            return True
        except Exception as exc:  # noqa: BLE001 — the last table (or the snapshot) stays
            logger.warning("model prices: read failed (%s); keeping the %s table", type(exc).__name__,
                           "last live" if self._live else "snapshot")
            return False
        finally:
            with self._lock:
                self._busy = False
