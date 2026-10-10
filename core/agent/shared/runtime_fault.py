"""runtime_fault.py — the runtime edge's failure vocabulary (P18, ADR-0010).

agent-api reaches the runtime kernel over runtime.v1's HTTP surface (`adapters.RuntimeHttpClient`).
Before this module that edge translated only the happy path: an `HTTPError 502` from `urllib`
climbed out of the dispatcher unhandled, FastAPI answered a bare 500, and the chat showed the person
"Internal Server Error" for what was, on the 0.13.2 demo stack, the runtime refusing to start their
agent because a previous one was still registered (kubectl `AlreadyExists`). "The agent is broken",
"the runtime is down" and "the runtime said no" read identically.

So every runtime call is translated HERE into one typed fault:

    {"source": "runtime", "kind": <KINDS>, "op": "spawn" | "status" | "list",
     "status": <the runtime's HTTP status, or None>, "detail": <one safe sentence>,
     "remedy": <one line, or "">}

`detail` is OURS, never the runtime's text: the runtime's 502 body carries the backend's own words
(a kubectl line naming the namespace and pod), which belong in the operator's log, not on the
person's screen. The raw text rides on the exception as `upstream` for that log and nowhere else.

The boundary owns its vocabulary (ADR-0010 — no shared error base): the model provider's fault is
`llm.faults.ProviderFault`, a sibling shape with the same `source` + `kind` convention.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
from typing import Optional

SOURCE = "runtime"

#: The runtime answered that it could not start the workload (runtime.v1 502).
SPAWN_REFUSED = "spawn_refused"
#: The person's workload quota is full (runtime.v1 429).
QUOTA_EXCEEDED = "quota_exceeded"
#: The runtime refused agent-api's caller credential (401/403).
UNAUTHORIZED = "unauthorized"
#: The runtime refused the request itself (400/422, or another 4xx).
REFUSED = "refused"
#: The runtime does not know the workload asked about (404).
NOT_FOUND = "not_found"
#: The runtime could not be reached at all: connection refused, DNS, a timeout.
UNREACHABLE = "unreachable"
#: The runtime answered with a server error other than a refused start.
UNAVAILABLE = "unavailable"
#: The runtime answered 2xx with a body that is not runtime.v1.
BAD_RESPONSE = "bad_response"

KINDS = (SPAWN_REFUSED, QUOTA_EXCEEDED, UNAUTHORIZED, REFUSED, NOT_FOUND, UNREACHABLE, UNAVAILABLE,
         BAD_RESPONSE)

#: What agent-api answers its own caller for each kind. A runtime that is down or full is a
#: "try again later" (503); every other kind is an upstream that answered wrongly (502). Never 500:
#: a 500 says agent-api itself broke, and it did not.
_HTTP_STATUS = {UNREACHABLE: 503, UNAVAILABLE: 503, QUOTA_EXCEEDED: 503}

_ALREADY_EXISTS = re.compile(r"already\s*exists|alreadyexists|\bconflict\b|\b409\b", re.IGNORECASE)
_IMAGE_MISSING = re.compile(r"no such image|image .*not found|errimagepull|imagepullbackoff|"
                            r"pull access denied|manifest unknown", re.IGNORECASE)
_QUOTA = re.compile(r"exceeded quota|resourcequota|insufficient (?:cpu|memory)|quota", re.IGNORECASE)


class RuntimeFault(RuntimeError):
    """A runtime call failed, translated. ``kind`` is one of :data:`KINDS`; ``detail`` and
    ``remedy`` are safe to show a person; ``upstream`` is the runtime's own text, for the log."""

    source = SOURCE

    def __init__(self, kind: str, *, op: str, status: Optional[int] = None, detail: str,
                 remedy: str = "", upstream: str = "") -> None:
        self.kind = kind
        self.op = op
        self.status = status
        self.detail = detail
        self.remedy = remedy
        self.upstream = upstream
        super().__init__(f"runtime {op} failed: {kind} — {detail}")

    @property
    def http_status(self) -> int:
        """The status agent-api answers with — 503 for down/full, 502 otherwise, never 500."""
        return _HTTP_STATUS.get(self.kind, 502)

    def as_dict(self) -> dict:
        """The fault as it travels: a JSON error body, an SSE `error` event, a pending row."""
        return {"source": SOURCE, "kind": self.kind, "op": self.op, "status": self.status,
                "detail": self.detail, "remedy": self.remedy}

    def sentence(self) -> str:
        """One sentence a person can read, for clients that render only `detail` text."""
        return sentence(self.as_dict())


_VERB = {"spawn": "start your agent", "status": "report on your agent",
         "list": "list running agents"}


def sentence(fault: dict) -> str:
    """A recorded fault (``as_dict()``) as the one sentence an older client shows on its own."""
    verb = _VERB.get(str(fault.get("op") or ""), "answer")
    remedy = str(fault.get("remedy") or "")
    return f"The agent runtime could not {verb}: {fault.get('detail') or 'it failed'}." + (
        f" {remedy}" if remedy else "")


def _body_text(body: str) -> str:
    """The runtime's `{detail}` (runtime.v1 `$defs/Error`), else the raw body. For the log."""
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return body
    if isinstance(parsed, dict) and parsed.get("detail") is not None:
        d = parsed["detail"]
        return d if isinstance(d, str) else json.dumps(d)
    return body


def _spawn_refused(upstream: str) -> tuple[str, str]:
    """A 502 from POST /workloads, read for a cause we can name WITHOUT repeating it."""
    if _ALREADY_EXISTS.search(upstream):
        return ("a previous agent for this chat is still registered",
                "Retry in a few seconds; if it keeps happening, an operator must remove the stale "
                "agent workload.")
    if _IMAGE_MISSING.search(upstream):
        return ("the agent image is not available to the runtime",
                "An operator must make the agent image available to the runtime.")
    if _QUOTA.search(upstream):
        return ("the cluster has no room for another agent right now",
                "Retry when a running agent has finished, or ask an operator to raise the quota.")
    return ("the runtime could not start the agent",
            "Retry in a few seconds; if it keeps happening, an operator should check the runtime's log.")


def from_http_error(op: str, err: urllib.error.HTTPError) -> RuntimeFault:
    """Translate the runtime's non-2xx answer (runtime.v1 § Errors) into a typed fault."""
    status = int(getattr(err, "code", 0) or 0)
    try:
        raw = err.read().decode("utf-8", "replace") if hasattr(err, "read") else ""
    except Exception:  # noqa: BLE001 — a body we cannot read is a body we do not need
        raw = ""
    upstream = _body_text(raw)[:500]
    if status in (401, 403):
        return RuntimeFault(UNAUTHORIZED, op=op, status=status, upstream=upstream,
                            detail="the runtime refused agent-api's credential",
                            remedy="An operator must give agent-api and the runtime the same "
                                   "RUNTIME_API_TOKEN.")
    if status == 429:
        return RuntimeFault(QUOTA_EXCEEDED, op=op, status=status, upstream=upstream,
                            detail="you already have as many agents running as you are allowed",
                            remedy="Retry when one of your running agents has finished.")
    if status == 404:
        return RuntimeFault(NOT_FOUND, op=op, status=status, upstream=upstream,
                            detail="the runtime does not know this agent")
    if status in (409, 502) and op == "spawn":
        # 409 is what a runtime that reports the collision AS a collision answers; 502 is what
        # runtime.v1 answers today with the backend's own words (kubectl `AlreadyExists`) inside.
        detail, remedy = _spawn_refused("already exists" if status == 409 else upstream)
        return RuntimeFault(SPAWN_REFUSED, op=op, status=status, upstream=upstream,
                            detail=detail, remedy=remedy)
    if 400 <= status < 500:
        return RuntimeFault(REFUSED, op=op, status=status, upstream=upstream,
                            detail=f"the runtime refused the request ({status})",
                            remedy="An operator should check the runtime's log for the refusal.")
    return RuntimeFault(UNAVAILABLE, op=op, status=status or None, upstream=upstream,
                        detail=f"the runtime answered with an error ({status})" if status
                        else "the runtime answered with an error",
                        remedy="Retry in a few seconds.")


def from_transport_error(op: str, err: BaseException) -> RuntimeFault:
    """Translate a call that never got an HTTP answer — refused, unresolvable, timed out."""
    reason = getattr(err, "reason", err)
    timed_out = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    detail = ("the runtime did not answer in time" if timed_out
              else "the agent runtime could not be reached")
    return RuntimeFault(UNREACHABLE, op=op, status=None, upstream=str(reason)[:300], detail=detail,
                        remedy="Retry in a moment; if it persists, an operator should check that "
                               "the runtime service is running.")


def from_bad_body(op: str, err: BaseException) -> RuntimeFault:
    """The runtime answered 2xx with something that is not runtime.v1."""
    return RuntimeFault(BAD_RESPONSE, op=op, status=None, upstream=str(err)[:300],
                        detail="the runtime answered with something agent-api could not read",
                        remedy="An operator should check that agent-api and the runtime run the "
                               "same release.")


def translate(op: str, err: BaseException) -> RuntimeFault:
    """Any failure of one runtime call, as a :class:`RuntimeFault` (an existing one passes through)."""
    if isinstance(err, RuntimeFault):
        return err
    if isinstance(err, urllib.error.HTTPError):
        return from_http_error(op, err)
    if isinstance(err, (urllib.error.URLError, OSError)):
        return from_transport_error(op, err)
    return from_bad_body(op, err)
