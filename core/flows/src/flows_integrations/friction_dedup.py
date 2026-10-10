"""friction_dedup — fold a flood of identical friction reports into one row with a count.

THE DEFECT THIS EXISTS FOR. A reporter stuck in a loop files the same report on every pass: a
scheduled run whose tool refused it on each tick filed one `friction.reported` per tick, dozens of
identical rows that buried every other report and told the fixer nothing the first one had not.
The recurrence IS signal — so it is kept, as a number on the first report, not as N copies of it.

THE KEY is `(uid, tool, normalised reason, UTC hour bucket)`:

  * `uid` — the report's subject as the route resolved it from the caller's credential. One
    person's report never increments another's (`test_a_different_uid_never_increments_another_s_report`).
  * `tool` — as sent, stripped and lowercased.
  * reason — the refusal REASON TOKEN when `what_happened` carries one (`reason: human_session_required`,
    or a known token anywhere in the text), else `what_happened` normalised: ids, hex, digits,
    timestamps, emails, URLs and quoted handles removed, punctuation and case collapsed. Falls back
    to `what_i_tried` when `what_happened` is empty.
  * the UTC hour (`floor(now / 3600)`) — a repeat in the NEXT hour is a new row, so a fixer sees
    an edge that is still happening rather than one counter that grew forever.

ONE WRITER: `record_occurrence` is called only by flows-api's `POST /friction`
(`flows_api.report_friction`). The counter lives in its own table, `friction_occurrence`; the
admitted reaction's `subject_refs` are never rewritten to carry it.
"""
from __future__ import annotations

import hashlib
import json
import re

#: Refusal reason tokens matched anywhere in `what_happened` even without a `reason:` label —
#: the ones a tool returns as `{"status": "refused", "reason": ...}` on the agent surface. A token
#: that is not listed is still found when the text labels it (`reason=…`, `"reason": "…"`).
KNOWN_REASONS = (
    "human_session_required",
    "delegated_dispatch",
    "service_authority_unavailable",
    "opaque_limit",
    "schema_violation",
)

HOUR_S = 3600
TEXT_KEY_MAX = 200

_LABELLED_REASON = re.compile(r"reason[\"'`]?\s*[:=]\s*[\"'`]?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)")
_KNOWN = re.compile(r"\b(" + "|".join(re.escape(r) for r in KNOWN_REASONS) + r")\b")
_STRIP = [
    re.compile(r"https?://\S+"),                                             # urls
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                             # emails
    re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),  # uuids
    re.compile(r"\b[a-z]{1,12}_[0-9a-f]{6,}\b"),                             # fr_…, prefixed ids
    re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{6,}\b"),                         # hex runs with a digit
    re.compile(r"\"[^\"\n]{0,80}\"|'[^'\n]{0,80}'|`[^`\n]{0,80}`"),          # quoted handles
    re.compile(r"\d+"),                                                       # any remaining digits
]
_NON_WORD = re.compile(r"[^a-z_]+")


def reason_key(what_happened: str, what_i_tried: str = "") -> str:
    """The reason part of the dedup key. A refusal token wins; otherwise normalised text."""
    text = (what_happened or "").lower()
    m = _LABELLED_REASON.search(text) or _KNOWN.search(text)
    if m:
        return "reason:" + m.group(1)
    if not text.strip():
        text = (what_i_tried or "").lower()
    for rx in _STRIP:
        text = rx.sub(" ", text)
    return "text:" + " ".join(_NON_WORD.sub(" ", text).split())[:TEXT_KEY_MAX]


def hour_bucket(now: float) -> int:
    """The UTC hour `now` falls in — epoch seconds are UTC, so this is `floor(now / 3600)`."""
    return int(now // HOUR_S)


def dedup_key(*, uid: str, tool: str, what_happened: str, what_i_tried: str, now: float) -> str:
    """sha256 over the four parts, so the stored key carries no report text."""
    parts = [str(uid or ""), (tool or "").strip().lower(),
             reason_key(what_happened, what_i_tried), hour_bucket(now)]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _reaction_exists(db, friction_id: str) -> bool:
    rows = db.execute("SELECT 1 FROM reaction WHERE event_type = 'friction.reported' "
                      "AND source_event_id LIKE :p", {"p": f"friction-{friction_id}::%"})
    return bool(rows)


def record_occurrence(db, *, key: str, friction_id: str, uid: str, now: float) -> tuple[str, int]:
    """Count one report against `key`. Returns `(friction_id, occurrences)`:

    * `occurrences == 1` — this is the first report of the edge in this hour; the caller admits it
      under the returned id (which is the `friction_id` it passed in).
    * `occurrences > 1` — a repeat; the returned id is the first report's, and the caller admits
      nothing.

    One atomic upsert decides first-vs-repeat, so two concurrent firsts cannot both admit. A
    counter whose reaction no longer exists (an operator deleted that subject's rows) is restarted
    under the new id rather than counting repeats against a report nobody can read."""
    rows = db.execute(
        """INSERT INTO friction_occurrence (dedup_key, friction_id, uid, occurrences,
                                            first_seen, last_seen)
           VALUES (:k, :fid, :uid, 1, :now, :now)
           ON CONFLICT (dedup_key) DO UPDATE
              SET occurrences = friction_occurrence.occurrences + 1, last_seen = :now
           RETURNING friction_id, occurrences""",
        {"k": key, "fid": friction_id, "uid": uid, "now": now})
    first_id, count = (rows[0][0], int(rows[0][1])) if rows else (friction_id, 1)
    if count > 1 and not _reaction_exists(db, first_id):
        db.execute("""UPDATE friction_occurrence SET friction_id = :fid, occurrences = 1,
                             first_seen = :now, last_seen = :now WHERE dedup_key = :k""",
                   {"k": key, "fid": friction_id, "now": now})
        return friction_id, 1
    return first_id, count


def forget(db, *, key: str) -> None:
    """Drop the counter for `key` — used when the first report was admitted into no flow, so the
    next report tries to admit again instead of being counted against a row that does not exist."""
    db.execute("DELETE FROM friction_occurrence WHERE dedup_key = :k", {"k": key})

