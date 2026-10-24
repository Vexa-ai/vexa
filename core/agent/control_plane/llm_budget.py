"""llm_budget.py — what this deployment may spend on model calls, and the refusal when it has.

Before this the only cap on model spend was the credit limit of the provider key. Now:

  * SPEND IS COUNTED from what each model call reports. A worker puts one ``llm-usage`` event on its
    output stream per model call (``llm.usage``): the provider's own ``cost`` when it reports one
    (OpenRouter does, the Claude CLI reports ``total_cost_usd``), else the token counts, which are
    priced per model at the MARKET price (``model_prices.py``: OpenRouter's table, read daily, over
    a bundled snapshot; the ``llm_price_*`` policy rates only for a model no table names).
    :class:`UsageMeter` reads those events for every turn this agent-api dispatched — whether or
    not anybody is watching the turn — and adds them to the ledger (redis; one counter per scope
    and period, atomic, kept across restarts).
  * ONE LEDGER (R0, 2026-10-16). :meth:`Budget.record` is the one writer of model usage: the USD
    counters here are the record of truth, and the same call adds the call's tokens to the rolling
    token view (``shared/usage.py``, ``vexa:usage:<subject>:<hour>``) that the token budgets read.
    The worker no longer writes that view; agent-api's own model calls (home, titles, summaries)
    go through :func:`record_local`, so they are in the spend ledger too.
  * SPEND IS CAPPED per deployment and per person, per UTC day and per UTC month, by four
    ``_global/POLICIES.md`` keys (:data:`DEFAULTS`). :meth:`Budget.admit` is asked before every turn
    is dispatched — a chat message, a routine's run, a sensor's or an event agent's turn all reach
    the model only through ``Dispatcher.dispatch`` — and refuses with :class:`BudgetExhausted` when
    any of the four is spent. Nothing is spawned or delivered for a refused turn.
  * THE ADMIN IS TOLD at 80% and at 100% of each cap, once per period: a log event
    (``llm_budget_threshold``), a row in the ledger's notice list (``GET /api/admin/llm-budget``),
    and an in-app notification to each person ``llm_budget_notify`` names.
  * ``VEXA_LLM_KILL_SWITCH`` (agent-api env) refuses every turn, at once, whatever the ledger says.

  * SPEND NOT YET REPORTED counts too. A harness reports a turn's usage when the turn ends (the
    claude CLI's ``result``), so a long turn's calls reach the ledger only afterwards. The model
    proxy (``model_proxy.py``) sees every call it relays: it prices each one with :func:`cost_usd`
    and adds it to the turn's UNREPORTED figure (:func:`add_unreported`, one key per payer and
    unit). Before every model request the proxy checks ledger + unreported against the caps, so a
    turn is stopped mid-turn at the cap. When the worker's report arrives, :class:`UsageMeter`
    records it here and takes the same amount off the unreported figure, and the turn's
    ``turn-complete`` clears what is left: the report replaces the proxy's running figure, and the
    ledger stays the one record of spend. A turn that reported nothing at all (the Codex harness
    reports no usage) is recorded at the proxy's figure when it completes.

A model call the proxy does not relay (a deployment subscription: the vendor's CLI calls the vendor
directly) reaches the ledger only with the turn's report; such a turn already running when a cap
is reached finishes, and the next one is refused. The defaults leave headroom under the provider
key's own limit, the last line of defence.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger("agent_api.llm_budget")

try:
    from redis.exceptions import RedisError as _RedisError
except Exception:  # noqa: BLE001 — no redis library: nothing can raise it
    class _RedisError(Exception):
        pass

KILL_ENV = "VEXA_LLM_KILL_SWITCH"
_ON = frozenset({"1", "true", "yes", "on"})
_OFF_WORDS = frozenset({"off", "none", "no", "unlimited", "false"})

#: THE POLICY KEYS, WITH THEIR DEFAULTS. Dollars (USD). A cap of ``off`` removes that cap; a value
#: that cannot be read keeps the default (a cap is never lifted by a typo).
DEFAULTS: dict = {
    "llm_budget_org_daily_usd": 25.0,
    "llm_budget_org_monthly_usd": 250.0,
    "llm_budget_user_daily_usd": 5.0,
    "llm_budget_user_monthly_usd": 50.0,
    # what a million tokens of a model NO PRICE TABLE NAMES is counted at, when a call reports no
    # cost. Empty: the market table's base open-weight price (model_prices.py). A model the table
    # names is always counted at its market price, whatever these say.
    "llm_price_input_usd_per_mtok": "",
    "llm_price_output_usd_per_mtok": "",
    # who is notified in-app at 80% and 100%: user ids, comma-separated. Empty: the log event and
    # the notice list only.
    "llm_budget_notify": "",
}
CAP_KEYS = {
    ("org", "day"): "llm_budget_org_daily_usd",
    ("org", "month"): "llm_budget_org_monthly_usd",
    ("user", "day"): "llm_budget_user_daily_usd",
    ("user", "month"): "llm_budget_user_monthly_usd",
}
THRESHOLDS = (80, 100)
PREFIX = "vexa:llm"
NOTICES_KEY = f"{PREFIX}:notices"
NOTICES_KEEP = 100
_TTL = {"day": 3 * 86400, "month": 40 * 86400}


@dataclass(frozen=True)
class Caps:
    caps: Mapping[tuple, Optional[float]]
    price_in: Optional[float]
    price_out: Optional[float]
    notify: tuple = ()
    problems: tuple = ()

    @classmethod
    def from_policy(cls, front: Optional[Mapping[str, Any]]) -> "Caps":
        front = {str(k).lower(): v for k, v in (front or {}).items()}
        problems: list[str] = []

        def number(key: str, allow_off: bool) -> Optional[float]:
            raw = front.get(key)
            if raw is None or str(raw).strip() == "":
                return float(DEFAULTS[key]) if DEFAULTS[key] != "" else None
            text = str(raw).strip().lower().lstrip("$")
            if allow_off and text in _OFF_WORDS:
                return None
            try:
                value = float(text)
            except ValueError:
                problems.append(key)
                return float(DEFAULTS[key]) if DEFAULTS[key] != "" else None
            if value < 0 or value != value:
                problems.append(key)
                return float(DEFAULTS[key]) if DEFAULTS[key] != "" else None
            return value

        caps = {scope: number(key, True) for scope, key in CAP_KEYS.items()}
        notify = tuple(n.strip() for n in str(front.get("llm_budget_notify") or "").split(",") if n.strip())
        return cls(caps=caps, price_in=number("llm_price_input_usd_per_mtok", False),
                   price_out=number("llm_price_output_usd_per_mtok", False),
                   notify=notify, problems=tuple(problems))


def cost_usd(usage: Mapping[str, Any], caps: Caps, prices=None) -> float:
    """What one ``llm-usage`` event costs, never negative:

      1. the provider's own figure, when it gave one;
      2. else its tokens at the MARKET price of its ``model`` (``prices``, a ``model_prices.PriceTable``);
      3. else, for a model no table names, at the operator's ``llm_price_*`` rates when set;
      4. else at the table's base open-weight market price (never a flat top-tier rate)."""
    cost = usage.get("cost")
    try:
        if cost is not None and float(cost) >= 0:
            return float(cost)
    except (TypeError, ValueError):
        pass
    from control_plane import model_prices  # noqa: PLC0415
    model = str(usage.get("model") or "")
    table = prices if prices is not None else default_prices()
    price = table.price(model) if (table is not None and model) else None
    if price is None and caps.price_in is not None and caps.price_out is not None:
        price = model_prices.Price(caps.price_in, caps.price_out, None, "policy")
    if price is None and table is not None:
        price = table.unmatched_price(model)
    if price is None:  # no table and no snapshot at all: the snapshot's own base price, hardcoded
        price = model_prices.Price(0.24, 2.20, None, "builtin")
    return max(0.0, price.cost(usage))


_DEFAULT_PRICES = None


def default_prices():
    """The process's price table when none was wired (no shared redis cache)."""
    global _DEFAULT_PRICES
    if _DEFAULT_PRICES is None:
        from control_plane import model_prices  # noqa: PLC0415
        _DEFAULT_PRICES = model_prices.PriceTable(None)
    return _DEFAULT_PRICES


def _periods(now: float) -> dict:
    t = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc)
    day_end = (t.replace(hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(days=1))
    month_end = (t.replace(day=28) + _dt.timedelta(days=4)).replace(day=1, hour=0, minute=0, second=0,
                                                                   microsecond=0)
    return {"day": (t.strftime("%Y%m%d"), day_end), "month": (t.strftime("%Y%m"), month_end)}


def _key(scope: str, subject: str, period: str, pid: str) -> str:
    who = "org" if scope == "org" else f"user:{subject}"
    return f"{PREFIX}:spend:{who}:{period}:{pid}"


UNREPORTED_PREFIX = f"{PREFIX}:unreported"
#: An unreported figure outlives the longest turn that can add to it (a job's 3600 s plus its wait
#: in the queue) and then lapses: a worker that died without reporting stops counting by then.
UNREPORTED_TTL_S = 2 * 3600


def _unreported_key(payer: str, unit: str) -> str:
    return f"{UNREPORTED_PREFIX}:{payer}:{unit}"


def _unreported_index(scope: str, payer: str = "") -> str:
    return f"{UNREPORTED_PREFIX}-index:org" if scope == "org" else f"{UNREPORTED_PREFIX}-index:user:{payer}"


def add_unreported(r, payer: str, unit: str, usd: float, *, ttl_s: int = UNREPORTED_TTL_S) -> None:
    """The model proxy's half: one relayed call's cost, added to ``payer``'s running figure for
    ``unit``'s turn until the worker reports it."""
    if not (payer and unit) or usd <= 0:
        return
    key = _unreported_key(payer, unit)
    pipe = r.pipeline()
    pipe.incrbyfloat(key, float(usd))
    pipe.expire(key, int(ttl_s))
    for idx in (_unreported_index("user", payer), _unreported_index("org")):
        pipe.sadd(idx, key)
        pipe.expire(idx, int(ttl_s))
    pipe.execute()


def unreported(r, payer: str) -> dict:
    """``{"user": usd, "org": usd}``: what the proxy has relayed and no worker has reported yet, for
    ``payer`` and for the whole deployment. A lapsed figure is dropped from its index here."""
    out = {}
    for scope in ("user", "org"):
        idx = _unreported_index(scope, payer)
        keys = sorted(str(k.decode() if isinstance(k, bytes) else k) for k in (r.smembers(idx) or ()))
        total = 0.0
        if keys:
            gone = []
            for k, raw in zip(keys, r.mget(keys)):
                if raw is None:
                    gone.append(k)
                    continue
                try:
                    total += max(0.0, float(raw))
                except (TypeError, ValueError):
                    continue
            if gone:
                r.srem(idx, *gone)
        out[scope] = total
    return out


def settle_unreported(r, payer: str, unit: str, usd: Optional[float] = None) -> float:
    """The worker's report replaces the proxy's running figure. ``usd`` reported for ``unit``'s turn
    comes off it (never below zero when read). ``None`` — the turn is complete — clears it and
    returns what is left to record when the turn reported nothing at all, else ``0.0``. Taken
    atomically, so two meters watching one unit never record it twice."""
    key = _unreported_key(payer, unit)
    flag = f"{PREFIX}:reported:{payer}:{unit}"
    if usd is None:
        pipe = r.pipeline(transaction=True)
        pipe.get(key)
        pipe.get(flag)
        pipe.delete(key, flag)
        left, reported, _n = pipe.execute()
        if reported:
            return 0.0
        try:
            return max(0.0, float(left or 0))
        except (TypeError, ValueError):
            return 0.0
    r.set(flag, "1", ex=UNREPORTED_TTL_S)
    if usd > 0 and r.exists(key):
        r.incrbyfloat(key, -float(usd))      # keeps the key's TTL
    return 0.0


#: The refusal kind for a «Vexa for Claude» account (admin-api ``app/account_mode.py``).
ACCOUNT_AGENTS_OFF = "account_agents_off"


class BudgetExhausted(Exception):
    """A turn refused before it reached the model. ``kind`` is ``llm_budget_exhausted`` (a cap is
    spent), ``llm_disabled`` (the kill switch is on), or ``account_agents_off`` (a «Vexa for Claude»
    account: identity's ``account_mode`` is ``claude``, and Vexa runs no agent for it)."""

    def __init__(self, kind: str, *, scope: str = "", period: str = "", cap: float = 0.0,
                 spent: float = 0.0, resets_at: str = "") -> None:
        super().__init__(kind)
        self.kind, self.scope, self.period = kind, scope, period
        self.cap, self.spent, self.resets_at = cap, spent, resets_at

    def sentence(self) -> str:
        if self.kind == "llm_disabled":
            return "AI features are switched off on this deployment right now. Please try again later."
        if self.kind == ACCOUNT_AGENTS_OFF:
            return ("Vexa's agents are off for this account: it uses Vexa for Claude, so your own Claude "
                    "does this work. Calls and workspaces keep working.")
        if self.kind == "usage_window":
            which = "5-hour" if self.period == "5h" else "weekly"
            back = f" — back at {self.resets_at}" if self.resets_at else ""
            return (f"You've reached this {which} limit{back}. Your message is kept; turn on extra usage "
                    "to carry on now.")
        when = "today" if self.period == "day" else "this month"
        resets = f" It resets at {self.resets_at}." if self.resets_at else ""
        if self.scope == "user":
            return f"You have reached your AI usage limit for {when}.{resets}"
        return (f"This deployment has reached its AI usage limit for {when}.{resets} "
                "Your administrator has been notified.")

    @property
    def http_status(self) -> int:
        # Not retryable: a flow's step fails at once with the sentence (flows retries 409/429/5xx).
        return 403 if self.kind in ("llm_disabled", ACCOUNT_AGENTS_OFF) else 402

    def remedy(self) -> str:
        if self.kind == "llm_disabled":
            return "The operator has paused model calls on this deployment."
        if self.kind == ACCOUNT_AGENTS_OFF:
            return "Ask Claude, where Vexa is connected, to do it."
        if self.kind == "usage_window":
            return "Wait for the window to reset, or turn on extra usage."
        if self.scope == "user":
            return "Ask your administrator if you need more today."
        return "Your administrator can raise the limit in _global/POLICIES.md."

    def fault(self) -> dict:
        """The typed fault the chat renders (unit.v1 `Fault`): the model call was refused for want of
        budget — the same words a provider out of credit gets, with this deployment's sentence."""
        return {"source": "model-provider",
                "kind": "refused" if self.kind in ("llm_disabled", ACCOUNT_AGENTS_OFF) else "unpaid",
                "status": self.http_status, "detail": self.sentence(), "remedy": self.remedy()}

    def as_dict(self) -> dict:
        """The JSON a refused dispatch answers: the sentence for clients that read only `detail`,
        the typed fault, and which refusal it was."""
        out = {"detail": self.sentence(), "fault": self.fault(), "reason": self.kind}
        if self.kind not in ("llm_disabled", ACCOUNT_AGENTS_OFF):
            out.update(scope=self.scope, period=self.period, resets_at=self.resets_at)
        return out


def kill_switch_on(env: Optional[Mapping[str, str]] = None) -> bool:
    return str((env if env is not None else os.environ).get(KILL_ENV) or "").strip().lower() in _ON


class Budget:
    """The ledger (redis) plus the policy. ``policy()`` returns ``_global/POLICIES.md``'s front
    matter; ``notify(recipients, notice)`` delivers an in-app notice (optional)."""

    POLICY_TTL_S = 30.0

    def __init__(self, redis_client, policy: Callable[[], Mapping[str, Any]], *,
                 clock: Callable[[], float] = time.time,
                 env: Optional[Mapping[str, str]] = None,
                 notify: Optional[Callable[[tuple, dict], None]] = None,
                 prices=None, features: Optional[Callable[[], Mapping[str, bool]]] = None) -> None:
        self._r, self._policy, self._clock = redis_client, policy, clock
        self._env, self._notify = env, notify
        self._cached: Optional[tuple] = None
        if prices is None:
            from control_plane import model_prices  # noqa: PLC0415
            prices = model_prices.PriceTable(redis_client, env=env)
        self.prices = prices
        # the `usage` feature (credits and windows, ``credits.py``); read at most every POLICY_TTL_S
        self._features = features
        self._feat_cached: Optional[tuple] = None

    def usage_on(self) -> bool:
        if self._features is None:
            return False
        now = time.monotonic()
        if self._feat_cached and now - self._feat_cached[0] < self.POLICY_TTL_S:
            return self._feat_cached[1]
        try:
            on = bool((self._features() or {}).get("usage", False))
        except Exception:  # noqa: BLE001 — an unreadable switch is off
            on = False
        self._feat_cached = (now, on)
        return on

    def policy_front(self) -> Mapping[str, Any]:
        try:
            return self._policy() or {}
        except Exception:  # noqa: BLE001
            return {}

    def caps(self) -> Caps:
        now = time.monotonic()
        if self._cached and now - self._cached[0] < self.POLICY_TTL_S:
            return self._cached[1]
        try:
            front = self._policy() or {}
        except Exception:  # noqa: BLE001 — an unreadable policy is the defaults, never no cap
            logger.warning("llm budget: policy unreadable, the defaults apply")
            front = {}
        caps = Caps.from_policy(front)
        if caps.problems:
            logger.warning("llm budget: unreadable policy values kept at their defaults: %s",
                           ", ".join(caps.problems))
        self._cached = (now, caps)
        return caps

    def spent(self, subject: str) -> dict:
        """``{(scope, period): (spent, cap, resets_at_iso)}`` for this subject."""
        caps, periods = self.caps(), _periods(self._clock())
        scopes = list(CAP_KEYS)
        keys = [_key(s, subject, p, periods[p][0]) for s, p in scopes]
        values = self._r.mget(keys)
        out = {}
        for (scope, period), raw in zip(scopes, values):
            try:
                used = float(raw or 0)
            except (TypeError, ValueError):
                used = 0.0
            out[(scope, period)] = (used, caps.caps[(scope, period)],
                                    periods[period][1].strftime("%Y-%m-%d %H:%M UTC"))
        return out

    def admit(self, subject: str) -> None:
        """Raise :class:`BudgetExhausted` when this subject may not start a model turn now.

        A ledger that cannot be read refuses (fail closed): a cap that silently stops counting is
        no cap. Turns need the same redis to run at all, so this costs no availability."""
        if kill_switch_on(self._env):
            raise BudgetExhausted("llm_disabled")
        if self._r is None:
            return          # no ledger on this deployment (no redis): only the kill switch applies
        try:
            state = self.spent(str(subject))
        except (OSError, _RedisError) as exc:
            # The ledger is unreachable: refuse (fail closed) — the same redis carries the turn.
            logger.error("llm budget: ledger unreadable (%s) — refusing the turn", type(exc).__name__)
            raise BudgetExhausted("llm_budget_exhausted", scope="org", period="day") from exc
        except Exception as exc:  # noqa: BLE001 — a client that cannot keep a ledger keeps none
            logger.error("llm budget: no ledger on this redis client (%s) — not counted",
                         type(exc).__name__)
            return
        if self.usage_on():
            from control_plane import credits as _credits  # noqa: PLC0415
            _credits.admit(self._r, str(subject), self.policy_front(), now=self._clock())
        # The person's own caps first: the most specific answer is the one they can act on.
        for scope, period in (("user", "day"), ("user", "month"), ("org", "day"), ("org", "month")):
            used, cap, resets = state[(scope, period)]
            if cap is not None and used >= cap:
                raise BudgetExhausted("llm_budget_exhausted", scope=scope, period=period, cap=cap,
                                      spent=used, resets_at=resets)

    def record(self, subject: str, usage: Mapping[str, Any], *, kind: str = "chat") -> float:
        """Add one call's cost to every counter it belongs to, and its tokens to the rolling token
        view under ``kind`` (``shared/usage.py``); announce any threshold it crossed."""
        from shared import usage as usage_mod  # noqa: PLC0415
        now = self._clock()
        caps, periods = self.caps(), _periods(now)
        usage_mod.record(self._r, str(subject), kind, usage_mod.tokens_of(dict(usage)), now=now)
        usd = cost_usd(usage, caps, self.prices)
        if usd <= 0:
            return 0.0
        if self.usage_on():
            from control_plane import credits as _credits  # noqa: PLC0415
            try:
                _credits.debit(self._r, str(subject), cost_usd=usd, now=now, policy=self.policy_front(),
                               subject=str(subject), kind=kind, model=str(usage.get("model") or ""))
            except Exception as exc:  # noqa: BLE001 — accounting never fails a call
                logger.warning("credits: not debited (%s)", type(exc).__name__)
        pipe = self._r.pipeline()
        scopes = list(CAP_KEYS)
        for scope, period in scopes:
            key = _key(scope, str(subject), period, periods[period][0])
            pipe.incrbyfloat(key, usd)
            pipe.expire(key, _TTL[period])
        results = pipe.execute()
        totals = {scopes[i]: float(results[2 * i]) for i in range(len(scopes))}
        for (scope, period), total in totals.items():
            cap = caps.caps[(scope, period)]
            if not cap:
                continue
            for pct in THRESHOLDS:
                if total >= cap * pct / 100.0:
                    # once per scope, period and threshold: `_announce` is idempotent (SET NX)
                    self._announce(scope, period, pct, str(subject), total, cap, periods[period][0], caps)
        return usd

    def _announce(self, scope, period, pct, subject, total, cap, pid, caps: Caps) -> None:
        who = "org" if scope == "org" else f"user:{subject}"
        marker = f"{PREFIX}:notice:{who}:{period}:{pid}:{pct}"
        if not self._r.set(marker, "1", nx=True, ex=_TTL[period]):
            return
        notice = {"scope": scope, "period": period, "percent": pct, "spent_usd": round(total, 4),
                  "cap_usd": cap, "at": round(self._clock(), 3)}
        if scope == "user":
            notice["subject"] = subject
        logger.log(logging.ERROR if pct >= 100 else logging.WARNING,
                   "llm_budget_threshold %s", json.dumps(notice, sort_keys=True))
        try:
            self._r.lpush(NOTICES_KEY, json.dumps(notice, sort_keys=True))
            self._r.ltrim(NOTICES_KEY, 0, NOTICES_KEEP - 1)
        except Exception:  # noqa: BLE001 — the log line above is the notice of record
            logger.warning("llm budget: notice not stored")
        if self._notify is not None and caps.notify:
            try:
                self._notify(caps.notify, notice)
            except Exception:  # noqa: BLE001 — a notice never fails the accounting
                logger.warning("llm budget: in-app notice not delivered")

    def notices(self, limit: int = 50) -> list:
        try:
            return [json.loads(x) for x in (self._r.lrange(NOTICES_KEY, 0, max(0, limit - 1)) or [])]
        except Exception:  # noqa: BLE001
            return []


USAGE_EVENT = "llm-usage"
WATCH_TTL_S = 3600.0
POLL_S = 0.5
SEEN_TTL_S = 2 * 86400


class UsageMeter:
    """Reads the ``llm-usage`` events of the units this agent-api dispatched, until each turn it
    dispatched has completed (or ``WATCH_TTL_S``), and records them. An event is counted once even
    when two replicas watch the same unit (a ``SET NX`` per stream entry)."""

    def __init__(self, budget: Budget, redis_client, *, output_topic: Callable[[str], str]) -> None:
        self._budget, self._r, self._topic = budget, redis_client, output_topic
        self._lock = threading.Lock()
        self._watch: dict = {}
        self._settle: list = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def watch(self, unit_id: str, subject: str, from_id: str = "0-0") -> None:
        """Count this unit's next turn to ``subject``, from ``from_id`` (the stream's tail before the
        turn was delivered). A unit whose turns bill different people (a room of two) is handed to
        the new payer here; what the stream holds up to ``from_id`` belongs to the earlier turns and
        is still counted to the person who was paying for them."""
        with self._lock:
            w = self._watch.get(unit_id)
            if w is not None and w["subject"] != subject and from_id and from_id != "0-0":
                self._settle.append({"uid": unit_id, "cursor": w["cursor"], "until": from_id,
                                     "subject": w["subject"]})
            if w is None or w["subject"] != subject:
                self._watch[unit_id] = {"cursor": from_id or "0-0", "expect": 1, "since": time.time(),
                                        "subject": subject}
            else:
                w["expect"] += 1
                w["since"] = time.time()

    def watching(self) -> list:
        with self._lock:
            return sorted(self._watch)

    def _count(self, uid: str, subject: str, entries) -> tuple[float, str, int]:
        """Record every usage the entries carry to ``subject``: ``(spent, last id, turns completed)``."""
        total, cursor, done = 0.0, "", 0
        for eid, fields in entries:
            cursor = eid
            try:
                ev = json.loads((fields or {}).get("event") or "{}")
            except ValueError:
                continue
            t = ev.get("type")
            # A turn's usage rides its `done` (`usage`); the write-back phase, whose `done` is
            # dropped, hands it on as an `llm-usage` event (`worker.engine.writeback_events`).
            usage = ev if t == USAGE_EVENT else (
                ev.get("usage") if t == "done" and isinstance(ev.get("usage"), dict) else None)
            if usage is not None:
                try:
                    first = self._r.set(f"{PREFIX}:seen:{uid}:{eid}", "1", nx=True, ex=SEEN_TTL_S)
                except Exception:  # noqa: BLE001
                    first = True
                if first:
                    try:
                        from shared import usage as usage_mod  # noqa: PLC0415
                        usd = self._budget.record(subject, usage, kind=usage_mod.kind_of_unit(uid))
                        total += usd
                        settle_unreported(self._r, subject, uid, usd)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("usage meter: spend not recorded (%s)", type(exc).__name__)
            elif t == "turn-complete":
                done += 1
                try:
                    left = settle_unreported(self._r, subject, uid)
                    if left > 0:
                        # the turn reported nothing: what the model proxy priced is its record
                        from shared import usage as usage_mod  # noqa: PLC0415
                        total += self._budget.record(subject, {"cost": left, "source": "model-proxy"},
                                                     kind=usage_mod.kind_of_unit(uid))
                except Exception as exc:  # noqa: BLE001 — the figure lapses on its own
                    logger.warning("usage meter: unreported spend not settled (%s)", type(exc).__name__)
        return total, cursor, done

    def poll_once(self) -> float:
        total = 0.0
        with self._lock:
            settle, self._settle = self._settle, []
            units = {u: dict(w) for u, w in self._watch.items()}
        # the earlier payer's turns on a unit that changed hands, bounded by where the new one began
        for s in settle:
            try:
                entries = self._r.xrange(self._topic(s["uid"]), min=f"({s['cursor']}",
                                         max=s["until"]) or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage meter: unit stream unreadable (%s)", type(exc).__name__)
                with self._lock:
                    self._settle.append(s)      # read again on the next pass
                continue
            total += self._count(s["uid"], s["subject"], entries)[0]
        for uid, w in units.items():
            try:
                entries = self._r.xrange(self._topic(uid), min=f"({w['cursor']}", count=500) or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage meter: unit stream unreadable (%s)", type(exc).__name__)
                continue
            spent, last, done = self._count(uid, w["subject"], entries)
            total += spent
            cursor = last or w["cursor"]
            with self._lock:
                cur = self._watch.get(uid)
                if cur is None or cur["subject"] != w["subject"]:
                    continue        # handed to another payer meanwhile: its own cursor stands
                cur["cursor"] = cursor
                cur["expect"] -= done
                if cur["expect"] <= 0 or time.time() - cur["since"] > WATCH_TTL_S:
                    self._watch.pop(uid, None)
        return total

    def start(self) -> None:
        if self._thread is not None:
            return

        def _loop() -> None:
            while not self._stop.wait(POLL_S):
                try:
                    if self._watch:
                        self.poll_once()
                except Exception:  # noqa: BLE001 — the meter outlives any one bad pass
                    logger.exception("usage meter pass failed")

        self._thread = threading.Thread(target=_loop, daemon=True, name="llm-usage-meter")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


_LEDGER: Optional[Budget] = None


def set_ledger(budget: Optional[Budget]) -> None:
    """agent-api's one ledger, for the model calls it makes itself (:func:`record_local`)."""
    global _LEDGER
    _LEDGER = budget


def record_local(subject: str, kind: str, usage: Optional[Mapping[str, Any]], *, fallback_client) -> float:
    """One model call agent-api made itself (the home, a title, a summary): into the one ledger.
    With no ledger wired (no redis for it), only the token view is counted, on ``fallback_client``."""
    if not subject or not isinstance(usage, Mapping):
        return 0.0
    if _LEDGER is not None and getattr(_LEDGER, "_r", None) is not None:
        try:
            return _LEDGER.record(subject, usage, kind=kind)
        except Exception as exc:  # noqa: BLE001 — accounting never fails the call
            logger.warning("llm budget: local call not recorded (%s)", type(exc).__name__)
            return 0.0
    from shared import usage as usage_mod  # noqa: PLC0415
    usage_mod.record(fallback_client, subject, kind, usage_mod.tokens_of(dict(usage)))
    return 0.0


def read_global_policy(settings) -> dict:
    """``_global/POLICIES.md``'s front matter, or ``{}``. Unreadable is the defaults."""
    from control_plane import front_page  # noqa: PLC0415
    from control_plane import system_mounts  # noqa: PLC0415
    from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415

    root = system_mounts.global_root(settings)
    raw = _wpaths.read_text_inside(root, "POLICIES.md", max_bytes=1_000_000) if root.is_dir() else None
    return front_page.front_matter(raw) if raw else {}
