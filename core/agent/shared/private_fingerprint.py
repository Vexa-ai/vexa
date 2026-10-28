"""private_fingerprint.py — WHAT a session read from the person's private sources, so the leak rule
on writes holds the write that carries it and lets every other write through.

The rule (``control_plane/private_taint.py`` for page writes through agent-api,
``llm/private_writes.py`` for the files a turn writes itself) used to hold EVERY write of a session
that had read any mail into a workspace other people read. On app.dev 2026-10-16 that blocked a
company page about CircleCI — nothing in it came from the mail — twice in one morning. A write is
now held when what it ADDS carries something the session read privately:

* a run of ``SHINGLE`` words read in a private source (verbatim text, a copied sentence);
* an identifier read there: an email address, a long number (phone, invoice, account: five digits
  or more), an amount with its currency, or a full name (two or more capitalised words).

What is kept is hashes, never the text: a fingerprint is a set of short digests, bounded in size.

The gate stays a gate. When the session's reads were not seen (a source whose content never reached
the fingerprint, a harness that does not report it), the caller holds every write, as before. A
paraphrase that carries no identifier and no run of words passes — that is the narrowing asked for
(friction 2, 2026-10-16), and the identifiers are what make a sentence about someone private.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

#: Words in a run that count as copied text.
SHINGLE = 6
#: Digests kept per fingerprint (a session's reads); past this, the fingerprint is "too much to
#: tell apart" and the caller holds every write, as before.
MAX_DIGESTS = 200_000

_WORD = re.compile(r"[^\W_]+(?:['’.\-@+][^\W_]+)*", re.UNICODE)
_EMAIL = re.compile(r"[\w.+\-]{1,64}@[\w\-]+(?:\.[\w\-]+)+", re.UNICODE)  # 64: RFC 5321 local part
_LONG_NUMBER = re.compile(r"(?<![\w])\+?\d[\d ()\-./]{3,}\d(?![\w])")
_AMOUNT = re.compile(
    r"(?i)(?:[$€£¥₽]\s?\d[\d,.\s]{0,40}\d|\d[\d,.\s]{0,40}\s?(?:[$€£¥₽]|eur|usd|gbp|chf|rub)\b|\d[\d,.]{0,40}\d?\s?k?\s?(?:euros?|dollars?|pounds?))")
_NAME = re.compile(r"\b[A-ZÀ-ÖØ-ÞА-ЯЁ][a-zà-öø-ÿа-яё'’\-]+(?:\s+[A-ZÀ-ÖØ-ÞА-ЯЁ][a-zà-öø-ÿа-яё'’\-]+)+\b")


def _digest(kind: str, value: str) -> str:
    return hashlib.blake2b(f"{kind}:{value}".encode(), digest_size=8).hexdigest()


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def atoms(text: str) -> set[tuple[str, str]]:
    """The identifiers in ``text`` as ``(kind, normalised value)``."""
    text = text or ""
    out: set[tuple[str, str]] = set()
    for m in _EMAIL.findall(text):
        out.add(("email", m.lower().strip(".")))
    for m in _LONG_NUMBER.findall(text):
        d = _digits(m)
        if len(d) >= 5:
            out.add(("number", d))
    for m in _AMOUNT.findall(text):
        d = _digits(m)
        if d:
            out.add(("amount", d))
    for m in _NAME.findall(text):
        out.add(("name", " ".join(m.lower().split())))
    return out


def _shingles(words: list[str]) -> Iterable[str]:
    for i in range(0, max(0, len(words) - SHINGLE + 1)):
        yield " ".join(words[i:i + SHINGLE])


class Fingerprint:
    """The digests of what one session read privately. ``complete`` is False once a read was not
    seen (or the fingerprint overflowed): the caller then holds every write."""

    def __init__(self) -> None:
        self.digests: set[str] = set()
        self.complete = True

    def add(self, text: Optional[str]) -> None:
        if text is None:
            self.complete = False
            return
        for kind, value in atoms(text):
            self.digests.add(_digest(kind, value))
        for s in _shingles(_words(text)):
            self.digests.add(_digest("run", s))
        if len(self.digests) > MAX_DIGESTS:
            self.digests.clear()
            self.complete = False

    def add_digests(self, digests: Iterable[str], complete: bool = True) -> None:
        self.digests |= set(digests)
        self.complete = self.complete and bool(complete)
        if len(self.digests) > MAX_DIGESTS:
            self.digests.clear()
            self.complete = False

    def carried(self, written: str, before: str = "") -> list[str]:
        """What of ``written`` (minus what ``before`` already said) came from these reads: the
        identifiers, then the first words of each copied run. Empty when nothing did."""
        old_atoms = atoms(before or "")
        old_runs = set(_shingles(_words(before or "")))
        found: list[str] = []
        for kind, value in sorted(atoms(written or "")):
            if (kind, value) in old_atoms:
                continue
            if _digest(kind, value) in self.digests:
                found.append(value)
        for s in _shingles(_words(written or "")):
            if s in old_runs:
                continue
            if _digest("run", s) in self.digests:
                found.append(s)
        out: list[str] = []
        for f in found:
            if f not in out:
                out.append(f)
        return out


def digests_of(text: str) -> list[str]:
    """The digests of one private read (what a harness adapter hands the worker's guard instead
    of the text)."""
    fp = Fingerprint()
    fp.add(text)
    return sorted(fp.digests)
