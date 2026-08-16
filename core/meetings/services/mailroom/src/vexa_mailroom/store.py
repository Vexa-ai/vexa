"""State — the binding store, the resume cursor and the notice log, in memory and on disk.

v0 keeps state in ONE JSON file (``MAILROOM_STATE_PATH``) written atomically (tmp + ``os.replace``),
because the mailroom has exactly one writer and the state is small: a handful of bindings, a
cursor, and a bounded notice log. It is deliberately NOT a table in the control plane's database —
the mailroom consumes meeting-api over its public API and owns no schema there (P23: one writer per
carrier). When the mailroom graduates past dev, this file swaps for a real store behind the same
``BindingStore`` port and nothing above it changes.

Resume-safety is two things, not one:

* **the cursor** — the arrival stamp of the newest message processed, so a restart re-reads a small
  tail instead of the whole mailbox; and
* **the seen list** — the last ``SEEN_LIMIT`` message ids, because arrival stamps are not unique
  (two invitations can land in the same second) and a cursor alone would either re-process or skip
  one of them. Re-processing is harmless (SEQUENCE idempotency catches it) — skipping is not.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from .ports import Binding, Notice

SEEN_LIMIT = 500
NOTICE_LIMIT = 200


class MemoryStore:
    """In-memory ``BindingStore`` + ``NoticeSink`` (the tests' store; also the file store's core)."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], Binding] = {}
        self._cursor: Optional[str] = None
        self._seen: list[str] = []
        self._notices: list[Notice] = []
        self._lock = asyncio.Lock()

    # --- BindingStore ---
    async def get(self, workspace_id: str, uid: str) -> Optional[Binding]:
        return self._bindings.get((workspace_id, uid))

    async def put(self, binding: Binding) -> None:
        self._bindings[binding.key] = binding
        await self._flush()

    async def all(self) -> Sequence[Binding]:
        return list(self._bindings.values())

    async def cursor(self) -> Optional[str]:
        return self._cursor

    async def seen(self) -> Sequence[str]:
        return list(self._seen)

    async def set_cursor(self, value: Optional[str], seen: Sequence[str]) -> None:
        self._cursor = value
        self._seen = list(seen)[-SEEN_LIMIT:]
        await self._flush()

    # --- NoticeSink ---
    async def record(self, notice: Notice) -> None:
        self._notices.append(notice)
        del self._notices[:-NOTICE_LIMIT]
        await self._flush()

    async def recent(self, limit: int = 50) -> Sequence[Notice]:
        return list(self._notices[-limit:])

    # --- persistence hook (no-op in memory) ---
    async def _flush(self) -> None:
        return None

    def snapshot(self) -> dict:
        return {
            "cursor": self._cursor,
            "seen": self._seen,
            "bindings": [b.as_dict() for b in self._bindings.values()],
            "notices": [n.as_dict() for n in self._notices],
        }

    def restore(self, data: dict) -> None:
        self._cursor = data.get("cursor")
        self._seen = list(data.get("seen") or [])
        self._bindings = {}
        for row in data.get("bindings") or []:
            b = Binding.from_dict(row)
            self._bindings[b.key] = b
        self._notices = [Notice(**{k: v for k, v in n.items() if k in Notice.__dataclass_fields__})
                         for n in (data.get("notices") or [])]


class FileStore(MemoryStore):
    """``MemoryStore`` that reads its state at construction and rewrites it atomically on change."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            try:
                self.restore(json.loads(self.path.read_text("utf-8") or "{}"))
            except (json.JSONDecodeError, OSError, TypeError):
                # A truncated state file must not wedge the poller: start clean and re-derive.
                # Re-processing is safe (SEQUENCE idempotency); refusing to boot is not.
                pass

    async def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.snapshot(), indent=1, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".mailroom-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except Exception:                                    # pragma: no cover - defensive
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
