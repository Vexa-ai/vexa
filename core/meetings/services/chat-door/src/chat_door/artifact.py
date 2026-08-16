"""Reading a rendered artifact file — the small parser the postman needs.

A rendered artifact is a markdown file at ``<meeting>/<participant>.md`` whose first lines
carry the addressing header the renderer emits, in the meeting's own language:

```
**To:** Dmitry Grankin                     |  **Кому:** Алексей Рогов
**Meeting:** 2025-08-28 · Google Meet · 59m|  **Встреча:** 2026-05-05 · Microsoft Teams · 78 мин
**record:** 126 · [open the record](#)     |  **запись:** 11706 · [открыть запись](#)
```

Two things are pulled out and one is rewritten:

* **the record id** — the meeting id the door will be scoped to. The *record line* wins over
  the directory name: the corpus keys files by one id and states the record's own id on that
  line, and the door must be pointed at the record.
* **the recipient's display name** — used for the ``To:`` header when an email is supplied.
* **the placeholder link** ``](#)`` is rewritten to the magic link, and the "(placeholder
  link)" marker removed. If a future renderer emits a real link, nothing is rewritten and the
  postman says so rather than silently double-linking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TO_KEYS = ("To", "Кому", "An", "Para", "À", "Pour")
_MEETING_KEYS = ("Meeting", "Встреча", "Besprechung", "Reunión", "Réunion")
_RECORD_KEYS = ("record", "запись", "Record", "Запись", "registro", "enregistrement")

_SUBJECT_TAIL = {
    "ru": "что изменилось для тебя",
    "en": "what changed for you",
}


@dataclass(frozen=True)
class Artifact:
    path: Path
    body: str
    meeting_id: str
    recipient_name: str
    meeting_label: str
    language: str
    participant_slug: str

    @property
    def subject(self) -> str:
        tail = _SUBJECT_TAIL.get(self.language, _SUBJECT_TAIL["en"])
        label = self.meeting_label or f"meeting {self.meeting_id}"
        return f"{label} — {tail}"


def _field(body: str, keys: tuple[str, ...]) -> str:
    for key in keys:
        m = re.search(rf"^\*\*{re.escape(key)}:?\*\*\s*(.+)$", body, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def _looks_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text))


def load_artifact(path: Path | str) -> Artifact:
    p = Path(path)
    body = p.read_text("utf-8")
    record_line = _field(body, _RECORD_KEYS)
    meeting_id = ""
    m = re.match(r"^([A-Za-z0-9_-]+)", record_line)
    if m:
        meeting_id = m.group(1)
    if not meeting_id:
        meeting_id = p.parent.name
    return Artifact(
        path=p,
        body=body,
        meeting_id=meeting_id,
        recipient_name=_field(body, _TO_KEYS),
        meeting_label=_field(body, _MEETING_KEYS),
        language="ru" if _looks_cyrillic(body[:400]) else "en",
        participant_slug=p.stem,
    )


def with_record_link(body: str, link: str) -> tuple[str, bool]:
    """Rewrite the placeholder record link to ``link``. Returns ``(body, rewrote)``."""
    rewritten, count = re.subn(r"\]\(#\)", f"]({link})", body, count=1)
    if count:
        rewritten = re.sub(
            r"\s*\*\((?:placeholder link|ссылка-заглушка)\)\*", "", rewritten, count=1
        )
    return rewritten, bool(count)
