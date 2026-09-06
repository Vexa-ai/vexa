"""meeting_mint.py — THE MEETING'S PAGE EXISTS FROM THE MOMENT THE MEETING DOES.

Founder, 2026-09-06, in a live Google Meet started FROM a chat, the transcript pinned beside it and
no document anywhere on the right: *"where is it?"* (Vexa-ai/vexa#1601).

The page was written by `core/flows`' `drop_to_attendees` when the call ENDED, so a live room had a
transcript and nothing to embed it in — #1598 had just made the meeting doc the one page a meeting
shows, and during the meeting that page did not exist yet. Its own report said what was missing:
*"minting it at bot-send time needs the flow's `_note_path` recipe reachable from agent-api (or the
row to carry the path)"*. This module is the second of those, and the row carrying the path is what
makes it safe.

THE RULE: **the meeting doc exists from the moment the meeting exists for this person.** A chat that
sends a bot (#1597's binding) mints the page on the sender's desk in the same turn, and a meeting
that arrives from the mailbox mints it at row creation — both through here.

── WHY THIS COMPOSES A PATH AT ALL, WHEN `meeting_note` REFUSES TO ────────────────────────────────

`meeting_note` reads the desk and *"COMPOSES NOTHING"*, on purpose: a resolver that re-implemented
the writer's recipe would be a third spelling of one path, agreeing with the writer only until one
of them changed. Nothing about that changes — it is still the reader.

This is the WRITER, and a writer has to name the file it creates. What stops the second spelling is
not that only one function may compose, but that only the FIRST composition counts: whoever mints
records the path on the meeting row (`meeting_note.NOTE_PATH_KEY`), and every later reader — this
module on a second bind, `/api/meeting/note`, and `core/flows`' `_note_path` when it drops the
report — READS IT BACK instead of composing its own. The flow's recipe survives untouched as the
fallback for a meeting nobody minted, which is every meeting that predates this.

THE STAMP IS UTC HERE, and the flow's is the ORGANISER'S ZONE. That is a real difference and it is
harmless for exactly one reason: it can only ever be visible in a filename nobody else composes.
agent-api holds no timezone for a subject (the schedule digest gets one from the browser, per
request, which is not the same fact), and inventing one would be worse than naming the zone we are
actually in. The flow's own no-timezone fallback is UTC too.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import urllib.request
from pathlib import Path

from control_plane import meeting_note

logger = logging.getLogger("agent_api.meeting_mint")

#: The shape of a meeting page's name on a desk. `core/flows`' `_note_path` composes the same one
#: (`kg/entities/meeting/<stamp>-<slug>.md`) and is pinned to it by its own test; the two agree by
#: shape, never by result, because only one of them ever gets to compose for a given meeting.
NOTE_PATH = meeting_note.MEETING_DIR + "/{stamp}-{slug}.md"

#: `%Y-%m-%d-%H%M`, so two occurrences of a recurring meeting on ONE day are still two files. Same
#: reason `core/flows`' `_meeting_stamp` renders it — a filename that collides silently overwrites
#: the morning's record with the afternoon's, and nothing fails.
_STAMP = "%Y-%m-%d-%H%M"

_NOT_WORD = re.compile(r"[^a-z0-9]+")


def slug(text: str, cap: int = 60) -> str:
    """A meeting title as a filename fragment — lowercase, one `-` per run of anything else.

    An ALLOW-list, exactly as the flow's `_slug` is and for its reason: a title is attacker-adjacent
    text off a calendar invite anybody in the room can edit, so `/`, `..`, a leading dot and every
    other separator are gone by construction rather than by a blacklist somebody has to keep
    complete."""
    out = _NOT_WORD.sub("-", str(text or "").lower()).strip("-")[:cap].rstrip("-")
    return out or "meeting"


def _epoch(value) -> "float | None":
    """A meeting row's own moment, as seconds — from an ISO timestamp or a number, else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return float(s) if re.fullmatch(r"\d+(\.\d+)?", s) else \
            datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def moment(row) -> datetime.datetime:
    """WHEN this meeting is, in UTC — the row's scheduled time, else when it started, else when the
    row was made, else now. The same fallback chain `core/flows`' `_meeting_stamp` walks, minus the
    organiser's zone, which agent-api does not hold (see the module header)."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    for v in ((data or {}).get("scheduled_at"), r.get("start_time"), r.get("created_at")):
        at = _epoch(v)
        if at is not None:
            return datetime.datetime.fromtimestamp(at, datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def title_of(row) -> str:
    """What to call this meeting on its own page.

    The row's title when it has one; otherwise the PLATFORM, which is the tier `_mail_title` in the
    flow settles on for an ad hoc meeting nobody named — never a bare native id, which reads as
    noise rather than as a name. A chat that sends a bot to a Meet link names nothing, so this is
    the ordinary case here rather than the edge one."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    given = str((data or {}).get("title") or "").strip()
    if given:
        return given
    platform = str(r.get("platform") or (data or {}).get("platform") or "").strip().lower()
    if platform and platform != "unknown":
        return platform.replace("_", " ").title() + " meeting"
    return "Meeting"


def compose(row) -> str:
    """Where this meeting's page goes when nothing has recorded a path for it yet.

    The slug comes off the TITLE when there is one and off the platform + native id when there is
    not — two untitled ad hoc meetings in one minute would otherwise land on one filename, and the
    second would find the first's page already there and quietly adopt it."""
    facts = meeting_note.row_facts(row)
    name = str((row.get("data") or {}).get("title") or "").strip() if isinstance(row, dict) else ""
    if not name:
        name = (title_of(row) + " " + facts["native"]).strip()
    return NOTE_PATH.format(stamp=moment(row).strftime(_STAMP), slug=slug(name))


def mint(workspaces_root, subject: str, row, *, path: str = "", record=None) -> dict:
    """This meeting's page on THIS person's desk — `{"path", "created"}`.

    IDEMPOTENT, AND THAT IS THE WHOLE SAFETY PROPERTY. A page that is already there is returned
    untouched: by the second send in a conversation, by a reload, by the mailbox flow arriving after
    the chat did. The page is written by three hands after this (the person, their Expand, and the
    flow's report), so a mint that "refreshed" it would delete somebody's writing at the moment the
    room got busy.

    `path` is the caller's proposal — the flow naming a page it already knows about — and it is
    honoured only if it is one of ours (`meeting_note.is_note_path`). A recorded path wins over a
    composed one; a proposed path wins over both, because the proposer is the writer.

    `record` is the hop that puts the path on the meeting row, and it fires only when the row does
    not carry one yet: the FIRST mint decides, and nothing later moves a page out from under a
    reader who has it open.

    Returns `{"path": None}` for a row with no id — there is no meeting to mint a page for, and a
    page bound to nothing is a box with no room behind it."""
    from shared import meeting_doc

    facts = meeting_note.row_facts(row)
    if not facts["id"]:
        return {"path": None, "created": False}
    recorded = meeting_note.recorded_path(row)
    proposed = str(path or "").strip()
    rel = next((p for p in (proposed, recorded) if p and meeting_note.is_note_path(p)),
               "") or compose(row)
    desk = Path(workspaces_root) / str(subject)
    f = desk / rel
    created = False
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        # THE SHAPE IS `shared/meeting_doc`'s, not this module's. It owns the slot, the regions and
        # the cursor — the three things Expand and the flow's report both write between — so a
        # second composition here is how a minted page and an expanded one would stop agreeing.
        f.write_text(meeting_doc.scaffold(meeting=facts["id"], title=title_of(row),
                                          native=facts["native"], date=facts["day"]),
                     encoding="utf-8")
        created = True
        _commit(desk, rel)
    if record is not None and not recorded:
        try:
            record(str(subject), facts["id"], rel)
        except Exception:  # noqa: BLE001 — the page is on the desk; the record is bookkeeping
            logger.exception("recording note_path=%s on meeting %s failed", rel, facts["id"])
    return {"path": rel, "created": created}


def _commit(desk: Path, rel: str) -> None:
    """One commit, BY PATHSPEC — the same writer every other desk write goes through.

    `git commit` commits THE INDEX, so a bare `add` + `commit` here would sweep in whatever the
    person's own worker had staged in the same repo mid-turn and file it under this message. A
    desk with no repo (a workspace that was never initialised) is a no-op, not a failure."""
    from workspaces.shared import entities as entities_mod

    try:
        entities_mod.commit_entity(desk, [rel], subject_path=rel, created=True)
    except Exception:  # noqa: BLE001 — the file is written; an uncommitted page still opens
        logger.exception("committing the minted meeting page %s failed", rel)


def http_recorder(meeting_api_url: str):
    """The default recorder: `POST {meeting_api_url}/meetings/{id}/annotate` with the caller's
    `X-User-Id`. Returns `(subject, meeting_id, path) -> bool`; injectable for L2 tests, the same
    seam style as `_http_meeting_owner_lookup` one file over.

    ANNOTATE RATHER THAN PATCH, deliberately. PATCH edits the INSTRUCTIONS for a meeting and is
    refused once the FSM owns the row — which is the entire live meeting, i.e. exactly when this
    fires. Annotations are owner-scoped, merge-only and legal in any status, so a caller can only
    ever affect the key it names and no writer can destroy what another one put there.

    NEVER RAISES. A row that could not be annotated costs the flow its read-back, and the flow
    falls back to composing the path as it does today — one degraded meeting, not a failed turn."""
    base = (meeting_api_url or "").rstrip("/")

    def _record(subject: str, meeting_id, path: str) -> bool:
        if not base or not subject or not str(meeting_id).isdigit():
            return False
        body = json.dumps({"metadata": {meeting_note.NOTE_PATH_KEY: str(path)}}).encode()
        req = urllib.request.Request(
            f"{base}/meetings/{int(meeting_id)}/annotate", data=body, method="POST",
            headers={"X-User-Id": str(subject), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= int(resp.status) < 300
        except Exception as exc:  # noqa: BLE001 — see the docstring
            logger.warning("recording note_path on meeting %s failed (%s) — the flow will compose "
                           "the path instead", meeting_id, exc)
            return False

    return _record
