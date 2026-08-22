"""The BASE PATH: who gets mailed after an unbound meeting, and who gets nothing.

One pure function. Given a parsed invitation and a transcript summary it plans the outbound
mail for a meeting that is bound to no group:

- the **organiser** receives the artifact (a deterministic template until the model writes it),
  carrying the *assign to a group* affordance;
- every **organisation-domain participant** receives an invitation to chat with the meeting —
  this is how the product spreads inside an organisation;
- an attendee **outside the organisation's domain receives nothing**, and the refusal is logged,
  never silent;
- the assistant's own address is excluded from fan-out.

The gate verdict is an input, not a decision made here: ``send`` fans out, ``hold_for_creator``
mails the organiser alone, ``suppress`` mails nobody. Every decision — including every
suppression — becomes one log entry, because a delivery system that fails quietly is
indistinguishable from one that works.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .invite import ParsedMail

__all__ = ["EmailPlan", "BasePathResult", "plan_base_path"]


@dataclass(frozen=True)
class EmailPlan:
    to: str
    kind: str                    # "artifact" | "chat_invite"
    subject: str
    body: str


@dataclass(frozen=True)
class BasePathResult:
    sends: tuple[EmailPlan, ...]
    log: tuple[dict, ...]        # one entry per decision, suppressions included


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _artifact_body(parsed: ParsedMail, transcript_summary: str, assign_url: str) -> str:
    when = parsed.dtstart or "(unknown time)"
    people = ", ".join(p.get("email", "") for p in parsed.participants) or "(no roster)"
    return (
        f"Minutes of: {parsed.summary or '(untitled meeting)'}\n"
        f"When: {when}\n"
        f"Participants: {people}\n"
        f"\n{transcript_summary.strip()}\n\n"
        f"This meeting is yours alone. To share it — and every later occurrence — with a group:\n"
        f"{assign_url}\n"
    )


def _chat_invite_body(parsed: ParsedMail, organizer: str, chat_url: str) -> str:
    # The door mints the reader's WORKSPACE with this meeting in it — chat is always scoped to a
    # workspace; the meeting is the context it points at. The copy says what they get.
    return (
        f"You were in “{parsed.summary or 'a meeting'}” with {organizer}.\n"
        f"Vexa kept the minutes — in your own workspace, with this meeting in it.\n"
        f"Open it and ask anything:\n"
        f"{chat_url}\n"
    )


def plan_base_path(
    parsed: ParsedMail,
    *,
    org_domain: str,
    assistant: str,
    transcript_summary: str,
    verdict: str = "send",                     # "send" | "hold_for_creator" | "suppress"
    assign_url: str = "http://localhost:3000/assign",
    chat_url: str = "http://localhost:3000/chat",
) -> BasePathResult:
    org = org_domain.lower().lstrip("@")
    bot = assistant.lower()
    log: list[dict] = []
    sends: list[EmailPlan] = []
    uid = parsed.uid or parsed.message_id

    def entry(decision: str, to: str, reason: str) -> None:
        log.append({"uid": uid, "decision": decision, "to": to, "reason": reason})

    if not parsed.ok:
        entry("suppress", "*", f"rejected invite: {parsed.rejection.reason}")
        return BasePathResult((), tuple(log))

    if verdict == "suppress":
        entry("suppress", "*", "gate verdict: suppress")
        return BasePathResult((), tuple(log))

    organizer = (parsed.organizer or "").lower()
    if not organizer:
        entry("suppress", "*", "no organiser on the invitation")
        return BasePathResult((), tuple(log))
    if _domain(organizer) != org:
        # An outside organiser gets no artifact and their meeting spawns no fan-out at all.
        entry("suppress", "*", f"organiser outside org domain: {organizer}")
        return BasePathResult((), tuple(log))

    from urllib.parse import quote
    # The organiser's landing is WORKSPACE + MEETING: the assign chooser, with the meeting itself
    # open in the center — never a banner floating over an unrelated page.
    mref = (f"&meeting={quote(parsed.platform + '/' + parsed.native_meeting_id)}"
            if parsed.platform and parsed.native_meeting_id else "")
    art_uid = f"{assign_url}?assign={quote(uid or '')}&mtitle={quote(parsed.summary or '')}{mref}"
    sends.append(EmailPlan(
        to=organizer, kind="artifact",
        subject=f"Minutes — {parsed.summary or 'your meeting'}",
        body=_artifact_body(parsed, transcript_summary, art_uid)))
    entry("send", organizer, "organiser artifact"
          + (" (held: gate verdict hold_for_creator)" if verdict == "hold_for_creator" else ""))

    if verdict == "hold_for_creator":
        for p in parsed.participants:
            addr = (p.get("email") or "").lower()
            if addr and addr not in (organizer, bot):
                entry("suppress", addr, "gate verdict: hold_for_creator")
        return BasePathResult(tuple(sends), tuple(log))

    for p in parsed.participants:
        addr = (p.get("email") or "").lower()
        if not addr or addr == organizer:
            continue
        if addr == bot:
            entry("skip", addr, "the assistant itself")
            continue
        if _domain(addr) != org:
            entry("suppress", addr, "outside org domain")
            continue
        # The chat door deep-links the meeting itself when the invite carried a resolvable
        # conference link; the uid form is the fallback the terminal can still look up.
        ref = (f"{chat_url}?meeting={parsed.platform}/{parsed.native_meeting_id}"
               if parsed.platform and parsed.native_meeting_id else f"{chat_url}?uid={uid}")
        sends.append(EmailPlan(
            to=addr, kind="chat_invite",
            subject=f"Chat with “{parsed.summary or 'your meeting'}”",
            body=_chat_invite_body(parsed, organizer, ref)))
        entry("send", addr, "org-domain participant chat invite")

    return BasePathResult(tuple(sends), tuple(log))
