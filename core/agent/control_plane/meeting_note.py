"""meeting_note.py — WHERE THIS MEETING'S REPORT LIVES ON THIS PERSON'S DESK, said by the server.

A meeting's record is written by `core/flows`' `drop_to_attendees` at
`kg/entities/meeting/<meeting-day>-<title-slug>.md` — the day in the ORGANISER's timezone, the slug
through a server-side allow-list. Neither is derivable from a browser, and deriving it anyway is
the defect this module closes: the terminal used to point its Minutes tab at
`kg/entities/meeting/<native>.md`, a second spelling of one path in a second language, and the two
never agreed. The founder opened a meeting whose 6.3 KB report had been written, mailed and dropped
an hour earlier and read *"No page here yet — it appears when the conversation (or a meeting)
writes one"* (Vexa-ai/vexa#1588).

`refs.note_path` on a SCAFFOLD already carries the path for a chat born from a mailed link — the
flow computes it once, at mint, and the client is told. This is the same answer for the chat that
was NOT born from a link: the meeting clicked in the rail, which is how a person reaches their own
meetings every day after the first.

IT COMPOSES NOTHING. It reads the desk and returns a file that IS THERE, matched against facts the
meetings domain owns — the row's id, its native id, its title. A resolver that re-implemented the
flow's recipe here would be a THIRD spelling, agreeing with the writer only until one of them
changed; `_note_path`'s own docstring is about exactly that. So `None` is a real answer and means
"nothing on this desk names this meeting" — the caller shows one document fewer, never a tab onto a
path nobody wrote.

THE MATCH IS POSITIVE EVIDENCE, in three tiers:

  0. `note_path` ON THE MEETING ROW ITSELF (Vexa-ai/vexa#1601). The page is minted now — at bot-send
     from a chat, or at row creation for a meeting that arrives from the mailbox — and whoever mints
     RECORDS the path it chose. So this tier is not a match at all: it is the writer telling the
     reader where it wrote, and the two tiers below are what answers for every meeting whose page
     predates the record. The file still has to BE THERE, which is why this is a tier of the same
     scan rather than a shortcut around it.
  1. the entity's own `meeting:` / `native:` frontmatter — the identity the writer stamps. Exact,
     and the only tier that can tell two occurrences of a RECURRING meeting apart.
  2. its `title:`, against the row's. Every report written before tier 1 existed is on somebody's
     desk already and cannot be rewritten — they are the users' own files — so they are recognised
     by the one fact they do carry. Several files with one title (a weekly) are broken apart by the
     frontmatter `date` nearest the row's own day, and a tie there takes the newest name, which is
     stable and never a coin flip.
"""
from __future__ import annotations

from pathlib import Path
import re

#: Where a meeting's record lives on a desk, relative to that desk's root. The directory is the
#: flow's (`drop_to_attendees` writes here and maintains `index.md` beside the files); this module
#: only reads it.
MEETING_DIR = "kg/entities/meeting"

#: The key a meeting ROW carries its page's path under, inside the owner-scoped `data.metadata` blob
#: the meetings domain already keeps for its owner's own annotations (Vexa-ai/vexa#1601).
#:
#: ONE SPELLING, AND THE ROW IS WHERE IT LIVES. The path is a function of a day rendered in somebody
#: else's timezone and a slug through an allow-list, so it is not a thing two services can each be
#: trusted to compose — that is the whole of #1588. Whoever MINTS the page records it here; every
#: other side READS it: this module, and `core/flows`' `_note_path` when it drops the report.
NOTE_PATH_KEY = "note_path"

#: A page name we could have minted: one path segment, `.md`, no dot-leader, no separators. The
#: recorded value arrives from a row an account's own API key can annotate, and it is used to NAME A
#: FILE ON EVERY DESK IN THE ROOM — so it is checked against the alphabet rather than trusted.
_NOTE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md\Z")

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_KV = re.compile(r"\A([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)\Z")

#: Frontmatter is the first thing in the file and it is small; a report is not. Reading the head is
#: enough to answer, and it keeps a directory of 6 KB reports off the critical path of opening a chat.
_HEAD_BYTES = 4096


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
        if value.strip()[0] == '"':
            v = v.replace('\\"', '"').replace("\\\\", "\\")
    return v.strip()


def front_matter(text: str) -> dict:
    """The `key: value` block at the top of an entity, as a flat dict of strings.

    Deliberately not a YAML parser: the frontmatter this reads is written by one function
    (`production._drop_entity`) in a shape it controls, and pulling a YAML dependency into agent-api
    to read five scalars would be a much larger surface than the thing it reads. Nested values and
    lists are left as their raw text — nothing here asks for one."""
    m = _FRONT_MATTER.match(text or "")
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if not line[:1].strip():          # indented → belongs to the key above; not ours to read
            continue
        kv = _KV.match(line.rstrip())
        if kv:
            out[kv.group(1).strip().lower()] = _unquote(kv.group(2))
    return out


def is_note_path(path) -> bool:
    """Is this one of ours — `kg/entities/meeting/<name>.md`, and not the folder's index?

    The guard is the ALPHABET, not a check somebody has to remember to run: no `/` after the folder,
    no leading dot, no `..`, nothing that can walk out of a desk."""
    p = str(path or "").strip()
    prefix = MEETING_DIR + "/"
    if not p.startswith(prefix):
        return False
    name = p[len(prefix):]
    return name != "index.md" and bool(_NOTE_NAME.match(name))


def recorded_path(row) -> str:
    """The page path THIS ROW carries, or `""` — tier 0 (see the module header).

    `""` for every meeting minted before the record existed, and that is the ordinary answer rather
    than a failure: the scan below is what has always answered for those and still does."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    meta = (data or {}).get("metadata")
    p = str((meta or {}).get(NOTE_PATH_KEY) or "").strip() if isinstance(meta, dict) else ""
    return p if is_note_path(p) else ""


def _day(value) -> str:
    """The `YYYY-MM-DD` at the front of a timestamp, or "". Never parsed into a datetime: the two
    sides are rendered in different zones by construction (see the module docstring), so a
    comparison finer than the day would be a precision this data does not have."""
    s = str(value or "").strip()
    return s[:10] if re.match(r"\A\d{4}-\d{2}-\d{2}", s) else ""


def row_facts(row) -> dict:
    """The three things a meeting row says about itself that a desk file can be matched on."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    return {
        "id": str(r.get("id") or "").strip(),
        "native": str(r.get("native_meeting_id") or "").strip(),
        "title": str((data or {}).get("title") or "").strip(),
        "day": _day((data or {}).get("scheduled_at") or r.get("start_time") or r.get("created_at")),
    }


def resolve(workspaces_root, subject: str, row) -> "str | None":
    """This meeting's report on this person's desk, as a workspace-relative path — or None.

    None is the ordinary answer for a meeting whose report has not been written yet, and the caller
    must treat it as one: it is "nothing here names this meeting", never "something failed"."""
    # TIER 0 — the row says where its page is (Vexa-ai/vexa#1601). Still conditional on the file
    # being on THIS desk: the record is one fact about the meeting, shared by everybody in the room,
    # and an attendee whose drop has not run yet has no such file. A tab onto a path nobody wrote on
    # this desk is exactly the failure this module exists to refuse.
    recorded = recorded_path(row)
    if recorded:
        try:
            if (Path(workspaces_root) / str(subject) / recorded).is_file():
                return recorded
        except OSError:
            pass
    facts = row_facts(row)
    if not (facts["id"] or facts["native"] or facts["title"]):
        return None
    folder = Path(workspaces_root) / str(subject) / MEETING_DIR
    if not folder.is_dir():
        return None
    ids = {v for v in (facts["id"], facts["native"]) if v}
    title = facts["title"].casefold()
    by_title: list[tuple[str, str]] = []      # (frontmatter day, file name)
    try:
        entries = sorted(p for p in folder.iterdir() if p.suffix == ".md")
    except OSError:
        return None
    for f in entries:
        if f.name == "index.md":
            continue
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(_HEAD_BYTES)
        except OSError:
            continue
        fm = front_matter(head)
        if str(fm.get("type") or "").strip().lower() not in ("", "meeting"):
            continue
        if ids and (str(fm.get("meeting") or "").strip() in ids
                    or str(fm.get("native") or "").strip() in ids):
            return f"{MEETING_DIR}/{f.name}"
        if title and str(fm.get("title") or "").strip().casefold() == title:
            by_title.append((_day(fm.get("date")), f.name))
    if not by_title:
        return None
    if len(by_title) > 1 and facts["day"]:
        exact = [n for d, n in by_title if d == facts["day"]]
        if exact:
            return f"{MEETING_DIR}/{sorted(exact)[-1]}"
    # No day to separate them, or none matched: the newest name wins. `<day>-<time>-<slug>` sorts
    # chronologically as a string, so "newest" here is a fact about the files rather than a guess —
    # and it is STABLE, which is the property that matters when the alternative is a coin flip.
    return f"{MEETING_DIR}/{sorted(n for _d, n in by_title)[-1]}"


def describe(workspaces_root, subject: str, row) -> dict:
    """The note, PLUS the two facts that decide whether it is the meeting's ONE page (#1598).

    `transcript` is the meeting the page's own widget slot names — non-empty means the live
    transcript renders INSIDE this document, so the room needs no separate Transcript tab. `cursor`
    is where the last Expand stopped reading.

    BOTH ARE READ OFF THE FILE, never assumed from the fact that a note exists. Every report written
    before the widget existed is on somebody's desk right now and carries neither; a client that
    inferred "there is a note, so the transcript is in it" would take the room off the screen of
    exactly the people whose meetings predate this. Absent is the honest answer and it degrades to
    the two-page room, which is what they have today.

    Never raises: a note we could not read is a note we cannot describe, and the path — which is
    what #1588 exists to answer — is still worth returning on its own."""
    from shared import meeting_doc

    path = resolve(workspaces_root, subject, row)
    out = {"path": path, "transcript": "", "cursor": ""}
    if not path:
        return out
    try:
        with (Path(workspaces_root) / str(subject) / path).open("r", encoding="utf-8",
                                                               errors="replace") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return out
    out["transcript"] = meeting_doc.slot_meeting(head)
    out["cursor"] = meeting_doc.read_cursor(head)
    return out
