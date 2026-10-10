"""refusal_guard.py — a turn that hears the same refusal twice stops asking.

A worker dispatched with nobody in the loop (a scheduled routine, a background flow, a meeting run)
is refused every mail, calendar and connection verb with gateway-identity.v1 ``REFUSAL``
(``reason: human_session_required``). The refusal does not change between calls: nothing the agent
does inside the turn can put a person in it. An agent that does not read that keeps asking, under
other names and other routes, and each attempt costs a tool call and files a friction record.

So the harness counts. Two calls are the same refusal when the TOOL and the REASON match. On the
second one the tool result gains a stop instruction (do not retry; relay ``tell_your_person``; end
the turn). On the third the harness ends the turn itself. That the same {tool, reason} came back
three times is reported once, as one ``refusal-repeated`` event.

PURE. It reads tool-result text and counts; it imports nothing from product code (``llm/`` never
does). ``refusal_reason`` is also what ``worker.engine`` reads off a finished turn's ``tool-result``
summaries to stamp ``done.refused``, for every harness, including the ones whose tool loop runs in
a subprocess this module cannot reach into.
"""
from __future__ import annotations

import json
import re
from typing import Optional

#: The reason the person-in-the-loop refusal carries (gateway-identity.v1 ``REFUSAL["reason"]``).
PERSON_REASON = "human_session_required"
#: On which identical refusal the stop instruction is appended, and on which the turn ends.
STOP_AT = 2
END_AT = 3

_STATUS_REFUSED = re.compile(r'"status"\s*:\s*"refused"')
_REASON = re.compile(r'"reason"\s*:\s*"([a-z][a-z0-9_]{2,63})"')
_RIG_REFUSED = re.compile(r'"refused"\s*:\s*"([a-z][a-z0-9_]{2,63})"')
_TELL = re.compile(r'"tell_your_person"\s*:\s*"((?:[^"\\]|\\.){1,600})"')


def refusal_reason(text: object) -> str:
    """The structured refusal reason in a tool result, or ``""`` for anything that is not one.

    Narrow on purpose: a refusal is a body that SAYS it is one. The person-in-the-loop reason is
    matched by name, because it survives every renderer (``HTTP 403 human_session_required`` on the
    gateway MCP's first line, the JSON body on its last, an 80-character summary); otherwise a JSON
    ``status: refused`` with a ``reason``, or the rig's ``refused: <reason>``. A tool error that is
    not a refusal (a 404, a timeout, a typo in a path) is never counted."""
    s = text if isinstance(text, str) else json.dumps(text, default=str) if text is not None else ""
    if not s:
        return ""
    if PERSON_REASON in s:
        return PERSON_REASON
    if _STATUS_REFUSED.search(s):
        m = _REASON.search(s)
        if m:
            return m.group(1)
    m = _RIG_REFUSED.search(s)
    return m.group(1) if m else ""


def tell_your_person(text: object) -> str:
    """The refusal's ``tell_your_person`` sentence, when the body carries one; else ``""``."""
    s = text if isinstance(text, str) else ""
    m = _TELL.search(s)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"')
    except ValueError:
        return m.group(1)


def stop_note(tool: str, reason: str) -> str:
    """What the second identical refusal's tool result gains."""
    return (f"\n\n[vexa harness] `{tool}` has now been refused twice in this turn for the same "
            f"reason ({reason}). It will be refused again: nothing in this turn can change that. Do "
            "not call it again and do not look for another route to it. If the refusal carries a "
            "`tell_your_person` sentence, pass it on as it is, then end your turn.")


class RefusalGuard:
    """Counts identical {tool, reason} refusals across one turn. One instance per turn."""

    def __init__(self, *, stop_at: int = STOP_AT, end_at: int = END_AT) -> None:
        self.stop_at, self.end_at = stop_at, end_at
        self.counts: dict[tuple[str, str], int] = {}
        #: The last refusal's sentence for the person, kept so a turn the harness ends still says it.
        self.tell = ""
        #: ``{tool, reason, count}`` once a refusal reached ``end_at``; else None.
        self.ended: Optional[dict] = None

    def observe(self, tool: str, ok: bool, out: str) -> tuple[str, str]:
        """``(tool result as the model should read it, action)``. ``action`` is ``""``,
        ``"stop"`` (the note was appended) or ``"end"`` (end the turn now)."""
        if ok:
            return out, ""
        reason = refusal_reason(out)
        if not reason:
            return out, ""
        key = (str(tool or ""), reason)
        n = self.counts.get(key, 0) + 1
        self.counts[key] = n
        self.tell = tell_your_person(out) or self.tell
        if n >= self.end_at:
            self.ended = {"tool": key[0], "reason": reason, "count": n}
            return out + stop_note(*key), "end"
        if n >= self.stop_at:
            return out + stop_note(*key), "stop"
        return out, ""

    def ended_reply(self) -> str:
        """The reply for a turn the harness ended: the refusal's own sentence when it had one."""
        if self.tell:
            return self.tell
        r = self.ended or {}
        return (f"I stopped: `{r.get('tool', '')}` was refused {r.get('count', 0)} times for the "
                f"same reason ({r.get('reason', '')}), and trying again would not change that.")
