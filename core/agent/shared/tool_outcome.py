"""tool_outcome.py — how a tool call ended, for the step that made it (held-label-rooms-1018).

The chat drew "✓ Wrote · 7 steps" over a reply that said "Nothing was written. The refactored
README is held": the privacy hold (D6) had refused the write, and the step line still read it as
done, because a step's tick came from the next step's arrival and nothing on the event said HOW the
call ended. ``result_outcome`` says it, once, for every harness's ``tool-result`` event
(``llm/tool_events``) and for the history reader (``control_plane/workspace_reader``): ``held`` (the
privacy hold refused the write and kept it for the person's approval, with the card's fields under
``held``), ``refused`` (the platform refused the call for another reason) or ``failed``. A call that
worked carries no outcome.
"""
from __future__ import annotations

import json


def _text(content: object) -> str:
    """A tool result as one string: a bare string, a list of content blocks, or an MCP envelope."""
    if isinstance(content, dict) and isinstance(content.get("content"), list):
        content = content["content"]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if isinstance(content, dict):
        return json.dumps(content, default=str)
    return ""

HELD_REASON = "private_sources_to_shared"
#: How much of a result is searched for a refusal body.
_SCAN = 16000
_HELD_FIELDS = ("workspace", "workspace_name", "path", "readers", "sources")


def _refusal_in(text: str) -> "dict | None":
    """The first JSON object in ``text`` that carries a ``refused`` slug (an error string often
    wraps the body: `HTTP 409: {"detail": {...}}`), with a ``detail`` envelope unwrapped."""
    text = text[:_SCAN]                  # a refusal body leads its result; a long read is not parsed
    if "refused" not in text:
        return None
    dec = json.JSONDecoder()
    at = text.find("{")
    while at >= 0:
        try:
            obj, _end = dec.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        for cand in (obj, obj.get("detail") if isinstance(obj, dict) else None):
            if isinstance(cand, dict) and isinstance(cand.get("refused"), str):
                return cand
        at = text.find("{", at + 1)
    return None


def result_outcome(ok: bool, content: object) -> dict:
    """The fields a ``tool-result`` event carries about how the call ended: ``{}`` when it worked,
    else ``{"outcome": "held"|"refused"|"failed"}`` — a held write also carries ``held``: the
    confirmation code and what the approval card shows (never the content that was held)."""
    text = _text(content)
    found = _refusal_in(text) if text else None
    if found and found.get("refused") == HELD_REASON and isinstance(found.get("confirm_code"), str):
        held = {"code": found["confirm_code"]}
        for k in _HELD_FIELDS:
            v = found.get(k)
            if isinstance(v, list):
                held[k] = [str(x) for x in v][:50]
            elif isinstance(v, str) and v:
                held[k] = v
        return {"outcome": "held", "held": held}
    if ok:
        return {}
    return {"outcome": "refused" if found else "failed"}
