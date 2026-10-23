"""express.py — the acts that never wait behind a running turn (2026-10-13, app.dev).

A chat busy with a long turn queues the person's next messages. Most of them should wait: they are
asks for the model. A meeting link, or a plain bot command, should not: the meeting is happening now,
and "send bot" sitting behind an 18-step page edit is a bot that never went. The worker's loop asks
this lane about each message waiting at the head of the queue while a turn runs; one it claims is
carried out at once, beside the turn, through the same MCP attachment and the same tools the model
would use (``meeting_link``), and answered with one line. The running turn and the next one are told.

What is claimed — the person's own words only, never a composed or marked turn:
* a meeting URL, alone or with a few command words ("send bot", "join"): send the bot;
* a bot command without a URL ("send bot", "stop the bot") when this worker already acted on a
  meeting: the same meeting.
Anything else is an ordinary message and keeps its place in the queue.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from shared.marks import MACHINERY_MARK, PHASE_MARK, act_label, read_job_mark, turn_namespace

_URL = re.compile(r"https?://\S+", re.I)
#: The words a bot command may carry around (or instead of) the link. Anything else is a sentence.
_COMMAND = {"send", "the", "a", "bot", "vexa", "please", "pls", "join", "now", "in", "to", "into", "add",
            "invite", "it", "this", "that", "meeting", "call", "stop", "remove", "leave", "kick", "out",
            "of", "go", "and", "agent", "notetaker", "here", "link"}
_SEND = re.compile(r"\b(send|add|invite|join)\b", re.I)
_STOP = re.compile(r"\b(stop|remove|leave|kick)\b", re.I)
_PLATFORMS = {"google_meet": "Google Meet", "teams": "Microsoft Teams", "zoom": "Zoom",
              "jitsi": "Jitsi"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", _URL.sub(" ", text.lower()))


class Express:
    """``send(link) -> (events, meeting_link_event)``; ``stop(platform, native_id) -> str`` (a line)."""

    def __init__(self, *, send: Callable, stop: Optional[Callable] = None) -> None:
        self._send = send
        self._stop = stop
        self.last: Optional[dict] = None     # the meeting this worker last acted on
        self.notes: list[str] = []           # what the running and the next turn are told

    def _kind(self, text: str) -> str:
        text = (text or "").strip()
        if not text or len(text) > 400:
            return ""
        words = _words(text)
        if any(w not in _COMMAND for w in words):
            return ""
        urls = _URL.findall(text)
        if len(urls) == 1:
            return "stop" if _STOP.search(text) else "send"
        if urls:
            return ""
        if not words or "bot" not in words or self.last is None:
            return ""
        if _STOP.search(text):
            return "stop" if self._stop else ""
        return "send" if _SEND.search(text) else ""

    def claims(self, text: str, prompt: str = "") -> bool:
        if prompt and (MACHINERY_MARK in prompt or PHASE_MARK in prompt or read_job_mark(prompt) is not None
                       or act_label(prompt) is not None or turn_namespace(prompt)):
            return False
        return bool(self._kind(text))

    def run(self, text: str) -> "tuple[list[dict], str]":
        kind = self._kind(text)
        urls = _URL.findall(text or "")
        if kind == "stop":
            m = self.last or {}
            line = self._stop(m.get("platform", ""), m.get("native_meeting_id", "")) if self._stop else ""
            line = line or f"Asked the bot to leave {self._where(m)}"
            self.notes.append(line)
            return [], line
        link = urls[0] if urls else (self.last or {}).get("url", "")
        events, ev = self._send(link)
        if ev is None:
            line = "That link is not a meeting link Vexa can join."
        else:
            if ev.get("outcome") in ("requested", "already_there"):
                self.last = {"url": link, "platform": ev.get("platform", ""),
                             "native_meeting_id": ev.get("native_meeting_id", "")}
            line = self._line(ev)
        self.notes.append(line)
        return list(events or []), line

    @staticmethod
    def _where(ev: dict) -> str:
        p = _PLATFORMS.get(str(ev.get("platform") or ""), str(ev.get("platform") or "the meeting"))
        return f"{p} {ev.get('native_meeting_id') or ''}".strip()

    def _line(self, ev: dict) -> str:
        where = self._where(ev)
        if ev.get("outcome") == "requested":
            return f"Bot sent to {where} — waiting for admission"
        if ev.get("outcome") == "already_there":
            return f"A bot is already in {where}"
        r = ev.get("refusal") or {}
        return f"The bot was not sent to {where}: {r.get('detail') or 'refused'}"

    def take_notes(self) -> str:
        """What happened beside the conversation since the last turn, once, for the next turn's prompt."""
        if not self.notes:
            return ""
        out = ("## Meanwhile, on the person's request (already done — do not repeat it)\n\n"
               + "\n".join(f"- {n}" for n in self.notes) + "\n\n")
        self.notes = []
        return out
