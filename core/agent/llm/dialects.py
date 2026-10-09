"""dialects.py — judge a model endpoint by the SHAPE it answered, not by its status code.

Two wire dialects reach a model endpoint from this codebase and they are NOT interchangeable:

| dialect     | path                   | auth header            | success body                  |
|-------------|------------------------|------------------------|-------------------------------|
| ``openai``  | ``/chat/completions``  | ``Authorization: Bearer`` | ``{"choices":[{"message":…}]}`` |
| ``anthropic`` | ``/v1/messages``     | ``x-api-key``          | ``{"content":[{"type":"text"…}]}`` |

A gateway that answers **HTTP 200 with the other dialect's body** used to be indistinguishable
from success here: ``choices`` was absent, the extraction fell through to ``""``, and the caller
got an EMPTY completion with no error (a meeting note that silently wrote nothing). The same
hole swallowed an HTML page from a CDN sitting in front of the gateway. Vexa-ai/vexa#1666 is that
hole seen from the operator's side.

So the extractors here are STRICT and they name what they found: which dialect was spoken, which
one answered, and which setting moves. Status-code errors stay with the adapters; this module only
judges 2xx bodies — plus ``parse_headers``/``render_headers`` for the per-endpoint extra headers
(#1667), shared by the adapters and the dispatch overlay so one format is parsed in one place.
"""
from __future__ import annotations

import json
from typing import Mapping, Optional

from llm.errors import LLMError

OPENAI = "openai"
ANTHROPIC = "anthropic"

# The one place the two call shapes are spelled, so a message can name the other one precisely.
_PATH = {OPENAI: "/chat/completions", ANTHROPIC: "/v1/messages"}
_AUTH = {OPENAI: "Authorization: Bearer", ANTHROPIC: "x-api-key"}
_LABEL = {OPENAI: "an OpenAI chat-completions", ANTHROPIC: "an Anthropic Messages"}

_MAX_SNIPPET = 200


# ── extra request headers (#1667) ─────────────────────────────────────────────────────────────

def parse_headers(raw: object) -> dict:
    """Extra request headers from config, in EITHER accepted spelling.

    ``Name: Value`` one per line is the canonical one — it is the format the ``claude`` CLI's own
    ``ANTHROPIC_CUSTOM_HEADERS`` takes, so the value a user types travels to both call shapes
    unchanged. A JSON object is also accepted (that is what #1667 proposed, and what an API client
    naturally sends). Anything unparseable yields ``{}``: a bad header string must never be the
    reason a turn dies — the request simply goes without it, and the Test button says so.

    Blank names, blank values and duplicate names (last wins) are dropped. ``Mapping`` in,
    ``dict[str, str]`` out.
    """
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        return {str(k).strip(): str(v).strip()
                for k, v in raw.items() if str(k).strip() and str(v).strip()}
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            return parse_headers(json.loads(text))
        except ValueError:
            return {}
    out: dict = {}
    for line in text.replace("\r\n", "\n").replace("\\n", "\n").split("\n"):
        name, sep, value = line.partition(":")
        if not sep:
            continue
        name, value = name.strip(), value.strip()
        if name and value:
            out[name] = value
    return out


def render_headers(headers: Mapping) -> str:
    """The ``Name: Value`` newline form — what ``ANTHROPIC_CUSTOM_HEADERS`` parses (the CLI splits
    on ``\\n``/``\\r\\n``). Round-trips ``parse_headers``."""
    return "\n".join(f"{k}: {v}" for k, v in headers.items() if k and v)


def redact_headers(headers: Mapping) -> dict:
    """Header NAMES in the clear, values masked — for logs and for the config Test verdict. A
    routing header is not a secret but an entitlement/session header usually is, and which header
    names are configured is the diagnostic operators actually need."""
    out: dict = {}
    for name, value in headers.items():
        text = str(value or "")
        out[str(name)] = "********" + (text[-4:] if len(text) > 8 else "")
    return out


# ── response shape ────────────────────────────────────────────────────────────────────────────

def detect_shape(payload: object) -> Optional[str]:
    """Which dialect's SUCCESS body this is — ``"openai"``, ``"anthropic"`` or ``None``.

    Judged on the discriminating members only (``choices`` / ``object: chat.completion`` vs
    ``content`` blocks / ``type: message``), so an error envelope from either vendor stays
    ``None`` rather than being mistaken for a reply."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("choices"), list) or payload.get("object") in (
            "chat.completion", "chat.completion.chunk"):
        return OPENAI
    if isinstance(payload.get("content"), list) or (
            payload.get("type") == "message" and payload.get("role") == "assistant"):
        return ANTHROPIC
    return None


def _snippet(text: str) -> str:
    return " ".join((text or "").split())[:_MAX_SNIPPET]


def _mismatch_message(spoke: str, answered: str, base: str) -> str:
    other = _PATH[answered]
    return (
        f"DIALECT MISMATCH at {base}: POST {_PATH[spoke]} answered HTTP 200 with "
        f"{_LABEL[answered]} body. The endpoint speaks the {answered} dialect on this path, not "
        f"{spoke} — that answer cannot be read as a completion, and the claude-code harness "
        f"rejects it as 'empty or malformed'. Point this call shape at an endpoint serving "
        f"{_PATH[spoke]} (auth: {_AUTH[spoke]}), or use the gateway's {other} path."
    )


def _body_message(spoke: str, base: str, body: str, content_type: str) -> str:
    """Why a 2xx body could not be JSON at all — the CDN/gateway interception case."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    head = (body or "").lstrip()[:1]
    if not (body or "").strip():
        return (f"{base} answered POST {_PATH[spoke]} with HTTP 200 and an EMPTY body — nothing to "
                f"parse. A proxy or gateway in front of the endpoint is the usual cause.")
    if ctype in ("text/html", "application/xhtml+xml") or head == "<":
        return (f"{base} answered POST {_PATH[spoke]} with an HTML page, not an API response "
                f"(content-type {ctype or 'unset'}) — a CDN or gateway (e.g. Cloudflare) in front "
                f"of the endpoint intercepted the request. Body: {_snippet(body)}")
    return (f"{base} answered POST {_PATH[spoke]} with a non-JSON body (content-type "
            f"{ctype or 'unset'}) — expected {_LABEL[spoke]} response. Body: {_snippet(body)}")


def _payload(spoke: str, base: str, body: str, content_type: str) -> dict:
    try:
        payload = json.loads(body)
    except ValueError:
        raise LLMError(_body_message(spoke, base, body, content_type)) from None
    if not isinstance(payload, dict):
        raise LLMError(_body_message(spoke, base, body, content_type))
    answered = detect_shape(payload)
    if answered and answered != spoke:
        raise LLMError(_mismatch_message(spoke, answered, base))
    return payload


def openai_text(body: str, *, base: str, content_type: str = "") -> str:
    """The assistant text out of a 2xx ``/chat/completions`` body — or a NAMED failure."""
    payload = _payload(OPENAI, base, body, content_type)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(
            f"{base} answered POST {_PATH[OPENAI]} with JSON carrying no 'choices' — not "
            f"{_LABEL[OPENAI]} response. Body: {_snippet(body)}")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    message = message if isinstance(message, dict) else {}
    refusal = message.get("refusal")
    if refusal:
        raise LLMError(f"{base} refused the completion: {_snippet(str(refusal))}")
    content = message.get("content")
    if isinstance(content, list):  # some gateways emit the multimodal part-list form
        content = "".join(p.get("text", "") for p in content
                          if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    text = "" if content is None else str(content)
    if not text.strip():
        raise LLMError(
            f"{base} answered POST {_PATH[OPENAI]} with an EMPTY completion (finish_reason "
            f"{choices[0].get('finish_reason') if isinstance(choices[0], dict) else None!r}). "
            f"An empty reply is a failed beat, not a result — check the model name and the "
            f"gateway's routing.")
    return text


def anthropic_text(body: str, *, base: str, content_type: str = "") -> str:
    """The assistant text out of a 2xx ``/v1/messages`` body — or a NAMED failure."""
    payload = _payload(ANTHROPIC, base, body, content_type)
    blocks = payload.get("content")
    if not isinstance(blocks, list) or not blocks:
        raise LLMError(
            f"{base} answered POST {_PATH[ANTHROPIC]} with JSON carrying no 'content' blocks — "
            f"not {_LABEL[ANTHROPIC]} response. Body: {_snippet(body)}")
    text = "".join(b.get("text", "") for b in blocks
                   if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        kinds = sorted({b.get("type") for b in blocks if isinstance(b, dict)} - {None})
        raise LLMError(
            f"{base} answered POST {_PATH[ANTHROPIC]} with no text content (blocks: "
            f"{kinds or 'none'}; stop_reason {payload.get('stop_reason')!r}). An empty reply is a "
            f"failed beat, not a result — check the model name and the gateway's routing.")
    return text
