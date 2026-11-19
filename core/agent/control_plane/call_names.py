"""call_names.py — A CALL IS NAMED AFTER WHO OR WHERE, then renamed by its topic (founder validation O1,
2026-10-18, felt on the lab mock: "# Vexa meeting · Personal" four times over said neither who nor what).

WHO OR WHERE. A call in a room of two is "Call with Megan" (the other person's first name, as the
caller reads them); a call in a named room is "#pilot call". A call in a private chat has neither and
keeps the meeting rule's own name (`shared.display_name`): nothing is written for it.

THE TOPIC. Once the call has a transcript worth naming, the cheap model names what it is about in a few
words and the call becomes "#pilot · ADFS test window" / "Call with Megan · ADFS test window". The row
records what it was called before (`call_renamed_from`), so the list can say "renamed". Once per call;
a name the person typed is never replaced (the title no longer starts with the base we wrote).

Both writes go through meeting-api's owner-scoped annotate as the call's OWNER (the meeting is theirs):
`title` plus merge-only metadata. Nothing here is a new record: the Meeting row is the record.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger("agent_api.call_names")

#: metadata keys on the meeting row (merge-only annotate)
NAMED_KEY = "call_named"              # the who-or-where name we wrote
RENAMED_FROM_KEY = "call_renamed_from"  # set when the topic renamed it
KIND_KEY = "call_kind"                # "person" | "room"
WITH_KEY = "call_with"                # [{subject, name}] — both people of a room of two
#: a transcript shorter than this has no topic yet
MIN_TRANSCRIPT_CHARS = 240
TOPIC_WORDS = 5
#: one rename pass per owner at most this often, at most this many calls per pass
MIN_GAP_S = 20.0
PER_PASS = 2

TOPIC_SYSTEM = (
    "You name what a call is about, for a list of calls. Reply with ONE JSON object and nothing else: "
    "{\"topic\": \"...\"}. At most five plain words, in the language spoken, naming the subject — no "
    "quotes, no emoji, no trailing punctuation, no people's names, no words like Call, Meeting, Sync or "
    "Discussion, never a date. If there is no clear subject yet, reply {\"topic\": \"\"}."
)


_PLACEHOLDER = re.compile(r"(?i)^(new chat|untitled( chat)?|chat|meeting|call|vexa meeting|room call)$")
_RAWISH = re.compile(r"(?i)\b(?:pchat|meet|call|sess|session|flow|scaffold)-[\w-]{4,}\b|^s-[0-9a-f]{8,}$|https?://")


def first_name(name: str) -> str:
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n:
        return ""
    if "@" in n and " " not in n:
        n = n.split("@", 1)[0]
    return n.split(" ")[0]


def person_call_name(name: str) -> str:
    f = first_name(name)
    return f"Call with {f}" if f else "Call"


def room_call_name(title: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip().lstrip("#").strip()
    return f"#{t} call" if t else "Room call"


def renamed(base: str, topic: str) -> str:
    """'#pilot call' + 'ADFS test window' → '#pilot · ADFS test window'; 'Call with Megan' → 'Call with
    Megan · ADFS test window'."""
    b = str(base or "").strip()
    t = str(topic or "").strip()
    if not t:
        return b
    if b.startswith("#") and b.endswith(" call"):
        b = b[: -len(" call")]
    return f"{b} · {t}" if b else t


def clean_topic(text) -> str:
    t = re.sub(r"[*_`#>\"“”‘’«»{}\[\]]", "", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip().strip(" .,:;!?-—")
    words = [w for w in t.split(" ") if w][:TOPIC_WORDS]
    out = " ".join(words).strip(" .,:;!?-—")
    if not out or re.fullmatch(r"(?i)(call|meeting|sync|discussion|chat|untitled|none|n/?a)", out):
        return ""
    if re.search(r"\d{4}-\d{2}-\d{2}|/|\b(?:call|meet|sess)-[\w-]{4,}\b", out, re.I):
        return ""
    return out[:1].upper() + out[1:]


def base_for(store, session_id: str, caller: str, *, is_pair: Callable[[str], bool],
             name_of: Callable[[str], str]) -> Optional[tuple[str, dict]]:
    """The who-or-where name of a call held in ``session_id`` and the metadata that goes with it, or
    None for a session that is not a room (a private chat names its call by the meeting rule)."""
    room = store.room(session_id)
    if not room:
        # A PRIVATE CHAT'S CALL is named after the chat when the chat has a real name ("Thinking out
        # loud"); a placeholder, an id or nothing leaves the meeting rule's own name.
        title = str((store.get(session_id) or {}).get("title") or "").strip()
        if not title or title == session_id or _PLACEHOLDER.match(title) or _RAWISH.search(title):
            return None
        return title[:80], {KIND_KEY: "chat"}
    ws = str(room.get("workspace") or "")
    if ws and is_pair(ws):
        others = [h for h in store.humans(session_id) if str(h) != str(caller)]
        if not others:
            return None
        other = str(others[0])
        oname = name_of(other) or other
        return person_call_name(oname), {
            KIND_KEY: "person",
            WITH_KEY: [{"subject": str(caller), "name": name_of(str(caller)) or str(caller)},
                       {"subject": other, "name": oname}]}
    row = store.get(session_id) or {}
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    return room_call_name(title), {KIND_KEY: "room", "call_where": title}


def name_call(titler, owner: str, meeting_id, base: tuple[str, dict]) -> bool:
    """Write the who-or-where name onto the meeting row. Never raises."""
    if titler is None or not meeting_id:
        return False
    name, meta = base
    try:
        return bool(titler(str(owner), meeting_id, name, {**meta, NAMED_KEY: name, RENAMED_FROM_KEY: None}))
    except Exception as exc:  # noqa: BLE001 — the call is started; its name is furniture
        logger.warning("naming call meeting %s failed (%s)", meeting_id, type(exc).__name__)
        return False


def topic_for(transcript: str, complete: Optional[Callable[[str, str], Optional[dict]]]) -> str:
    if complete is None or len(str(transcript or "").strip()) < MIN_TRANSCRIPT_CHARS:
        return ""
    try:
        out = complete(TOPIC_SYSTEM, str(transcript)[-3000:])
    except Exception:  # noqa: BLE001 — a name is furniture
        return ""
    return clean_topic((out or {}).get("topic"))


# ── the topic pass ──────────────────────────────────────────────────────────────────────────────
_renamed: set = set()
_attempts: dict = {}
_last_run: dict = {}
_lock = threading.Lock()
#: a call whose transcript has not yielded a topic after this many asks is left with its name
MAX_ATTEMPTS = 3


def reset() -> None:
    with _lock:
        _renamed.clear()
        _attempts.clear()
        _last_run.clear()


def renamable(row: Optional[dict]) -> Optional[str]:
    """The who-or-where name to rename from, when the meeting row still carries exactly the name we
    wrote and has not been renamed; else None (a person's own title is never replaced)."""
    data = (row or {}).get("data") or {}
    meta = data.get("metadata") or {}
    named = str(meta.get(NAMED_KEY) or "").strip()
    if not named or meta.get(RENAMED_FROM_KEY) or str(data.get("title") or "").strip() != named:
        return None
    return named


def rename_pass(owner: str, calls: Iterable[dict], *, row_of: Callable[[str], Optional[dict]],
                transcript_of: Callable[[str], str], complete, titler, now: Optional[float] = None,
                run: Callable[[Callable[[], None]], None] = lambda f: threading.Thread(target=f, daemon=True).start()) -> list:
    """Rename up to ``PER_PASS`` of ``owner``'s live calls by their topic, in the background. ``calls``
    are session-store call rows (the owner's, live). Returns the meeting ids picked."""
    now = time.time() if now is None else now
    if complete is None or titler is None:
        return []
    with _lock:
        if now - _last_run.get(owner, 0.0) < MIN_GAP_S:
            return []
        picked = [c for c in calls if c.get("meeting_id") and str(c["meeting_id"]) not in _renamed
                  and _attempts.get(str(c["meeting_id"]), 0) < MAX_ATTEMPTS][:PER_PASS]
        if not picked:
            return []
        _last_run[owner] = now
        for c in picked:
            _renamed.add(str(c["meeting_id"]))

    def work():
        for c in picked:
            mid = str(c["meeting_id"])
            done = False
            try:
                named = renamable(row_of(mid))
                if named is None:
                    done = True                    # renamed already, or the person named it
                    continue
                text = transcript_of(mid)
                if len(str(text or "").strip()) < MIN_TRANSCRIPT_CHARS:
                    continue                       # nothing to name yet; no model call spent
                with _lock:
                    _attempts[mid] = _attempts.get(mid, 0) + 1
                topic = topic_for(text, complete)
                if topic:
                    done = bool(titler(str(owner), int(mid), renamed(named, topic), {RENAMED_FROM_KEY: named}))
            except Exception:  # noqa: BLE001
                logger.warning("renaming call meeting %s failed", mid)
            finally:
                if not done:
                    with _lock:
                        _renamed.discard(mid)      # try again on a later pass

    run(work)
    return [str(c["meeting_id"]) for c in picked]
