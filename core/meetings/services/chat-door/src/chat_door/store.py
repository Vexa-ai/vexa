"""Lazy identity + the personal-instructions doc — the two things the door writes.

**Nothing exists for a person until they click.** Before the first verified magic link there
is no user row and no personal doc for that email; the first verified click creates both, and
every click after that is a no-op on creation. That is the whole of "lazy user creation" in
the spec, and :meth:`FileIdentityStore.ensure_user` is the only place it happens.

Layout on disk (one directory per email identity, keyed by a slug so the path is safe):

```
<store_dir>/users/<slug>/user.json                 # {email, created_at, ...}
<store_dir>/users/<slug>/personal-instructions.md  # created empty; appended by the door
```

The personal doc is a **visible, human-readable markdown file** on purpose — the spec's
auditability principle: the thing that steers the agent must be readable by the person it
steers. It starts empty (a title line only) and grows one dated entry per steer.

This is a v0 store: files, no database, no locking beyond append ordering in one process.
It exists so the write path is real and testable, not so it scales.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

_DOC_NAME = "personal-instructions.md"
_USER_NAME = "user.json"


def slugify_email(email: str) -> str:
    """A filesystem-safe, stable key for an email identity."""
    normalized = unicodedata.normalize("NFKD", email.strip().lower())
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug or "unknown"


@dataclass(frozen=True)
class User:
    email: str
    slug: str
    created_at: str
    doc_path: Path
    created_now: bool = False
    """True only on the call that actually created the row — the lazy-creation signal."""


class IdentityStore(Protocol):
    def get_user(self, email: str) -> User | None: ...
    def ensure_user(self, email: str, *, now: datetime | None = None) -> User: ...
    def append_instruction(
        self, email: str, text: str, *, meeting_id: str, now: datetime | None = None
    ) -> str: ...
    def read_instructions(self, email: str) -> str: ...


class FileIdentityStore:
    """The v0 :class:`IdentityStore` — one directory per identity under ``root``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _dir(self, email: str) -> Path:
        return self.root / "users" / slugify_email(email)

    # -- read ---------------------------------------------------------------

    def get_user(self, email: str) -> User | None:
        d = self._dir(email)
        rec = d / _USER_NAME
        if not rec.exists():
            return None
        data = json.loads(rec.read_text("utf-8"))
        return User(
            email=data["email"],
            slug=d.name,
            created_at=data["created_at"],
            doc_path=d / _DOC_NAME,
        )

    def read_instructions(self, email: str) -> str:
        doc = self._dir(email) / _DOC_NAME
        return doc.read_text("utf-8") if doc.exists() else ""

    # -- write --------------------------------------------------------------

    def ensure_user(self, email: str, *, now: datetime | None = None) -> User:
        """Create the user row + empty personal doc if absent. Idempotent."""
        existing = self.get_user(email)
        if existing is not None:
            return existing
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        d = self._dir(email)
        d.mkdir(parents=True, exist_ok=True)
        (d / _USER_NAME).write_text(
            json.dumps({"email": email, "created_at": stamp, "created_by": "chat-door"},
                       indent=2, sort_keys=True) + "\n",
            "utf-8",
        )
        doc = d / _DOC_NAME
        if not doc.exists():
            doc.write_text(
                f"# Personal instructions — {email}\n\n"
                "_What you tell the door lands here, dated. It shapes your next artifact._\n",
                "utf-8",
            )
        return User(email=email, slug=d.name, created_at=stamp, doc_path=doc, created_now=True)

    def append_instruction(
        self, email: str, text: str, *, meeting_id: str, now: datetime | None = None
    ) -> str:
        """Append one dated entry to the personal doc. Returns the entry as written."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("instruction text must be non-empty")
        user = self.ensure_user(email, now=now)
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"\n### {stamp} · via chat door · meeting {meeting_id}\n\n{cleaned}\n"
        with user.doc_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        return entry
