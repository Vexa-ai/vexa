"""Bot limits, the shadow: what the limits WOULD say about a spawn, logged and counted.

Nothing here can change a spawn. ``observe`` never raises and gives up after a short timeout. With
``BOT_ADMISSION_MODE`` off (the default, and any value other than ``shadow``) there is no observer at
all, so nothing is queried, written or logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Mapping, Optional

from ..obs import log_event
from .predict import Prediction, decide

log = logging.getLogger(__name__)

MODE_ENV = "BOT_ADMISSION_MODE"
OBSERVE_TIMEOUT_S = 0.25

PREDICTION_KEY = "bot_admission:prediction:{}"
PREDICTION_TTL_S = 3 * 24 * 3600
OUTSIDE_KEY = "bot_admission:outside:{}"
OUTSIDE_EVERY_S = 7 * 24 * 3600
COUNTS_KEY = "bot_admission:counts"
COUNTS_TTL_S = 30 * 24 * 3600
COMPUTE_KEY = "bot_admission:last_compute"


def admission_mode(env: Optional[Mapping[str, str]] = None) -> str:
    return "shadow" if (os.environ if env is None else env).get(MODE_ENV) == "shadow" else "off"


class FailOnce:
    """Logs a failing dependency once, then again only after it has worked in between."""

    def __init__(self) -> None:
        self._down = False

    def failed(self, what: str, exc: BaseException) -> None:
        if not self._down:
            self._down = True
            log.warning("bot admission: %s failed: %s", what, type(exc).__name__)

    def worked(self) -> None:
        self._down = False


class ShadowObserver:
    def __init__(self, repo, redis, *, timeout_s: float = OBSERVE_TIMEOUT_S) -> None:
        self._repo = repo
        self._redis = redis
        self._timeout_s = timeout_s
        self._fail = FailOnce()

    async def observe(
        self, *, user_id: int, kind: str, cap: Optional[int],
        exclude_meeting_id: Optional[int] = None, now: Optional[datetime] = None,
    ) -> None:
        """Log what the limits would say. Never raises, never takes longer than the timeout."""
        try:
            await asyncio.wait_for(
                self._observe(user_id, kind, cap, exclude_meeting_id, now), self._timeout_s,
            )
            self._fail.worked()
        except Exception as exc:  # noqa: BLE001 — a spawn never pays for the shadow
            self._fail.failed("observe", exc)

    async def _observe(self, user_id, kind, cap, exclude_meeting_id, now) -> None:
        at = now or datetime.now(timezone.utc).replace(tzinfo=None)
        stored = await self._redis.get(PREDICTION_KEY.format(user_id))
        prediction = Prediction.from_json(json.loads(stored)) if stored else Prediction()
        counts = await self._repo.count_active_bots_by_kind(
            user_id=user_id, exclude_meeting_id=exclude_meeting_id,
        )
        active = counts[kind]
        decision = decide(prediction, kind=kind, active=active, cap=cap, at=at)
        log_event(
            "bot_admission_shadow", audience="operator", span="bots.create", user_id=user_id,
            fields={
                "user_id": user_id, "kind": kind, "active": active, "limit": decision.limit,
                "would": decision.would, "within_prediction": decision.within_prediction,
            },
        )
        await self._redis.hincrby(COUNTS_KEY, "would_allow" if decision.would == "allow" else "would_queue", 1)
        await self._redis.expire(COUNTS_KEY, COUNTS_TTL_S)
        if not decision.within_prediction:
            first = await self._redis.set(OUTSIDE_KEY.format(user_id), "1", nx=True, ex=OUTSIDE_EVERY_S)
            if first:
                log_event(
                    "bot_admission_outside_prediction", audience="operator", span="bots.create",
                    user_id=user_id,
                    fields={"user_id": user_id, "kind": kind, "active": active, "limit": decision.limit},
                )

    async def reading(self) -> dict:
        counts = await self._redis.hgetall(COUNTS_KEY) or {}
        stored = await self._redis.get(COMPUTE_KEY)
        compute = json.loads(stored) if stored else {}
        return {
            "mode": "shadow",
            "would_allow": int(counts.get("would_allow", 0)),
            "would_queue": int(counts.get("would_queue", 0)),
            "accounts_predicted": compute.get("accounts"),
            "last_compute_at": compute.get("at"),
            "last_compute_duration_s": compute.get("duration_s"),
        }


def build_observer(repo, redis, *, env: Optional[Mapping[str, str]] = None) -> Optional[ShadowObserver]:
    """The observer in shadow; None otherwise, and None means nothing runs."""
    return ShadowObserver(repo, redis) if admission_mode(env) == "shadow" else None
