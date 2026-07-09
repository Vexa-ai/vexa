"""Shared fixtures for the cookbook test ladder.

L0 (composition) uses `FakeSlim` — fake `.agent`/`.meetings` sub-clients that RECORD every call and
return canned data. It lets us assert each cookbook verb calls the right SDK methods, in the right order,
with the right arguments, and folds the result into the right shape — with zero IO.
"""
from __future__ import annotations

import pytest


class _Recorder:
    """Base for a fake sub-client: every awaited method appends (name, kwargs) to the shared call log."""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def _record(self, _method: str, **kwargs) -> None:
        self._calls.append((_method, kwargs))


class FakeAgent(_Recorder):
    # canned live events the agent "emits" during a watch window (mixed types, vocabulary-agnostic)
    WATCH_EVENTS = [
        {"type": "transcript", "text": "hello"},
        {"type": "note", "note": {"text": "a note"}},
        {"type": "card", "card": {"kind": "decision", "title": "ship it"}},
        {"type": "card", "card": {"kind": "action", "title": "follow up"}},
    ]

    async def start_processing(self, native, *, platform="google_meet"):
        self._record("start_processing", native=native, platform=platform)
        return {"ok": True, "on": True, "native": native}

    async def stop_processing(self, native, *, platform="google_meet"):
        self._record("stop_processing", native=native, platform=platform)
        return {"ok": True, "on": False, "native": native}

    async def watch(self, native, *, seconds, on_event):
        self._record("watch", native=native, seconds=seconds)
        for evt in self.WATCH_EVENTS:
            on_event(evt)
        return {e["type"]: 1 for e in self.WATCH_EVENTS}

    async def chat(self, prompt, *, session=None, active=None, files=None, on_event=None):
        self._record("chat", prompt=prompt, session=session, active=active, files=files)
        return "REPLY"

    async def read_doc(self, native):
        self._record("read_doc", native=native)
        return {"content": "# meeting doc"}

    async def workspace_tree(self, *, hidden=False):
        self._record("workspace_tree", hidden=hidden)
        return ["CLAUDE.md", "kg/entities/person/jane.md"]

    async def workspace_file(self, path):
        self._record("workspace_file", path=path)
        return {"content": f"contents of {path}"}

    async def init_workspace(self):
        self._record("init_workspace")
        return {"ok": True, "seeded": True}

    async def swap_workspace(self):
        self._record("swap_workspace")
        return {"ok": True}

    async def create_routine(self, name, *, cron, prompt, run_now=False):
        self._record("create_routine", name=name, cron=cron, prompt=prompt, run_now=run_now)
        return {"routine": {"name": name, "cron": cron}, "job_id": "job-1", "ran_now": run_now}

    async def list_routines(self):
        self._record("list_routines")
        return [{"name": "morning-digest", "cron": "30 9 * * mon-fri", "enabled": True}]

    async def set_routine_enabled(self, name, *, enabled):
        self._record("set_routine_enabled", name=name, enabled=enabled)
        return {"ok": True, "name": name, "enabled": enabled}


class FakeMeetings(_Recorder):
    async def send_bot(self, native, *, url, platform="google_meet", bot_name="VexaSlim", language="en"):
        self._record("send_bot", native=native, url=url, platform=platform)
        return {"ok": True, "bot": bot_name}

    async def stop_bot(self, native, *, platform="google_meet"):
        self._record("stop_bot", native=native, platform=platform)
        return 200


class FakeSlim:
    """Stand-in for `Slim`: two recording sub-clients sharing one ordered call log (`slim.calls`)."""

    def __init__(self) -> None:
        self.calls: list = []
        self.base = "http://fake-gateway"
        self._headers = {"X-API-Key": "fake-key"}
        self.agent = FakeAgent(self.calls)
        self.meetings = FakeMeetings(self.calls)

    async def auth_me(self) -> dict:
        self.calls.append(("auth_me", {}))
        return {"user_id": 1, "email": "fake@vexa.ai", "scopes": ["bot"]}

    def names(self) -> list:
        """The ordered method names called across both sub-clients."""
        return [name for name, _ in self.calls]

    def last(self, name: str) -> dict:
        """The kwargs of the most recent call to `name`."""
        for n, kw in reversed(self.calls):
            if n == name:
                return kw
        raise AssertionError(f"{name} was never called; calls={self.names()}")


@pytest.fixture
def slim() -> FakeSlim:
    return FakeSlim()
