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
#: How long a counter whose first report was never marked admitted (its process died between the
#: upsert and `admit()`) is treated as in flight. Admission takes milliseconds.
STALE_AFTER_S = 60.0
TEXT_KEY_MAX = 200

_LABELLED_REASON = re.compile(r"reason[\"'`]?\s*[:=]\s*[\"'`]?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)")
_KNOWN = re.compile(r"\b(" + "|".join(re.escape(r) for r in KNOWN_REASONS) + r")\b")
_I = re.IGNORECASE
#: Applied IN ORDER to the ORIGINAL text (case kept, so a mixed-case handle is still recognisable),
#: each match replaced by a space. Everything here is an identifier of ONE occurrence — two failures
#: that differ only in these are the same failure.
_STRIP = [
    re.compile(r"https?://\S+", _I),                                        # urls
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                            # emails
    re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?", _I),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", _I),  # uuids
    re.compile(r"\b[a-z]{1,12}_(?=[0-9a-z]*\d)[0-9a-z]{4,}\b", _I),           # fr_…, prefixed ids
    re.compile(r"\b[a-z]{1,12}_[0-9a-f]{6,}\b", _I),                          # fr_<hex> without a digit
    re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{6,}\b", _I),                      # hex runs with a digit
    re.compile(r"\"[^\"\n]{0,80}\"|'[^'\n]{0,80}'|`[^`\n]{0,80}`"),          # quoted handles
]
#: `jti=…`, `message_id: …`, `token=…` — the LABEL is kept (it says what kind of thing failed), the
#: value is dropped. The label must END in one of these as a whole segment (`message_id`, not
#: `invalid`), and `code`/`status` are deliberately absent: an error code is the failure, not its id.
_LABELLED_ID = re.compile(
    r"\b((?:[a-z0-9]+_)*(?:id|ids|jti|token|handle|key|nonce|cursor|sid))(\s*[=:]\s*)"
    r"[\"'`]?[^\s\"'`,;)\]}]+", _I)
#: An opaque handle: 16+ url-safe base64 characters (a 22-char token, a message id) that is not a
#: word — it carries a digit, or mixes upper and lower case. `human_session_required` is neither.
_HANDLE = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{16,}(?![\w-])")
_DIGITS = re.compile(r"\d+")
_NON_WORD = re.compile(r"[^a-z_]+")


def _is_handle(tok: str) -> bool:
    return (any(c.isdigit() for c in tok)
            or (any(c.isupper() for c in tok) and any(c.islower() for c in tok)))


def normalise(text: str) -> str:
    """`text` with every per-occurrence identifier removed, case and punctuation collapsed."""
    t = _LABELLED_ID.sub(lambda m: m.group(1) + m.group(2), text or "")
    for rx in _STRIP:
        t = rx.sub(" ", t)
    t = _HANDLE.sub(lambda m: " " if _is_handle(m.group(0)) else m.group(0), t)
    t = _DIGITS.sub(" ", t).lower()
    return " ".join(_NON_WORD.sub(" ", t).split())[:TEXT_KEY_MAX]


def reason_key(what_happened: str, what_i_tried: str = "") -> str:
    """The reason part of the dedup key. A refusal token wins; otherwise normalised text."""
    low = (what_happened or "").lower()
    m = _LABELLED_REASON.search(low) or _KNOWN.search(low)
    if m:
        return "reason:" + m.group(1)
    return "text:" + normalise(what_happened if low.strip() else (what_i_tried or ""))


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
      under the returned id (which is the `friction_id` it passed in) and then calls
      `mark_admitted`.
    * `occurrences > 1` — a repeat; the returned id is the first report's, and the caller admits
      nothing.

    One atomic upsert decides first-vs-repeat, so concurrent first reports admit exactly one
    reaction (`test_friction_dedup_postgres.py::test_simultaneous_first_reports_admit_exactly_one_reaction_on_postgres`).

    A counter whose reaction no longer exists (an operator deleted that subject's rows) is restarted
    under the new id rather than counting repeats against a report nobody can read. A counter whose
    first report is NOT YET MARKED ADMITTED is left alone while it is younger than `STALE_AFTER_S`:
    that first report may still be between this upsert and its own `admit()`, and restarting it
    then would admit a second reaction for one edge (found on real Postgres with eight simultaneous
    reports). The restart is a compare-and-swap on the old id, so two concurrent restarts admit one
    reaction, not two."""
    rows = db.execute(
        """INSERT INTO friction_occurrence (dedup_key, friction_id, uid, occurrences,
                                            first_seen, last_seen, admitted)
           VALUES (:k, :fid, :uid, 1, :now, :now, 0)
           ON CONFLICT (dedup_key) DO UPDATE
              SET occurrences = friction_occurrence.occurrences + 1, last_seen = :now
           RETURNING friction_id, occurrences, first_seen, admitted""",
        {"k": key, "fid": friction_id, "uid": uid, "now": now})
    if not rows:
        return friction_id, 1
    first_id, count = rows[0][0], int(rows[0][1])
    first_seen, admitted = float(rows[0][2] or now), bool(rows[0][3])
    in_flight = not admitted and now - first_seen < STALE_AFTER_S
    if count > 1 and not in_flight and not _reaction_exists(db, first_id):
        swapped = db.execute(
            """UPDATE friction_occurrence SET friction_id = :fid, occurrences = 1,
                      first_seen = :now, last_seen = :now, admitted = 0
               WHERE dedup_key = :k AND friction_id = :old
               RETURNING friction_id""",
            {"k": key, "fid": friction_id, "old": first_id, "now": now})
        if swapped:
            return friction_id, 1
        again = db.execute("SELECT friction_id, occurrences FROM friction_occurrence "
                           "WHERE dedup_key = :k", {"k": key})
        if again:
            return again[0][0], int(again[0][1])
    return first_id, count


def mark_admitted(db, *, key: str, friction_id: str) -> None:
    """The first report's reaction exists now: from here a missing reaction means deleted."""
    db.execute("UPDATE friction_occurrence SET admitted = 1 "
               "WHERE dedup_key = :k AND friction_id = :fid", {"k": key, "fid": friction_id})


def forget(db, *, key: str) -> None:
    """Drop the counter for `key` — used when the first report was admitted into no flow, so the
    next report tries to admit again instead of being counted against a row that does not exist."""
    db.execute("DELETE FROM friction_occurrence WHERE dedup_key = :k", {"k": key})

