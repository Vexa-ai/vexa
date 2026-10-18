"""The input port: what signals listens to. A meeting's verified transcript feed is the first
adapter (``meeting_feed.py``); a shared chat is the planned second. The runner and the listener
speak only this port, so nothing meeting-shaped reaches the core loop.

A source is a stream of entries with ordered ids. ``discover()`` names the sources with fresh
activity; ``open()`` prepares reading one from a time on; ``read()`` returns the entries this reader
must account for (re-delivered ones included, the caller skips those at or before ``after``);
``ack()`` confirms them; ``context()`` gives the lines before an entry; ``wake_target()`` is what the
wake event says about where it came from; ``listening()`` is the brief for that source together with
the company policy it runs under and whom a wake concerns. ``meta`` is what discovery learned about
a source (for a meeting: its owner), kept in the shared registry so any instance can resume it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from .brief import Brief
from .policy import Policy
from .model import Line


@dataclass(frozen=True)
class Activity:
    id: str
    at_ms: int
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Listening:
    brief: Brief
    policy: Policy
    subject: str = ""            # whom a wake concerns (a meeting: its owner)


@dataclass(frozen=True)
class Item:
    entry_id: str
    kind: str                       # "line" · "retract" · "end" · "other"
    line: Optional[Line] = None
    ids: tuple = field(default_factory=tuple)
    mode: str = "score"             # a line: "score" · "addressed" (the source's fixed rule tier) · "context"
    interim: bool = False           # an UNFINISHED line the rule tier heard (a meeting's draft segment)


class Source(Protocol):
    kind: str
    event_type: str

    async def discover(self) -> list: ...
    async def open(self, sid: str, from_ms: int) -> bool: ...
    async def read(self, sid: str, consumer: str, after: str) -> list: ...
    async def ack(self, sid: str, entry_ids: list) -> None: ...
    async def context(self, sid: str, before: str, n: int) -> list: ...
    def wake_target(self, sid: str) -> dict: ...
    async def listening(self, sid: str, meta: dict) -> Listening: ...
