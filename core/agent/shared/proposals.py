"""proposals.py — THE PERSON'S SHORT LIST: what is worth doing now, written by whichever agent saw it.

Founder, 2026-09-06 14:30Z, on the empty chat (Vexa-ai/vexa#1614):

    *"let's see what we want to have here: create ad hoc google meet meeting; whatever, that is a
    short list that is updated by other agents when they see something as JTBD, can have up to 10
    items"*

An empty chat used to offer one stale chip. What it offers now is this list: **at most ten items,
newest first**, each one an act the person can take in one click, in the chat they are already in.
The STANDING acts (create a Meet, paste a meeting link) are the client's — they are true of
everybody and need no store. What lives here is the other half: the JOBS an agent noticed while
doing something else. The post-meeting turn sees a commitment the report named for this person;
prep sees a meeting nobody has read for; the friction loop sees a page with no source. Each appends
one item and moves on.

FOUR FIELDS, and the schema is the contract:

    source   where the job was SEEN — `meeting:97`, `page:kg/entities/company/oenb.md`. Half of the
             dedup key, and the thing the chip shows so the person knows why they are being asked.
    act      the one line. It is what the chip says and what gets said into the chat on a click.
    since    when it was FIRST seen, ISO-8601 UTC. The list is newest-first on this, and an item
             that has been waiting a week reads as one.
    status   `open` · `ran` · `dismissed`. Only `open` is ever offered.

DEDUP IS `source` + `act`, AND IT IS ALSO THE ID. Two runs of the same flow over the same meeting
propose the same job; a second row for it would be the list eating itself. So the id is a digest of
exactly those two fields: the second write finds the first and UPDATES IT IN PLACE, keeping the
`since` it already had — the job is as old as the first time somebody saw it, not as old as the last
time a flow re-ran.

...AND A CLOSED ITEM STAYS CLOSED. The tombstones are why: an item the person ran or dismissed keeps
its row, with its status, and a later duplicate updates that row rather than resurrecting it. A
store that dropped closed rows would re-offer, on the next flow run, exactly the thing the person
just said no to — which is the one failure that would make the row untrustworthy for good.

WHERE IT LIVES. `<desk>/.vexa/proposals.json`, beside the workspace identity and the touch mirror,
in the dot-dir every enumerator already hides — it is machinery, never a page somebody opens. And
GIT-EXCLUDED for the same reason `mirror_touches` excludes its file: this is a queue, not a fact
about the workspace, and committing it would put a new version of it in somebody's history every
time an agent noticed something.

NOTHING HERE DECIDES WHAT IS WORTH PROPOSING. That judgement belongs to the writer that saw the job,
one writer per item, which is what `by` records. This module owns the file: read it, add to it,
close a row, cap it.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from workspaces.shared.workspace_id import VEXA_DIR

log = logging.getLogger(__name__)

PROPOSALS_FILE = f"{VEXA_DIR}/proposals.json"
CONTRACT = "proposals.v1"

#: The founder's number. Ten is the whole list, so an eleventh job pushes the oldest OPEN one out —
#: the list is "what is worth doing now", and the oldest thing nobody has touched is the weakest
#: claim on that.
OPEN_MAX = 10

#: How many CLOSED rows are kept as tombstones. They exist only so a duplicate cannot resurrect a
#: job the person already answered; past this the memory of a refusal is older than the refusal is
#: worth, and the oldest are dropped.
CLOSED_MAX = 40

OPEN, RAN, DISMISSED = "open", "ran", "dismissed"
STATUSES = (OPEN, RAN, DISMISSED)

# Length caps, applied on the way in. A store an agent writes to is a store an agent can flood, and
# a chip is one line: an act longer than this is not an act, it is a paragraph.
ACT_MAX, SOURCE_MAX, LABEL_MAX, BY_MAX = 200, 200, 80, 40

UTC = datetime.timezone.utc


def _iso(at: Optional[float] = None) -> str:
    when = datetime.datetime.now(UTC) if at is None else datetime.datetime.fromtimestamp(float(at), UTC)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def item_id(source: str, act: str) -> str:
    """The dedup key, made addressable.

    A digest of `source` + `act` and nothing else, so the id a writer would mint for a job it has
    already proposed is the id that job already has — no read is needed to know it, and the client
    can dismiss a row by an id that means something rather than by a position in a list."""
    raw = f"{str(source or '').strip()}\n{str(act or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _clip(value, cap: int) -> str:
    return str(value or "").strip()[:cap]


def _path(root) -> Path:
    return Path(root) / PROPOSALS_FILE


def read(root) -> list[dict]:
    """Every row on this desk, in stored order (newest first). A missing or unreadable file is an
    EMPTY LIST, never an error: a chat that refused to render because a cache file was half-written
    would be a worse product than a chat with no chips."""
    try:
        raw = _path(root).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        doc = json.loads(raw)
    except ValueError:
        log.info("proposals: %s is not readable JSON — treating the list as empty", _path(root))
        return []
    items = doc.get("items") if isinstance(doc, dict) else doc
    return [i for i in (items or []) if isinstance(i, dict)]


def open_items(root, cap: int = OPEN_MAX) -> list[dict]:
    """What the empty chat is offered: OPEN rows, newest first, at most `cap`."""
    return [i for i in read(root) if str(i.get("status") or OPEN) == OPEN][:cap]


def _write(root, items: list[dict]) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"contract": CONTRACT, "items": items}, indent=1) + "\n",
                 encoding="utf-8")
    _exclude_from_git(Path(root))


def _exclude_from_git(root: Path) -> None:
    """Keep the queue out of the desk's history — `.git/info/exclude`, per clone, never travelling.

    The same treatment `mirror_touches` gives the touch list and for the same reason: a turn's
    `git add -A` would otherwise commit a new version of this file every time an agent noticed
    something, which is churn in the history of somebody's desk for a value that is not a fact about
    the workspace. Best-effort — a desk that is not a git repository simply has nothing to exclude."""
    try:
        info = root / ".git" / "info"
        if not info.parent.is_dir():
            return
        info.mkdir(parents=True, exist_ok=True)
        ex = info / "exclude"
        body = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if f"/{PROPOSALS_FILE}" not in body:
            ex.write_text(body.rstrip("\n") + f"\n/{PROPOSALS_FILE}\n", encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 — a queue is never worth failing a turn over
        log.info("proposals: could not exclude %s from git: %s", PROPOSALS_FILE, exc)


def _cap(items: list[dict]) -> list[dict]:
    """Ten open, forty tombstones — both newest-first, both trimmed from the bottom."""
    kept_open, kept_closed = [], []
    for i in items:
        (kept_open if str(i.get("status") or OPEN) == OPEN else kept_closed).append(i)
    return kept_open[:OPEN_MAX] + kept_closed[:CLOSED_MAX]


def add(root, *, source: str, act: str, source_label: str = "", by: str = "",
        at: Optional[float] = None) -> dict:
    """Propose one job. Returns the row as it now stands, with `added` saying whether it is new.

    A duplicate (same `source` + `act`) UPDATES IN PLACE: it keeps its `since`, keeps its `status`
    — a job the person already ran or dismissed is not re-offered by a flow running twice — and
    refreshes the label and the writer. A new row goes to the FRONT, because the list is newest
    first and the thing an agent just noticed is the freshest claim on the person's attention."""
    source, act = _clip(source, SOURCE_MAX), _clip(act, ACT_MAX)
    if not source or not act:
        raise ValueError("a proposal needs both a source (where the job was seen) and an act")
    iid = item_id(source, act)
    items = read(root)
    for i in items:
        if str(i.get("id") or "") == iid:
            i["source_label"] = _clip(source_label, LABEL_MAX) or i.get("source_label") or ""
            i["by"] = _clip(by, BY_MAX) or i.get("by") or ""
            _write(root, _cap(items))
            return {**i, "added": False}
    row = {"id": iid, "source": source, "source_label": _clip(source_label, LABEL_MAX),
           "act": act, "since": _iso(at), "status": OPEN, "by": _clip(by, BY_MAX)}
    _write(root, _cap([row] + items))
    return {**row, "added": True}


def resolve(root, iid: str, status: str) -> Optional[dict]:
    """Close one row — `ran` when its act fired, `dismissed` when the person said no. `None` when
    this desk has no such row, which the caller reports as a 404 rather than inventing one."""
    if status not in (RAN, DISMISSED):
        raise ValueError(f"a proposal closes as {RAN!r} or {DISMISSED!r}, never {status!r}")
    items = read(root)
    for i in items:
        if str(i.get("id") or "") == str(iid or ""):
            i["status"] = status
            i["closed_at"] = _iso()
            _write(root, _cap(items))
            return dict(i)
    return None
