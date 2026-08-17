"""The artifact schema — one shape, whatever renders it.

An artifact is *the rendered context delta for one person*: what changed in their context
because this meeting happened. This module says what that document **is** — a small typed
value with a canonical markdown emitter — so that every renderer, deterministic or
model-driven, produces the identical document shape and every consumer parses one form.

**Why a schema and not a prose contract.** Two renderers built against a prose description
in one night emitted structurally different headers — one bold (``**To:**``), one plain
(``To:``) with the date line carrying no key at all. The downstream postman read only the
bold form, silently fell back to the *directory name* for the record id, and mailed magic
links pointing at the wrong meeting (5174 where the artifact was about 5175), with the
subject degraded to a bare ``meeting 5174``. Nothing errored. A prose contract cannot fail
loudly; a type can.

Three properties follow from that incident and are load-bearing here:

* **``meeting_id`` is the record's OWN id** — what the payload states, never the filename,
  the directory, or the id that was asked for. :class:`Artifact` has no way to express the
  other thing.
* **The header keys and section order are fixed** by :mod:`.labels`, not by the renderer.
* **``record_link`` is a slot.** A renderer leaves it empty and the emitter writes the
  placeholder the postman rewrites; a caller that already holds the magic link sets it and
  the emitter writes it directly. Both spellings are the same document.

Serialization is symmetric (``to_dict`` / ``from_dict``) because the run log doubles as the
fixture stream: an artifact recorded today has to be re-readable when it becomes a judge
fixture tomorrow.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from .labels import KIND_ORDER, Vocabulary, vocabulary_for

#: Bumped when the emitted document or the serialized shape changes incompatibly.
ARTIFACT_SCHEMA_VERSION = 1

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify(name: str) -> str:
    """A file-safe, ASCII key for a display name.

    Transliterates Cyrillic and folds Latin accents, because the slug names a file and an
    email header (``X-Vexa-Artifact``) and both are read by people on a terminal.
    """
    lowered = (name or "").strip().lower()
    folded = "".join(_TRANSLIT.get(ch, ch) for ch in lowered)
    ascii_form = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_form).strip("-")
    return slug or "participant"


@dataclass(frozen=True)
class Recipient:
    """Who one artifact is for.

    ``identity`` is the pipeline's stable key for this person — the email when one is
    known, otherwise ``name:<slug>``. It is what idempotency and the run log are keyed on,
    and the ``name:`` prefix is deliberately visible: a participant we cannot address is a
    fact worth reading off the log, not a silent absence.
    """

    display_name: str
    email: str | None = None
    is_creator: bool = False

    @property
    def slug(self) -> str:
        return slugify(self.display_name)

    @property
    def identity(self) -> str:
        return self.email.strip().lower() if self.email else f"name:{self.slug}"

    @property
    def addressable(self) -> bool:
        return bool(self.email)

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "email": self.email,
            "is_creator": self.is_creator,
            "identity": self.identity,
            "slug": self.slug,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Recipient":
        return cls(
            display_name=str(d.get("display_name") or ""),
            email=d.get("email") or None,
            is_creator=bool(d.get("is_creator")),
        )


@dataclass(frozen=True)
class Section:
    """One block of the delta.

    ``kind`` is the machine key (stable across languages, ordered by
    :data:`~vexa_artifact_pipeline.labels.KIND_ORDER`); ``title`` is what the reader sees.
    ``items`` renders as a bullet list, ``body`` as a paragraph; a section may carry either
    or both, and a section with neither is dropped by the emitter rather than printed
    empty.
    """

    kind: str
    title: str
    items: tuple[str, ...] = ()
    body: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.items and not self.body.strip()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "items": list(self.items), "body": self.body}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Section":
        return cls(
            kind=str(d.get("kind") or ""),
            title=str(d.get("title") or ""),
            items=tuple(str(i) for i in (d.get("items") or ())),
            body=str(d.get("body") or ""),
        )


@dataclass(frozen=True)
class Artifact:
    """The rendered context delta for one person, as data.

    ``meeting_id`` is the record's own id — the one the payload states. It is the id the
    magic link is scoped to and the id the record line carries, and nothing in the pipeline
    is allowed to substitute a filename or a requested id for it.
    """

    recipient: Recipient
    meeting_id: str
    meeting_label: str
    language: str = "en"
    sections: tuple[Section, ...] = ()
    #: Empty until a delivery path mints the record link. Empty renders the placeholder
    #: form the postman rewrites; set renders the real link and drops the placeholder.
    record_link: str = ""
    #: Which renderer produced this, so a fixture can be read back against its author.
    renderer: str = ""
    schema_version: int = ARTIFACT_SCHEMA_VERSION

    # -- shape ---------------------------------------------------------------

    @property
    def ordered_sections(self) -> tuple[Section, ...]:
        """Non-empty sections in the canonical order. Unknown kinds keep their own order,
        after the known ones — a new renderer can add a section without reordering the
        document a reader already recognises."""
        known = [s for s in self.sections if not s.is_empty and s.kind in KIND_ORDER]
        extra = [s for s in self.sections if not s.is_empty and s.kind not in KIND_ORDER]
        known.sort(key=lambda s: KIND_ORDER.index(s.kind))
        return tuple(known + extra)

    @property
    def is_empty(self) -> bool:
        """No section survived — the meeting changed nothing this reader can be told."""
        return not self.ordered_sections

    @property
    def vocabulary(self) -> Vocabulary:
        return vocabulary_for(self.language)

    def with_link(self, link: str) -> "Artifact":
        return replace(self, record_link=link)

    # -- emission ------------------------------------------------------------

    def to_markdown(self) -> str:
        """The canonical document. The only place artifact markdown is written."""
        v = self.vocabulary
        link = self.record_link or "#"
        tail = "" if self.record_link else f" *{v.placeholder}*"
        lines = [
            f"**{v.to}:** {self.recipient.display_name}",
            f"**{v.meeting}:** {self.meeting_label}",
            f"**{v.record}:** {self.meeting_id} · [{v.open_record}]({link}){tail}",
            "",
            "---",
            "",
        ]
        for section in self.ordered_sections:
            lines.append(f"**{section.title}**")
            lines.append("")
            if section.body.strip():
                lines.append(section.body.strip())
                lines.append("")
            for item in section.items:
                lines.append(f"- {item}")
            if section.items:
                lines.append("")
        lines.append(f"*{v.footer}*")
        return "\n".join(lines).rstrip() + "\n"

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "meeting_id": self.meeting_id,
            "meeting_label": self.meeting_label,
            "language": self.language,
            "renderer": self.renderer,
            "record_link": self.record_link,
            "recipient": self.recipient.to_dict(),
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Artifact":
        return cls(
            recipient=Recipient.from_dict(d.get("recipient") or {}),
            meeting_id=str(d.get("meeting_id") or ""),
            meeting_label=str(d.get("meeting_label") or ""),
            language=str(d.get("language") or "en"),
            sections=tuple(Section.from_dict(s) for s in (d.get("sections") or ())),
            record_link=str(d.get("record_link") or ""),
            renderer=str(d.get("renderer") or ""),
            schema_version=int(d.get("schema_version") or ARTIFACT_SCHEMA_VERSION),
        )


def section(kind: str, vocabulary: Vocabulary, items: Sequence[str] = (), body: str = "") -> Section:
    """Build a section whose title comes from the vocabulary, never from the renderer."""
    return Section(kind=kind, title=vocabulary.heading(kind), items=tuple(items), body=body)


def dedupe(items: Iterable[str]) -> tuple[str, ...]:
    """Order-preserving dedupe on normalized text — the same sentence twice is one item."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item.strip().lower())
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return tuple(out)
