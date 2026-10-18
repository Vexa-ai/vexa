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

from workspaces.shared import workspace_paths as wpaths

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
#: the workspace the recorded page is in (#1783): "" is the person's home; a slug names another of
#: their workspaces. Recorded beside the path, so every reader and writer goes to the SAME file.
NOTE_WORKSPACE_KEY = "note_workspace"

#: THE PAGE'S IDENTITY KEYS (#1783, founder 2026-10-15: "why not map page to meeting reliably?").
#: A page written for a known meeting carries them in its frontmatter; a bot's mint ADOPTS the page
#: whose key equals the meeting's — exact match, never a title guess.
IDENTITY_KEYS = ("meeting", "native", "meeting_url", "calendar_event")

#: A page name we could have minted: one path segment, `.md`, no dot-leader, no separators. The
#: recorded value arrives from a row an account's own API key can annotate, and it is used to NAME A
#: FILE ON EVERY DESK IN THE ROOM — so it is checked against the alphabet rather than trusted.
_NOTE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md\Z")

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_KV = re.compile(r"\A([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)\Z")

#: Frontmatter is the first thing in the file and it is small; a report is not. Reading the head is
#: enough to answer, and it keeps a directory of 6 KB reports off the critical path of opening a chat.
_HEAD_BYTES = 4096
#: a read-only note handed back whole (a switched-off Personal workspace's report)
_MAX_NOTE_BYTES = 1 << 20


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


def nothing_written(text: str) -> bool:
    """A note with no words in it yet: frontmatter, HTML comments (the transcript marker, region
    fences) and headings only."""
    body = _FRONT_MATTER.sub("", text or "", count=1)
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    body = re.sub(r"(?m)^#{1,6}\s.*$", "", body)
    return not body.strip()


def normal_url(url) -> str:
    """A meeting link as it is compared: scheme and `www.` dropped, host lower-cased, no query or
    fragment, no trailing slash. Two spellings of one link compare equal; two links never do."""
    s = str(url or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s, flags=re.I)
    s = re.split(r"[?#]", s, maxsplit=1)[0].rstrip("/")
    host, _, rest = s.partition("/")
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}/{rest}" if rest else host


def row_identity(row) -> dict:
    """What a meeting row says it IS, for the identity match: row id, native id, link, calendar
    event. Empty values are left out — an empty key matches nothing."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    out = {
        "meeting": str(r.get("id") or "").strip(),
        "native": str(r.get("native_meeting_id") or r.get("platform_specific_id") or "").strip(),
        "meeting_url": normal_url((data or {}).get("constructed_meeting_url") or (data or {}).get("meeting_url")),
        "calendar_event": str((data or {}).get("calendar_uid") or (data or {}).get("calendar_event_id") or "").strip(),
    }
    return {k: v for k, v in out.items() if v}


def identity_matches(fm: dict, row) -> bool:
    """Does this page's frontmatter NAME this meeting — one identity key, exactly equal?"""
    ident = row_identity(row)
    for key in IDENTITY_KEYS:
        mine = str((fm or {}).get(key) or "").strip()
        if not mine or key not in ident:
            continue
        if (normal_url(mine) if key == "meeting_url" else mine) == ident[key]:
            return True
    return False


def resolve_exact_in(desk: Path, row) -> "str | None":
    """The page in ``desk`` whose frontmatter names this meeting by identity — or None. Exact only:
    this is what a mint ADOPTS, so a title that merely looks alike is never enough."""
    try:
        names = wpaths.list_files_inside(Path(desk), MEETING_DIR, suffix=".md")
    except (OSError, ValueError):
        return None
    for name in sorted(names or []):
        if name == "index.md":
            continue
        try:
            head = wpaths.read_head_inside(Path(desk), f"{MEETING_DIR}/{name}", _HEAD_BYTES)
        except (OSError, ValueError):
            continue
        if head is None:
            continue
        fm = front_matter(head)
        if str(fm.get("type") or "").strip().lower() not in ("", "meeting"):
            continue
        if identity_matches(fm, row):
            return f"{MEETING_DIR}/{name}"
    return None


def recorded_place(row) -> tuple[str, str]:
    """``(path, workspace)`` the row records for its page — ``("", "")`` when none."""
    r = row if isinstance(row, dict) else {}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    meta = (data or {}).get("metadata")
    ws = str((meta or {}).get(NOTE_WORKSPACE_KEY) or "").strip() if isinstance(meta, dict) else ""
    return recorded_path(row), ws


def stamp_identity(text: str, keys: dict) -> str:
    """The page with these identity keys set in its frontmatter (and ``type: meeting`` when it has no
    type) — every other line exactly as it was. A page with no frontmatter gets one."""
    keys = {k: str(v).strip() for k, v in (keys or {}).items() if str(v or "").strip()}
    src = text or ""
    m = _FRONT_MATTER.match(src)
    lines = m.group(1).splitlines() if m else []
    body = src[m.end():] if m else src
    have = {(_KV.match(l.rstrip()).group(1).strip().lower() if _KV.match(l.rstrip()) else None): i
            for i, l in enumerate(lines)}
    if "type" not in have:
        lines.insert(0, "type: meeting")
        have = {(_KV.match(l.rstrip()).group(1).strip().lower() if _KV.match(l.rstrip()) else None): i
                for i, l in enumerate(lines)}
    for k, v in keys.items():
        if k in have:
            lines[have[k]] = f"{k}: {v}"
        else:
            lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(lines) + "\n---\n" + ("" if body.startswith("\n") else "\n") + body.lstrip("\n")


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


def desk_holds_note(workspaces_root, subject: str, path) -> bool:
    """Is `path` a meeting's note that lives on this person's DESK? (#1783: one note per meeting.)

    The note is minted on the desk when the bot is sent, and the meeting view reads it there. A turn
    in a chat that works in another workspace used to write the same relative path into THAT
    workspace, so the agent said "extended" while the view kept showing the empty desk copy. A write
    of this path that names no workspace therefore goes to the desk when the desk holds the note:
    a meeting page (`type: meeting`) at exactly that path. Anything else is an ordinary page."""
    if not is_note_path(path):
        return False
    try:
        head = wpaths.read_head_inside(home_dir(workspaces_root, subject), str(path).strip(), _HEAD_BYTES)
    except (OSError, ValueError):
        return False
    return head is not None and front_matter(head).get("type") == "meeting"


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


def note_places(workspaces_root, subject: str) -> list[dict]:
    """WHERE THIS PERSON'S MEETING NOTES CAN BE, in the order they are looked for (#1783).

    ``{"slug", "dir", "home", "hidden"}`` each. First the HOME — the person's primary active
    workspace, which is where every writer with no workspace named lands (the mint, Update notes, the
    page write, the post-meeting drop): their Personal workspace normally, the chat's workspace when
    Personal is switched off or deleted. ``slug`` is ``""`` for it, which is what the read and write
    routes default to. Then their other active workspaces, by slug. LAST, the Personal workspace's own
    directory when it is no longer active (founder, 2026-10-15: he deleted Personal and his finished
    report vanished from the meeting): read-only, never a home, so a report written there before is
    still shown and nothing new is written into a workspace the person took away."""
    root = Path(workspaces_root)
    desk = root / str(subject)
    try:
        from control_plane.workspace_attach import active_workspaces, desk_hidden  # noqa: PLC0415
        mounts = list(active_workspaces(root, str(subject)))
        hidden = desk_hidden(root, str(subject))
    except Exception:  # noqa: BLE001 — an unreadable state is the plain desk, as before
        mounts, hidden = [], False
    # THE HOME is the desk while Personal is on; a hidden Personal leaves NO home — nothing new is
    # written anywhere picked by order (#1783). Other workspaces are still READ for a bound page.
    out: list[dict] = [] if hidden else [{"slug": "", "dir": desk, "home": True, "hidden": False}]
    try:
        seen = {desk.resolve()} if not hidden else set()
        for m in mounts:
            d = Path(m.path)
            if d.resolve() in seen or (hidden and getattr(m, "desk", False)):
                continue
            seen.add(d.resolve())
            out.append({"slug": str(m.slug), "dir": d, "home": False, "hidden": False})
        if hidden and desk.is_dir() and desk.resolve() not in seen:
            out.append({"slug": "", "dir": desk, "home": False, "hidden": True})
    except OSError:
        pass
    return out


def home_dir(workspaces_root, subject: str) -> Path:
    """The directory a meeting's note is written in — ``note_places``' home. Raises ``NoHome`` when
    the person's Personal is hidden (nothing is written by order into another workspace)."""
    for p in note_places(workspaces_root, subject):
        if p["home"]:
            return p["dir"]
    from control_plane.workspace_attach import NoHome  # noqa: PLC0415
    raise NoHome(subject)


def find(workspaces_root, subject: str, row) -> "tuple[str, Path] | None":
    """``(path, directory)`` of this meeting's note wherever ``note_places`` finds it, or None — for
    the readers (the home stream, the bundle export) that used to look only in ``<root>/<subject>``."""
    for p in note_places(workspaces_root, subject):
        hit = resolve_in(p["dir"], row)
        if hit:
            return hit, p["dir"]
    return None


def resolve(workspaces_root, subject: str, row) -> "str | None":
    """This meeting's report on this person's desk (``<root>/<subject>``), as a workspace-relative
    path — or None. ``resolve_in`` is the same search over any one directory."""
    return resolve_in(Path(workspaces_root) / str(subject), row)


def resolve_in(desk: Path, row) -> "str | None":
    """This meeting's report in ``desk``, as a workspace-relative path — or None.

    None is the ordinary answer for a meeting whose report has not been written yet, and the caller
    must treat it as one: it is "nothing here names this meeting", never "something failed"."""
    # TIER 0 — the row says where its page is (Vexa-ai/vexa#1601). Still conditional on the file
    # being on THIS desk: the record is one fact about the meeting, shared by everybody in the room,
    # and an attendee whose drop has not run yet has no such file. A tab onto a path nobody wrote on
    # this desk is exactly the failure this module exists to refuse.
    desk = Path(desk)
    recorded = recorded_path(row)
    if recorded:
        # nofollow: the desk is a work tree the model's tools can write, so a link at the recorded
        # path is not "the report on this desk"
        try:
            if wpaths.is_file_inside(desk, recorded):
                return recorded
        except (OSError, ValueError):
            pass
    facts = row_facts(row)
    if not (facts["id"] or facts["native"] or facts["title"]):
        return None
    names = wpaths.list_files_inside(desk, MEETING_DIR, suffix=".md")
    if not names:
        return None
    ids = {v for v in (facts["id"], facts["native"]) if v}
    title = facts["title"].casefold()
    by_title: list[tuple[str, str]] = []      # (frontmatter day, file name)
    for name in names:
        if name == "index.md":
            continue
        head = wpaths.read_head_inside(desk, f"{MEETING_DIR}/{name}", _HEAD_BYTES)
        if head is None:
            continue
        fm = front_matter(head)
        if str(fm.get("type") or "").strip().lower() not in ("", "meeting"):
            continue
        if ids and (str(fm.get("meeting") or "").strip() in ids
                    or str(fm.get("native") or "").strip() in ids):
            return f"{MEETING_DIR}/{name}"
        if title and str(fm.get("title") or "").strip().casefold() == title:
            by_title.append((_day(fm.get("date")), name))
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

    # BY THE MEETING, WHEREVER IT IS (#1783): the home first, then the person's other active
    # workspaces, then a Personal workspace they switched off — whose report comes back WITH its
    # text, because no read route addresses a workspace that is not active, and read-only.
    path, place = None, None
    places = note_places(workspaces_root, subject)
    # TIER 0 — THE BINDING (#1783): the page the row records, in the workspace it records.
    rec_path, rec_ws = recorded_place(row)
    if rec_path:
        for p in places:
            if p["hidden"] or (p["slug"] != rec_ws) or (not rec_ws and not p["home"]):
                continue
            try:
                if wpaths.is_file_inside(p["dir"], rec_path):
                    path, place = rec_path, p
                    break
            except (OSError, ValueError):
                pass
    if path is None:
        # then a page that names the meeting by identity, then the older lookups
        for finder in (resolve_exact_in, resolve_in):
            for p in places:
                hit = finder(p["dir"], row)
                if hit:
                    path, place = hit, p
                    break
            if path:
                break
    out = {"path": path, "workspace": place["slug"] if place else "", "transcript": "", "cursor": ""}
    if not path:
        return out
    try:
        head = wpaths.read_head_inside(place["dir"], path, _HEAD_BYTES)
    except (OSError, ValueError):
        head = None
    if head is None:
        return out
    out["transcript"] = meeting_doc.slot_meeting(head)
    out["cursor"] = meeting_doc.read_cursor(head)
    if place["hidden"]:
        out["readonly"] = True
        try:
            out["content"] = wpaths.read_text_inside(place["dir"], path, max_bytes=_MAX_NOTE_BYTES) or ""
        except (OSError, ValueError):
            out["content"] = head
    return out
