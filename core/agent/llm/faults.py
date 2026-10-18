"""faults.py — the MODEL PROVIDER's failure vocabulary: one typed fault, one shape (P18, ADR-0010).

THE ONE PLACE A PROVIDER FAILURE IS NAMED. Every harness (claude-code, codex, openai-agent, and any
provider adapter added later) translates its provider's failure into :class:`ProviderFault` and hands
it to the turn's stream on the ``done`` event, as ``done.fault``. Import it from here; do not define a
second one. The shape and the ``kind`` vocabulary are unit.v1's ``Fault`` (``core/agent/contracts/unit.v1``).

The shape is STABLE — the terminal renders it field for field::

    {"source":   "model-provider",
     "kind":     "unpaid" | "unauthorized" | "rate_limited" | "unavailable" | "refused",
     "provider": <host the request went to, e.g. "openrouter.ai">,
     "model":    <model id, or "">,
     "status":   <HTTP status, or None when the failure had none (a timeout)>,
     "detail":   <one safe line — the provider's own words, secrets redacted, or ours>,
     "remedy":   <one line a person can act on, or "">}

``kind`` from the status: 402 → unpaid · 401/403 → unauthorized · 429 → rate_limited ·
5xx/529/timeout/transport → unavailable · any other 4xx → refused. When there is no status to read
(a CLI that printed the failure as prose), the text is read for one — ``API Error: 402`` — and then
for the providers' own phrases ("insufficient credits", "rate limit"…).

Why this exists: the founder's next failure on the 0.13.2 demo stack is OpenRouter answering 402,
out of credit. Without a type it arrives as "Model inference failed" or as nothing at all, and the
person cannot tell an unpaid account from a broken agent (the same class as ADR-0010's STT 402).

This module imports nothing from product code (llm/README.md § Rules).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

from llm import fault_wire
from llm.errors import LLMError, provider_host

# THE VOCABULARY IS unit.v1's, NOT THIS MODULE'S (S65). `llm/fault_wire.py` is generated from
# core/agent/contracts/unit.v1/unit.schema.json — this brick's own copy, since llm/ imports nothing
# from product code — and gate:schema fails when it drifts. A kind is added there, never here.
_V = fault_wire.ModelProvider

logger = logging.getLogger("llm.faults")

SOURCE = _V.SOURCE

UNPAID = _V.UNPAID
UNAUTHORIZED = _V.UNAUTHORIZED
RATE_LIMITED = _V.RATE_LIMITED
UNAVAILABLE = _V.UNAVAILABLE
REFUSED = _V.REFUSED
KINDS = _V.KINDS

_WHAT = {
    UNPAID: "is out of credits",
    UNAUTHORIZED: "rejected the credential",
    RATE_LIMITED: "is limiting requests",
    UNAVAILABLE: "is unavailable",
    REFUSED: "refused the request",
}


@dataclass(frozen=True)
class ProviderFault:
    """A model provider's failure, typed. Build it with :func:`classify`; send it as ``as_dict()``."""

    kind: str
    provider: str = "unknown"
    model: str = ""
    status: Optional[int] = None
    detail: str = ""
    remedy: str = ""
    source: str = SOURCE

    def as_dict(self) -> dict:
        d = asdict(self)
        return {"source": d["source"], "kind": d["kind"], "provider": d["provider"],
                "model": d["model"], "status": d["status"], "detail": d["detail"],
                "remedy": d["remedy"]}

    def sentence(self) -> str:
        """The fault as one sentence — ``done.reply`` for a client that renders only the reply."""
        code = f" ({self.status})" if self.status else ""
        head = f"The model provider ({self.provider}) {_WHAT.get(self.kind, 'failed')}{code}."
        tail = " ".join(x for x in (self.detail and f"It said: {self.detail}", self.remedy) if x)
        return f"{head} {tail}".strip()


def remedy_for(kind: str, provider: str) -> str:
    """The one line a person can act on, per kind."""
    where = provider if provider and provider != "unknown" else "the provider"
    return {
        UNPAID: f"Add credits at {where}, or choose another model under Settings → Models.",
        UNAUTHORIZED: f"Check the API key for {where} under Settings → Models.",
        RATE_LIMITED: "Wait a moment and send it again.",
        UNAVAILABLE: "Send it again shortly, or choose another model under Settings → Models.",
        REFUSED: "Check the model name and its settings under Settings → Models.",
    }.get(kind, "")


def kind_for_status(status: int) -> Optional[str]:
    """The kind an HTTP status means, or None for a status that is not a failure."""
    if status == 402:
        return UNPAID
    if status in (401, 403):
        return UNAUTHORIZED
    if status == 429:
        return RATE_LIMITED
    if status >= 500 or status in (408, 529):
        return UNAVAILABLE
    if 400 <= status < 500:
        return REFUSED
    return None


# A status the provider's failure text carries: the claude CLI prints `API Error: 402 {...}`, an
# OpenAI-dialect body says `"code": 402`, and a proxy may say `status 429` / `HTTP 503`.
_STATUS_IN_TEXT = re.compile(
    r"api error:?\s*(\d{3})\b"
    r"|\b(?:status|http|code)[\"']?\s*[:=]?\s*(\d{3})\b"
    r"|^\s*(\d{3})\s+(?:payment required|unauthorized|forbidden|too many requests|"
    r"internal server error|bad gateway|service unavailable|gateway timeout)",
    re.IGNORECASE | re.MULTILINE)

_UNPAID_WORDS = re.compile(r"insufficient (?:credits?|balance|funds)|credit balance is too low|"
                           r"out of credits?|payment required|billing|add (?:more )?credits|"
                           r"quota exceeded.*billing|exceeded your current quota|insufficient_quota",
                           re.IGNORECASE)

_PHRASES = (
    (UNPAID, _UNPAID_WORDS),
    (RATE_LIMITED, re.compile(r"rate[ _-]?limit|too many requests", re.IGNORECASE)),
    (UNAUTHORIZED, re.compile(r"\bunauthori[sz]ed\b|invalid[ _-]*(?:x-)?api[ _-]*key|invalid[ _-]*bearer|"
                              r"authentication[ _-]*(?:error|failed)|no auth credentials|"
                              r"user not found|not[ _-]*logged[ _-]*in|please run /login|"
                              r"\bforbidden\b", re.IGNORECASE)),
    (UNAVAILABLE, re.compile(r"overloaded|timed? ?out|timeout|service unavailable|bad gateway|"
                             r"connection (?:refused|reset|error)|temporarily unavailable",
                             re.IGNORECASE)),
)

# The claude CLI's own SDK error label on an assistant message (`error: "billing_error"`).
_SDK_ERROR = {"billing_error": UNPAID, "authentication_failed": UNAUTHORIZED,
              "rate_limit": RATE_LIMITED, "server_error": UNAVAILABLE, "invalid_request": REFUSED}

_SECRETS = re.compile(r"(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}|bearer\s+\S+|api[_-]?key[=:]\s*\S+",
                      re.IGNORECASE)


# A LINK IN THE PROVIDER'S WORDS IS THE OPERATOR'S, NOT THE PERSON'S. OpenRouter's 402 ends "To
# increase, visit https://openrouter.ai/settings/keys and create a key…" — the page that manages the
# deployment's key. The person in the chat cannot use it and should not be sent there, so the
# sentence carrying it leaves the detail and the link goes to the worker's log (`provider_links`).
_LINK = re.compile(r"https?://[^\s\"'<>)\]]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s\"'<>)\]]*",
                   re.IGNORECASE)


def provider_links(text: object) -> list[str]:
    """Every link the provider's failure text carries — for the operator's log, never the chat."""
    return [m.group(0).rstrip(".,;:") for m in _LINK.finditer(str(text or ""))]


def _drop_links(text: str) -> str:
    """The text without any sentence that carries a link. A sentence is kept whole or not at all,
    so the detail never reads "visit  and create a key"."""
    if not _LINK.search(text):
        return text
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if s and not _LINK.search(s))


def safe_detail(text: object, limit: int = 200) -> str:
    """The provider's own words, made safe to show: the JSON envelope unwrapped to its message,
    anything credential-shaped redacted, any sentence carrying a link removed (the link is the
    operator's — see :func:`provider_links`), one line, bounded."""
    raw = str(text or "")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            body = json.loads(m.group(0))
            err = body.get("error", body) if isinstance(body, dict) else body
            msg = err.get("message") if isinstance(err, dict) else None
            if isinstance(msg, str) and msg.strip():
                raw = msg
        except ValueError:
            pass
    raw = re.sub(r"^\s*api error:?\s*\d{3}\s*", "", raw, flags=re.IGNORECASE)
    flat = " ".join(_SECRETS.sub("[redacted]", _drop_links(" ".join(raw.split()))).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def status_in(text: object) -> Optional[int]:
    """The HTTP status a failure text names, if it names one that is a failure."""
    for m in _STATUS_IN_TEXT.finditer(str(text or "")):
        code = int(next(g for g in m.groups() if g))
        if kind_for_status(code):
            return code
    return None


def classify(*, status: Optional[int] = None, text: object = None, provider: Optional[str] = None,
             model: Optional[str] = None, sdk_error: Optional[str] = None,
             transport: bool = False, kind: Optional[str] = None) -> Optional[ProviderFault]:
    """The typed fault for one provider failure, or None when nothing here says it is one.

    ``status`` — the HTTP status, when the adapter has it · ``text`` — the provider's / CLI's own
    words · ``sdk_error`` — the claude CLI's error label · ``transport`` — the request never got
    an answer (connect error, timeout) · ``kind`` — one of :data:`KINDS` the adapter already read
    from its vendor's own error label (codex's ``codexErrorInfo``); a status still wins over it.
    ``provider`` defaults to the configured endpoint's host."""
    host = provider or provider_host()
    hint = kind if kind in KINDS else None
    kind = None
    if status is not None:
        kind = kind_for_status(int(status))
    if kind is None:
        kind = hint
    if status is None and text:
        # The status the text names wins over the CLI's label, like an HTTP status does: the label
        # says "billing_error", the text says 402, and the person should see the 402.
        status = status_in(text)
        kind = kind or (kind_for_status(status) if status else None)
    if kind is None and sdk_error:
        kind = _SDK_ERROR.get(str(sdk_error))
    if kind is None and text:
        kind = next((k for k, rx in _PHRASES if rx.search(str(text))), None)
    if kind is None and transport:
        kind = UNAVAILABLE
    # NOT EVERY PROVIDER SAYS "UNPAID" WITH A 402. Anthropic answers an empty balance with a 400
    # ("Your credit balance is too low…") and OpenAI with a 429 (`insufficient_quota`) — both read as
    # something the person could fix by waiting or rewording, which they cannot. Their words win.
    if kind in (REFUSED, RATE_LIMITED, UNAUTHORIZED) and text and _UNPAID_WORDS.search(str(text)):
        kind = UNPAID
    if kind is None:
        return None
    detail = safe_detail(text) if text else ""
    remedy = remedy_for(kind, host)
    if kind == UNPAID and text and _ASKED_TOO_MUCH.search(str(text)):
        # "You requested up to 32000 tokens, but can only afford 4857": the balance covers a
        # smaller answer. The output cap is the operator's dial for exactly this.
        remedy = (f"Add credits at {host if host and host != 'unknown' else 'the provider'}, lower "
                  "the model's output cap (VEXA_AGENT_MAX_OUTPUT_TOKENS), or choose another model "
                  "under Settings → Models.")
    links = [_SECRETS.sub("[redacted]", u) for u in provider_links(text)] if text else []
    # THE OPERATOR'S COPY: what the person sees, plus the links the chat leaves out.
    logger.warning("model provider fault: provider=%s kind=%s status=%s model=%s detail=%r%s",
                   host, kind, status, model or "", detail,
                   f" links={links}" if links else "")
    return ProviderFault(kind=kind, provider=host, model=model or "", status=status,
                         detail=detail, remedy=remedy)


# The provider's own words for "your balance covers a smaller answer than you asked for".
_ASKED_TOO_MUCH = re.compile(r"fewer max_tokens|can only afford|max_tokens.*(?:credit|afford)",
                             re.IGNORECASE)


class ProviderError(LLMError):
    """An LLM call failed and the adapter knows which provider failure it was."""

    def __init__(self, message: str, fault: ProviderFault) -> None:
        super().__init__(message)
        self.fault = fault


def fault_of(exc: BaseException) -> Optional[ProviderFault]:
    """The typed fault an adapter attached to ``exc`` (``ProviderError``, or ``exc.fault``)."""
    f = getattr(exc, "fault", None)
    return f if isinstance(f, ProviderFault) else None
