"""chat_autotitle.py — a chat nobody named gets a name from what was said in it (#1783; founder
2026-10-15: the chat list read "Chat · 9:11 AM" — "this is bloated"; then "otherwise most chats will
read New chat").

WHEN. After the first exchange (a person's turn and an answer), and once more after about five turns
if the topic moved on. Lazily, when the rail is listed: the rail is read every few seconds, so a
chat is named moments after its first answer, and an old untitled chat is named the next time it is
listed — a few per listing, never a burst.

HOW. At most six plain words, in the chat's own language, by the deployment's cheapest model
(``home_model.complete_json``: the company's ``model_home`` policy, ``models_allowed``), each call
counted against the person's spend (``usage`` kind ``title``) and skipped when the budget says stop.
With no model to ask, the person's own first words are the name (``chat_label.human_head``).

NEVER. A name the person gave is never replaced (``name(..., human=False)`` refuses it atomically).
A meeting's or call's chat is named by the meeting rule (``display_name``), and a routine's chat by
its routine — neither is titled here.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

from shared import chat_label

logger = logging.getLogger(__name__)

#: How many chats one listing may title, and how often one person's rail may title at all.
PER_LISTING = 2
MIN_GAP_S = 15.0
#: Re-title once when the chat has grown this many turns past its first title.
RETITLE_AFTER = 5
MAX_WORDS = 6

SYSTEM = (
    "You name a chat for a list of chats. Reply with ONE JSON object and nothing else: "
    "{\"title\": \"...\"}. At most six words, plain, in the language the person wrote in, naming what "
    "the chat is about — no quotes, no emoji, no trailing punctuation, no words like Chat, Conversation, "
    "Help or Question, and never an id, a file path or a date."
)

#: (subject, session) → the number of turns when this chat was last titled here.
_titled: dict = {}
_inflight: set = set()
_last_run: dict = {}
_lock = threading.Lock()


def reset() -> None:
    """Tests: forget what was titled."""
    with _lock:
        _titled.clear()
        _inflight.clear()
        _last_run.clear()


_FILLER = re.compile(r"^(chat|conversation|new chat|help|question|untitled)$", re.I)
_RAW = re.compile(r"\b(?:pchat|meet|call|flow|scaffold|sess|session)-[\w-]{4,}\b|/|\d{4}-\d{2}-\d{2}", re.I)


def clean(text) -> str:
    """At most six plain words: no quotes, no markdown, no trailing punctuation; "" when what is
    left is filler, an id, a path or machinery."""
    t = chat_label.strip_markers(str(text or ""))
    t = re.sub(r"[*_`#>\"“”‘’«»]", "", t)
    t = re.sub(r"\s*[:;|]\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip().strip(" .,:;!?-—")
    words = t.split(" ")[:MAX_WORDS]
    out = " ".join(words).strip(" .,:;!?-—")
    if not out or _FILLER.match(out) or _RAW.search(out) or chat_label.is_machinery_label(out):
        return ""
    return out[:1].upper() + out[1:]


def _excerpt(turns: list, limit: int = 900) -> str:
    parts = []
    for t in turns[:6]:
        who = "Person" if t.get("role") == "user" else "Assistant"
        text = chat_label.human_head(str(t.get("text") or "")) if t.get("role") == "user" else str(t.get("text") or "")
        text = re.sub(r"\s+", " ", text).strip()[:300]
        if text:
            parts.append(f"{who}: {text}")
    return "\n".join(parts)[:limit]


def title_for(turns: list, complete: Optional[Callable[[str, str], Optional[dict]]]) -> str:
    """The chat's name from its turns: the model's answer, cleaned, else the person's first words."""
    if complete is not None:
        try:
            out = complete(SYSTEM, _excerpt(turns))
        except Exception:  # noqa: BLE001 — a name is furniture
            out = None
        name = clean((out or {}).get("title"))
        if name:
            return name
    first = next((t for t in turns if t.get("role") == "user" and chat_label.human_head(str(t.get("text") or ""))), None)
    return clean(chat_label.human_head(str(first.get("text") or ""))) if first else ""


def _token_name(row: dict) -> bool:
    label = str(row.get("label") or row.get("named_title") or "").strip()
    return bool(label) and bool(_RAW.search(label) or re.fullmatch(r"[A-Za-z0-9_-]{20,}", label))


def _wanted(row: dict, turns: int) -> bool:
    """A chat this listing should title: answered at least once, not a meeting's or a routine's, not
    named by the person — and either never named, or titled here once and grown five turns since."""
    sid = str(row.get("session") or "")
    if not sid or sid.startswith(("meet-", "dm-")) or row.get("meeting") or row.get("routine") or row.get("flow"):
        return False
    if turns < 2:
        return False
    if row.get("name_source") == "human":
        # a "name" that is a token (a join or scaffold id) was never the person's: it is untitled
        return _token_name(row)
    key = (row.get("_subject"), sid)
    label = str(row.get("label") or "").strip()
    if not label or label == sid or _RAW.search(label) or _FILLER.match(label):
        return True
    done = row.get("auto_titled_turns")            # stored with the row: survives a restart
    if done is None:
        done = _titled.get(key)
    return row.get("name_source") == "agent" and done is not None and done < RETITLE_AFTER <= turns


def schedule(subject: str, rows: list, *, turns_of: Callable[[str], list], name: Callable[..., bool],
             complete: Optional[Callable[[str, str], Optional[dict]]] = None, now: Optional[float] = None,
             run: Callable[[Callable[[], None]], None] = lambda f: threading.Thread(target=f, daemon=True).start()) -> list:
    """Title up to ``PER_LISTING`` of this person's chats in the background; returns the sessions
    picked. ``turns_of(session)`` reads a chat's turns, ``name(session, title)`` stores an agent-source
    name, ``complete(system, user)`` is the cheap model call (or None)."""
    now = time.time() if now is None else now
    with _lock:
        if now - _last_run.get(subject, 0.0) < MIN_GAP_S:
            return []
        _last_run[subject] = now
    picked = []
    for row in rows:
        if len(picked) >= PER_LISTING:
            break
        sid = str(row.get("session") or "")
        if (subject, sid) in _inflight:
            continue
        try:
            turns = turns_of(sid) or []
        except Exception:  # noqa: BLE001
            continue
        if not _wanted({**row, "_subject": subject}, len(turns)):
            continue
        with _lock:
            _inflight.add((subject, sid))
        picked.append(sid)

        def job(sid=sid, turns=turns, row=row):
            try:
                title = title_for(turns, complete)
                # replacing a token someone's client stored as a "human" name takes the human slot
                if title and name(sid, title, len(turns), _token_name(row)):
                    with _lock:
                        _titled[(subject, sid)] = len(turns)
            except Exception:  # noqa: BLE001
                logger.warning("auto-title failed for a chat of subject=%s", subject)
            finally:
                with _lock:
                    _inflight.discard((subject, sid))
        run(job)
    return picked
