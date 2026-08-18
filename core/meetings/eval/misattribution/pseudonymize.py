"""Consistent pseudonymization of participant names in a transcript.

The judge must never see a real name: roster names AND every in-text mention of
them (including ASR / DOM misspellings) are rewritten to stable pseudonyms
``P1``, ``P2``, ... The name map is written to a caller-chosen path that MUST
stay outside the repository — nothing downstream of this module carries a real
name, so judge output, scorer verdicts and the fixture manifest are all
content-free with respect to identity.

Matching is deliberately generous: a vocative is only useful if we catch
"thanks, Dmtiry" as well as "thanks, Dmitry". Over-replacement is the safe
direction of error (it can only remove identity, never leak it).
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# Tokens that are names in a roster but far too generic to fuzzy-match in text.
_STOPWORDS = {
    "the", "and", "bot", "notetaker", "note", "taker", "recorder", "meeting",
    "assistant", "ai", "guest", "user", "unknown", "speaker", "unverified",
}

# Frequent English words the FUZZY path must never rewrite. Exact alias hits
# still apply (a participant really named "Will" collides with the modal verb,
# and there is no way around that), but a near-miss must not eat ordinary
# vocabulary: sweep runs showed "will", "pay" and "present" being replaced by
# pseudonyms, which corrupts the judge's input for no gain.
_COMMON = {
    "will", "well", "want", "were", "what", "when", "with", "that", "this",
    "they", "them", "then", "than", "there", "these", "those", "here", "have",
    "has", "had", "your", "yours", "you", "our", "ours", "just", "like",
    "make", "made", "more", "most", "much", "many", "some", "such", "same",
    "said", "say", "says", "see", "seen", "take", "talk", "tell", "time",
    "thing", "think", "come", "call", "case", "each", "even", "ever", "give",
    "good", "great", "know", "look", "mean", "need", "next", "part", "pay",
    "present", "play", "back", "been", "being", "does", "done", "down", "from",
    "into", "over", "only", "also", "already", "another", "about", "after",
    "again", "against", "because", "before", "between", "both", "very", "week",
    "work", "would", "could", "should", "sure", "still", "start", "stuff",
    "team", "thanks", "thank", "yeah", "okay", "right", "left", "real", "really",
}

_FUZZY_RATIO = 0.82
_MIN_FUZZY_LEN = 4


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


@dataclass
class NameMap:
    """Bidirectional roster-name <-> pseudonym map plus the alias index."""

    to_pseudo: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)  # normalized alias -> pseudonym

    @property
    def pseudonyms(self) -> list[str]:
        seen: list[str] = []
        for p in self.to_pseudo.values():
            if p not in seen:
                seen.append(p)
        return seen

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump({"to_pseudo": self.to_pseudo, "aliases": self.aliases}, fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "NameMap":
        with open(path) as fh:
            d = json.load(fh)
        return cls(to_pseudo=d["to_pseudo"], aliases=d["aliases"])


def _same_identity(a: str, b: str) -> bool:
    """Are two roster spellings the same human?

    Rosters mix sources — DOM label, calendar attendee, email local-part — so
    one person arrives as "Dmtiry Grankin", "dmitry" and "Dmitry Grankin" in the
    same meeting. Splitting them across pseudonyms destroys the vocative test
    silently: the judge flags P4 while the label says P1 and the scorer sees no
    contradiction. Merging is therefore the safe direction of error here.
    """
    if a == b:
        return True
    # Transposition typo ("dmitry" / "dmtiry"): same letters, same length.
    if len(a) >= 5 and len(a) == len(b) and sorted(a) == sorted(b):
        return True
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
        return True
    # Given-name variant: "alex" / "alexander", "matt" / "matthew".
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def build_name_map(roster: Iterable[str]) -> NameMap:
    """Assign P1..PN in first-seen order and index every alias of each name.

    Roster spellings that denote the same human collapse onto one pseudonym.
    """
    nm = NameMap()
    for name in roster:
        name = (name or "").strip()
        if not name or name in nm.to_pseudo:
            continue
        aliases = _aliases_of(name)
        pseudo = None
        for alias in aliases:
            for known, kp in nm.aliases.items():
                if _same_identity(alias, known):
                    pseudo = kp
                    break
            if pseudo:
                break
        if pseudo is None:
            pseudo = f"P{len(nm.pseudonyms) + 1}"
        nm.to_pseudo[name] = pseudo
        for alias in aliases:
            # First writer wins: an ambiguous shared first name stays with the
            # participant who was listed first rather than flip-flopping.
            nm.aliases.setdefault(alias, pseudo)
    return nm


def _aliases_of(name: str) -> list[str]:
    out = {_norm(name)}
    parts = [p for p in re.split(r"[\s._-]+", name) if p]
    for p in parts:
        if len(p) >= 3 and _norm(p) not in _STOPWORDS:
            out.add(_norm(p))
    if len(parts) >= 2:
        out.add(_norm(" ".join(parts[:2])))
    return sorted(a for a in out if a)


_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")


def redact(text: str, nm: NameMap) -> str:
    """Replace every mention of a roster name (exact, alias, or near-miss)."""
    if not text:
        return text

    # Longest multi-word aliases first so "dmitry grankin" wins over "dmitry".
    multi = sorted((a for a in nm.aliases if " " in a), key=len, reverse=True)
    for alias in multi:
        text = re.sub(re.escape(alias), nm.aliases[alias], text, flags=re.IGNORECASE)

    single = {a: p for a, p in nm.aliases.items() if " " not in a}

    def sub_word(m: re.Match) -> str:
        w = m.group(0)
        n = _norm(w)
        if n in single:
            return single[n]
        if len(n) >= _MIN_FUZZY_LEN and n not in _STOPWORDS and n not in _COMMON:
            for alias, pseudo in single.items():
                # "Alexander" for roster "Alex" — a longer form of the same name.
                if len(alias) >= 4 and n.startswith(alias) and len(n) > len(alias):
                    return pseudo
            best, best_r = None, 0.0
            for alias, pseudo in single.items():
                if len(alias) < 5 or abs(len(alias) - len(n)) > 2:
                    continue
                r = difflib.SequenceMatcher(None, alias, n).ratio()
                if r > best_r:
                    best, best_r = pseudo, r
            if best and best_r >= _FUZZY_RATIO:
                return best
        return w

    return _WORD.sub(sub_word, text)


def pseudonym_for(label: str, nm: NameMap) -> str | None:
    """Map a rendered speaker label to its pseudonym (None when off-roster)."""
    if not label:
        return None
    if label in nm.to_pseudo:
        return nm.to_pseudo[label]
    n = _norm(label)
    if n in nm.aliases:
        return nm.aliases[n]
    best, best_r = None, 0.0
    for alias, pseudo in nm.aliases.items():
        r = difflib.SequenceMatcher(None, alias, n).ratio()
        if r > best_r:
            best, best_r = pseudo, r
    return best if best_r >= _FUZZY_RATIO else None
