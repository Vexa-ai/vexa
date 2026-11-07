"""Handing a wake to the company's flow: one event.v1 ``meeting.signal`` to flows-api.

signals never wakes the agent itself. The event goes to flows-api ``POST /events/signal`` — a door
only the signals service may use (``X-Internal-Secret`` + ``X-Vexa-Service: signals``) — where the
company-owned ``_global`` flow decides what a wake does (by default: a turn in the meeting's chat).
The body is event.v1: ``name``, ``subject`` (whom it concerns: the meeting's owner) and ``data`` =
signals.v1 ``WakeData``. flows keys the fact on the buffer's segment ids and reason, so the same wake
sent twice (a retry, or an instance that took over a wake its predecessor had not confirmed) is ONE
fact.

Answers: 2xx is delivered (also when the company retired its flow — ``refused: no_flow_reacts``,
logged once). A 400 is a wake flows will never take: logged as an error and dropped, never retried.
Unset ``VEXA_FLOWS_API_URL`` is the no-flows profile: the wake is dropped (a publish edge, not a
dependency). 401/403, 5xx or no answer: not delivered, the runner retries it.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("signals.wake")

DELIVERED, RETRY = "delivered", "retry"
SERVICE = "signals"


class FlowsEmitter:
    def __init__(self, base_url: str, secret: str, *, transport=None):
        self.base = str(base_url or "").rstrip("/")
        self.secret, self.transport = secret, transport
        self._said: set = set()

    def _once(self, line: str) -> None:
        if line not in self._said:
            self._said.add(line)
            log.warning("wake: %s", line)

    async def emit(self, event_type: str, subject: str, data: dict) -> str:
        if not self.base:
            self._once("VEXA_FLOWS_API_URL is unset (no-flows profile) — wakes are dropped")
            return DELIVERED
        body = {"name": event_type, "subject": subject, "data": data}
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=5.0) as client:
                r = await client.post(self.base + "/events/signal", json=body,
                                      headers={"X-Internal-Secret": self.secret, "X-Vexa-Service": SERVICE})
        except httpx.HTTPError as exc:
            log.warning("wake for %s: flows-api did not answer (%s)", event_type, type(exc).__name__)
            return RETRY
        if 200 <= r.status_code < 300:
            try:
                refused = r.json().get("refused") or []
            except (ValueError, AttributeError):
                refused = []
            if "no_flow_reacts" in refused:
                self._once(f"no flow reacts to {event_type} — the company retired its flow; wakes do nothing")
            return DELIVERED
        if r.status_code == 400:
            log.error("wake for %s refused by flows-api (HTTP 400: %s) — dropped", event_type, r.text[:300])
            return DELIVERED
        log.warning("wake for %s: flows-api answered HTTP %s", event_type, r.status_code)
        return RETRY
