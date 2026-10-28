"""sensitive.py — the identifiers a workspace page never holds (Vexa-ai/vexa#1783).

On 2026-10-13 an agent read an uploaded power of attorney carrying two passport numbers. Nothing
the agent wrote held them, but the upload itself was swept into the workspace's history, and the
person asked for it gone: *"it's unnecessary footprint in this knowledge"*. A workspace is
knowledge that is shared, synced and read by every later turn; a government identifier, a card
number or a credential in it is footprint nobody asked for.

``find`` names the KINDS present in a text — never the values — and ``redact`` replaces each match
with ``[removed]``. Both are the same rules, so what a write refuses is exactly what a forget
removes. The rules are deliberately narrow: a number alone is not an identifier, so national ids
and passports need their word nearby; card numbers must pass the Luhn check; IBANs their mod-97
check; credentials are matched by the prefixes their issuers use.
"""
from __future__ import annotations

import re
from typing import Iterable

REMOVED = "[removed]"

#: An identifier that is only an identifier with its word beside it.
_ID_WORDS = (r"passport|pass\.?\s*no|паспорт|national\s+id|id\s*card|identity\s+card|"
             r"personalausweis|reisepass|dni|nie|nif|ssn|social\s+security|"
             r"tax\s*(?:id|number)|инн|снилс|driver'?s?\s+licen[cs]e")
_ID_NEAR = re.compile(
    rf"(?i)(?:{_ID_WORDS})[^\n\d]{{0,40}}((?:[A-Z]{{0,3}}[ \-]?)?\d[\d \-]{{4,16}}\d|[A-Z]{{1,3}}\d{{5,9}})")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_KEYS = re.compile(
    r"(?:sk_(?:live|test)_[A-Za-z0-9]{10,}|whsec_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[abposr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_\-]{30,}|vx[ad]_[A-Za-z0-9_.\-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def _spans(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for m in _ID_NEAR.finditer(text):
        out.append((m.start(1), m.end(1), "a government identifier"))
    for m in _SSN.finditer(text):
        out.append((m.start(), m.end(), "a government identifier"))
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            out.append((m.start(), m.end(), "a card number"))
    for m in _IBAN.finditer(text):
        if _iban_ok(m.group()):
            out.append((m.start(), m.end(), "a bank account number"))
    for m in _KEYS.finditer(text):
        out.append((m.start(), m.end(), "a credential"))
    out.sort()
    merged: list[tuple[int, int, str]] = []
    for s, e, k in out:
        if merged and s < merged[-1][1]:
            ps, pe, pk = merged[-1]
            merged[-1] = (ps, max(pe, e), pk)
        else:
            merged.append((s, e, k))
    return merged


def find(text: str) -> dict[str, int]:
    """``{kind: count}`` of the identifiers in ``text`` — kinds only, never a value."""
    kinds: dict[str, int] = {}
    for _, _, k in _spans(text or ""):
        kinds[k] = kinds.get(k, 0) + 1
    return kinds


def find_all(texts: Iterable[str]) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for t in texts:
        for k, n in find(t).items():
            kinds[k] = kinds.get(k, 0) + n
    return kinds


def redact(text: str) -> tuple[str, int]:
    """``text`` with every identifier replaced by ``[removed]``, and how many were."""
    spans = _spans(text or "")
    if not spans:
        return text, 0
    parts, at = [], 0
    for s, e, _ in spans:
        parts.append(text[at:s])
        parts.append(REMOVED)
        at = e
    parts.append(text[at:])
    return "".join(parts), len(spans)


def refusal(kinds: dict[str, int]) -> str:
    """The one sentence a refused write answers with — naming kinds and counts, never values."""
    said = ", ".join(f"{k} ({n})" for k, n in sorted(kinds.items()))
    return (f"Not written: this would put {said} into the workspace. Vexa never keeps government "
            "ids, passport or card numbers, bank account numbers or credentials in workspace pages. "
            "Write it without them — say what the document is, not its numbers.")


def values(text: str) -> list[str]:
    """The identifier VALUES in ``text`` — for a forget to find the same item elsewhere (a title, a
    note, a transcript turn) where its keyword is not beside it. Never logged, never answered."""
    return sorted({text[s:e] for s, e, _ in _spans(text or "")}, key=len, reverse=True)


def redactor(*, literals: Iterable[str] = (), pattern: str = "", identifiers: bool = False):
    """One ``redact(text) -> (text, n)`` for a forget: the item's own values (literal,
    case-insensitive, digits matched across spaces and dashes), a pattern the person named, and —
    when asked — every identifier ``redact`` finds."""
    rxs = []
    for lit in literals:
        lit = (lit or "").strip()
        if len(lit) < 4:
            continue
        parts = [re.escape(ch) for ch in re.sub(r"[\s\-]", "", lit)]
        rxs.append(re.compile(r"[\s\-]?".join(parts), re.I))
    if pattern and pattern.strip():
        rxs.append(re.compile(re.escape(pattern.strip()), re.I))

    def run(text: str) -> tuple[str, int]:
        n = 0
        for rx in rxs:
            text, k = rx.subn(REMOVED, text)
            n += k
        if identifiers:
            text, k = redact(text)
            n += k
        return text, n
    run.active = bool(rxs) or identifiers  # type: ignore[attr-defined]
    return run


def document_text(raw: bytes, name: str = "") -> str:
    """The readable text of an attachment, for finding its identifiers: UTF-8 text as is, and the
    body of an Office Open XML file (docx/xlsx/pptx) with its tags stripped. Anything else: ''."""
    if not raw:
        return ""
    if raw[:2] == b"PK":
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                parts = [n for n in z.namelist() if n.endswith(".xml") and
                         (n.startswith("word/") or n.startswith("xl/") or n.startswith("ppt/"))]
                chunks = []
                for n in parts[:200]:
                    data = z.read(n)[:5_000_000].decode("utf-8", "ignore")
                    chunks.append(re.sub(r"<[^>]+>", " ", data))
                return re.sub(r"[ \t]+", " ", " ".join(chunks))
        except Exception:  # noqa: BLE001 — not a readable archive: no text
            return ""
    if b"\0" in raw[:8000]:
        return ""
    return raw.decode("utf-8", "ignore")
