"""session_store.py — conversations, owned by the agent module and stored apart from workspaces
(ADR-0048).

A SESSION is a conversation: an id, an owner, participants with roles, a title, the workspaces it
uses, an optional flow binding (a routine thread, ADR-0046) and an optional channel (a workspace
whose members may see and join it). Its METADATA lives here, in agent-api's own database. Its
TRANSCRIPT — every file the harness keeps under its continuity root, plus the worker's turn records —
lives in object storage as one immutable object per turn version (``session_blobs``).

THE ONE RULE OF WRITING A TRANSCRIPT: a new version names the version it was based on, and it is
accepted only while that version is still the head. Two harness transcripts cannot be merged into
anything meaningful, so a stale base is refused (``SessionConflict``) and logged — never merged and
never overwritten. The object is written first under a fresh nonce and only then made the head, so a
refused write leaves no trace but an orphan the refusal deletes.

The database is reached through DB-API: ``sqlite:///path`` (tests, a single machine) or
``postgresql://user:pass@host:port/db`` through ``pg8000`` (BSD-3-Clause, pure Python, the same
driver flows uses). One connection, one lock: the store's writes are a handful per turn.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

from control_plane import session_blobs

logger = logging.getLogger("agent_api.session_store")

SCHEMA_VERSION = 1
#: A whole transcript bundle (every file of one session at one version). The worker's own resume
#: budget (``VEXA_CHAT_RESUME_MAX_BYTES``, 1 MB by default) restarts a thread long before this.
MAX_BUNDLE_BYTES = 64 << 20
#: The roles a participant can hold. ``owner`` is unique per session and is never removed.
ROLES = ("owner", "member", "viewer")
#: Who sees and joins a room, and how readily its agent speaks up (rooms, 2026-10-15).
ROOM_VISIBILITY = ("open", "closed", "direct")
ROOM_PROACTIVITY = ("off", "quiet", "active")
KINDS = ("human", "agent")

_REL = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._@+=-]*(/[A-Za-z0-9._-][A-Za-z0-9._@+=-]*)*$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def flow_ref(scope: str, name: str) -> str:
    """A session's flow binding (ADR-0046 decision 12): ``<scope>/<flow name>``. A scope never
    holds a ``/`` (``instance`` or ``user:<uid>``), so the first one splits it back."""
    return f"{scope or 'instance'}/{name}"


def read_flow(value: Optional[str]) -> Optional[dict]:
    """``{"scope", "flow"}`` for a stored binding, or None for a session no flow owns."""
    if not value or "/" not in str(value):
        return None
    scope, name = str(value).split("/", 1)
    return {"scope": scope, "flow": name} if scope and name else None

_DDL = (
    "CREATE TABLE IF NOT EXISTS agent_session_schema (version INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS agent_session (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        workspaces TEXT NOT NULL DEFAULT '[]',
        flow TEXT,
        channel TEXT,
        head_version INTEGER NOT NULL DEFAULT 0,
        head_key TEXT,
        created DOUBLE PRECISION NOT NULL,
        last_active DOUBLE PRECISION NOT NULL,
        UNIQUE (owner, name))""",
    """CREATE TABLE IF NOT EXISTS agent_session_version (
        session_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        object_key TEXT NOT NULL,
        bytes INTEGER NOT NULL,
        created DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (session_id, version))""",
    """CREATE TABLE IF NOT EXISTS agent_session_note (
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        author TEXT NOT NULL,
        text TEXT NOT NULL,
        created DOUBLE PRECISION NOT NULL,
        heard INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (session_id, seq))""",
    """CREATE TABLE IF NOT EXISTS agent_session_participant (
        session_id TEXT NOT NULL,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        role TEXT NOT NULL,
        added DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (session_id, principal))""",
    # A call held in the session (ADR-0048 decision 6): the LiveKit room, the meeting row it is,
    # who started it, and the transcript share participants are given when they join.
    """CREATE TABLE IF NOT EXISTS agent_session_call (
        session_id TEXT NOT NULL,
        room TEXT NOT NULL,
        meeting_id INTEGER NOT NULL,
        owner TEXT NOT NULL,
        share_token TEXT NOT NULL,
        started DOUBLE PRECISION NOT NULL,
        ended DOUBLE PRECISION,
        PRIMARY KEY (session_id, room))""",
    # A file a participant attached to a message here: whose desk it is on and where. The
    # attachment route answers from THIS row to the session's participants — never from workspace
    # membership — so sharing a conversation shares what was posted in it and nothing else.
    """CREATE TABLE IF NOT EXISTS agent_session_attachment (
        session_id TEXT NOT NULL,
        path TEXT NOT NULL,
        poster TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        created DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (session_id, path, poster))""",
    # A change proposed after a call or meeting (ADR-0048 decision 7): nothing lands in a workspace
    # until the meeting's owner accepts it.
    """CREATE TABLE IF NOT EXISTS agent_session_proposal (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        meeting_id INTEGER,
        owner TEXT NOT NULL,
        kind TEXT NOT NULL,
        workspace TEXT,
        path TEXT,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        status TEXT NOT NULL,
        decided_by TEXT,
        decided DOUBLE PRECISION,
        created DOUBLE PRECISION NOT NULL)""",
    # A person's chat folders (#1783, ``chat_folders``): per PERSON, never per chat, so on a shared
    # session each participant files it in their own folders and nobody sees another's. The built-in
    # sections (pinned, hidden, ungrouped) have a row too, which holds their collapsed state.
    """CREATE TABLE IF NOT EXISTS agent_session_folder (
        owner TEXT NOT NULL,
        id TEXT NOT NULL,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        collapsed INTEGER NOT NULL DEFAULT 0,
        created DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (owner, id))""",
    # Where one person filed one rail row (``c:<session>`` or ``m:<meeting>``): one place per row.
    """CREATE TABLE IF NOT EXISTS agent_session_filing (
        owner TEXT NOT NULL,
        row_key TEXT NOT NULL,
        folder TEXT NOT NULL,
        position DOUBLE PRECISION NOT NULL DEFAULT 0,
        filed DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (owner, row_key))""",
    # What happened to a person that they did not do themselves (#1783, ``notifications``): one row
    # per RECIPIENT, so isolation is a key, not a filter. The text names the actor and the object
    # only — never a message, a page or a transcript line (the leak rule).
    """CREATE TABLE IF NOT EXISTS agent_session_notification (
        id TEXT PRIMARY KEY,
        recipient TEXT NOT NULL,
        kind TEXT NOT NULL,
        actor TEXT NOT NULL,
        actor_name TEXT NOT NULL DEFAULT '',
        object_kind TEXT NOT NULL,
        object_id TEXT NOT NULL,
        object_name TEXT NOT NULL DEFAULT '',
        url TEXT,
        source TEXT,
        text TEXT NOT NULL,
        created DOUBLE PRECISION NOT NULL,
        read_at DOUBLE PRECISION,
        acted TEXT)""",
    "CREATE INDEX IF NOT EXISTS agent_session_notification_recipient "
    "ON agent_session_notification (recipient, created)",
    # The new-chat home (#1783, ``home``): every composition APPENDED (never overwritten) and every
    # card's lifecycle as an event — ids, titles and short reasons only, one owner per row.
    """CREATE TABLE IF NOT EXISTS agent_session_home_composition (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        composed_at DOUBLE PRECISION NOT NULL,
        trigger TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT '',
        inputs TEXT NOT NULL DEFAULT '{}',
        view TEXT NOT NULL,
        customer TEXT NOT NULL DEFAULT '{}',
        next_due DOUBLE PRECISION NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS agent_session_home_composition_owner ON agent_session_home_composition (owner, composed_at)",
    """CREATE TABLE IF NOT EXISTS agent_session_home_event (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        composition TEXT NOT NULL DEFAULT '',
        card TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT '',
        event TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        people TEXT NOT NULL DEFAULT '[]',
        chat TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT '',
        page TEXT NOT NULL DEFAULT '',
        at DOUBLE PRECISION NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS agent_session_home_event_owner ON agent_session_home_event (owner, at)",
    # A CHAT WITH TWO OR MORE PEOPLE IS A ROOM (rooms, 2026-10-15): who may see and join it
    # (`visibility`: open — listed to its workspace's members, who join themselves; closed — its
    # members only, by invite; direct), how readily the agent speaks up (`proactivity`: off, quiet,
    # active — any member changes it, visibly), and the listening brief the agent writes for the
    # room's sensor (`listen`, JSON, versioned). One row per room; a one-person chat has none.
    """CREATE TABLE IF NOT EXISTS agent_session_room (
        session_id TEXT PRIMARY KEY,
        visibility TEXT NOT NULL,
        proactivity TEXT NOT NULL,
        workspace TEXT,
        listen TEXT NOT NULL DEFAULT '{}',
        listen_version INTEGER NOT NULL DEFAULT 0,
        updated DOUBLE PRECISION NOT NULL,
        level TEXT,
        created_by TEXT,
        org_id TEXT,
        cross_org INTEGER)""",
    # WHAT THE ROOM'S AGENT CHANGED DURING A CALL (R1 1.5): one row per page write it made while the
    # room's call ran — the page before (the undo), a digest of the page after (to tell whether
    # anyone changed it since), who it answered, and whether a person undid it.
    """CREATE TABLE IF NOT EXISTS agent_session_call_change (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        room TEXT NOT NULL,
        meeting_id INTEGER,
        unit TEXT NOT NULL,
        on_behalf_of TEXT,
        workspace TEXT NOT NULL,
        path TEXT NOT NULL,
        base TEXT,
        after_digest TEXT NOT NULL,
        at DOUBLE PRECISION NOT NULL,
        undone_by TEXT,
        undone_at DOUBLE PRECISION)""",
    "CREATE INDEX IF NOT EXISTS agent_session_call_change_room ON agent_session_call_change (room, at)",)

#: Columns a table gained after its first release. ``CREATE TABLE IF NOT EXISTS`` leaves an existing
#: table as it was, so each is added where it is missing: by ``session_schema init`` on Postgres
#: (``ADD COLUMN IF NOT EXISTS``), by ``migrate`` on SQLite. Nullable, so no row needs a value.
_ADDED_COLUMNS = (
    ("agent_session_notification", "source", "TEXT"),    # the agent's own notice (`notify`)
    ("agent_session_notification", "acted", "TEXT"),     # what the person did with a notice
    # ROOM-LEVEL SETTINGS (R1, 2026-10-16): the room's own model level, who made it, its org, and
    # whether people from outside the org read it — one truth for the room, not one per member.
    ("agent_session_room", "level", "TEXT"),
    ("agent_session_room", "created_by", "TEXT"),
    ("agent_session_room", "org_id", "TEXT"),
    ("agent_session_room", "cross_org", "INTEGER"),
    ("agent_session_participant", "read_at", "DOUBLE PRECISION"),  # when this person last read the room
)


def _is_disconnect(exc: Exception) -> bool:
    """A lost connection (pg8000's InterfaceError, a socket error) — never a refused statement."""
    name = type(exc).__name__
    return name in ("InterfaceError", "ConnectionError", "BrokenPipeError", "ConnectionResetError") \
        or isinstance(exc, (ConnectionError, BrokenPipeError))


class SessionConflict(RuntimeError):
    """A transcript version based on something other than the head. Refused, never merged."""

    def __init__(self, session_id: str, base: int, head: int) -> None:
        super().__init__(f"session {session_id}: version {base} is not the head ({head})")
        self.session_id, self.base, self.head = session_id, base, head


class SessionDenied(PermissionError):
    """The caller's role in the session does not allow the act. ``reason`` is typed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_rel(rel: str) -> str:
    """A bundle path: relative, plain components, no traversal. Refused, never repaired."""
    if not isinstance(rel, str) or not _REL.fullmatch(rel) or any(p in (".", "..") for p in rel.split("/")):
        raise ValueError("not a plain relative path")
    return rel


def encode_files(files: dict) -> dict:
    return {check_rel(k): base64.b64encode(v).decode("ascii") for k, v in files.items()}


def decode_files(raw: dict) -> dict:
    return {check_rel(k): base64.b64decode(v) for k, v in (raw or {}).items()}


def _connect_postgres(*, host: str, port: int, database: str, user: str, password: str):
    import pg8000.dbapi  # noqa: PLC0415 — only a Postgres-backed store loads the driver

    conn = pg8000.dbapi.connect(user=user, password=password, host=host, port=port, database=database)
    conn.autocommit = True
    return conn, (lambda sql: sql.replace("?", "%s")), "postgres"


def _connect(url: str):
    """``(connection, paramstyle-converter)`` for a DB URL. Postgres through pg8000 is imported only
    when asked for, so a test run and an install with the store off need no driver."""
    u = urlparse(url)
    if u.scheme == "sqlite":
        import sqlite3

        path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else ":memory:"
        conn = sqlite3.connect(path or ":memory:", check_same_thread=False, isolation_level=None)
        return conn, (lambda sql: sql), "sqlite"
    if u.scheme in ("postgres", "postgresql", "postgresql+pg8000"):
        return _connect_postgres(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                 host=u.hostname or "localhost", port=u.port or 5432,
                                 database=(u.path or "/").lstrip("/") or "vexa")
    raise ValueError(f"unsupported session database scheme {u.scheme!r}")


class SessionStore:
    def __init__(self, conn, conv, dialect: str, blobs: "session_blobs.BlobStore",
                 reconnect=None) -> None:
        self._conn, self._conv, self._dialect, self._blobs = conn, conv, dialect, blobs
        self._lock = threading.RLock()
        self._reconnect = reconnect
        self._in_tx = False

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    @classmethod
    def open(cls, db_url: str, blobs: "session_blobs.BlobStore") -> "SessionStore":
        """A SQLite file migrates itself; a Postgres URL goes through ``open_postgres``."""
        if not db_url.startswith("sqlite:"):
            u = urlparse(db_url)
            return cls.open_postgres(blobs, host=u.hostname or "localhost", port=u.port or 5432,
                                     database=(u.path or "/").lstrip("/") or "vexa",
                                     user=unquote(u.username or ""), password=unquote(u.password or ""))
        conn, conv, dialect = _connect(db_url)
        s = cls(conn, conv, dialect, blobs, reconnect=lambda: _connect(db_url))
        s.migrate()
        return s

    @classmethod
    def open_postgres(cls, blobs: "session_blobs.BlobStore", **params) -> "SessionStore":
        """The instance's Postgres by its parts (no URL, so no password ever needs quoting), as the
        session store's OWN role. No DDL here: the migrations path creates the tables and the role
        (``session_schema init``); this only checks the login and the schema version, and raises
        ``session_schema.SchemaNotReady`` (the store stays off) when either is wrong."""
        from control_plane import session_schema  # noqa: PLC0415 — imports this module

        conn, conv, dialect = _connect_postgres(**params)
        try:
            session_schema.check(conn)
        except BaseException:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        return cls(conn, conv, dialect, blobs, reconnect=lambda: _connect_postgres(**params))

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _x(self, sql: str, args: Iterable = ()):
        try:
            cur = self._conn.cursor()
            cur.execute(self._conv(sql), tuple(args))
            return cur
        except Exception as exc:  # noqa: BLE001 — classified below
            # A connection the database dropped (a restart, an idle timeout) is opened again once,
            # outside a transaction only: a statement inside one cannot be replayed on its own.
            if self._reconnect is None or self._in_tx or not _is_disconnect(exc):
                raise
            logger.warning("session store: the database connection dropped; reconnecting")
            self._conn, self._conv, self._dialect = self._reconnect()
            cur = self._conn.cursor()
            cur.execute(self._conv(sql), tuple(args))
            return cur

    def _tx(self):
        store = self

        class _T:
            def __enter__(self):
                store._lock.acquire()
                try:
                    store._x("BEGIN")
                except BaseException:
                    store._lock.release()
                    raise
                store._in_tx = True
                return store

            def __exit__(self, et, ev, tb):
                try:
                    store._in_tx = False
                    store._x("ROLLBACK" if et else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _T()

    def migrate(self) -> None:
        """Create the tables if absent and record the schema version. Idempotent. For a SQLite file
        (tests, a single process) only — Postgres is migrated by ``session_schema init``."""
        with self._lock:
            for ddl in _DDL:
                self._x(ddl)
            for table, col, decl in _ADDED_COLUMNS:
                if col not in {r[1] for r in self._x(f"PRAGMA table_info({table})").fetchall()}:
                    self._x(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            row = self._x("SELECT version FROM agent_session_schema").fetchone()
            if row is None:
                self._x("INSERT INTO agent_session_schema (version) VALUES (?)", (SCHEMA_VERSION,))

    def schema_version(self) -> int:
        with self._lock:
            row = self._x("SELECT version FROM agent_session_schema").fetchone()
            return int(row[0]) if row else 0

    # ── sessions ─────────────────────────────────────────────────────────────────────────────
    _COLS = "id, owner, name, title, workspaces, flow, channel, head_version, created, last_active"

    def _row(self, r) -> Optional[dict]:
        if r is None:
            return None
        try:
            ws = json.loads(r[4] or "[]")
        except ValueError:
            ws = []
        return {"id": r[0], "owner": r[1], "name": r[2], "title": r[3], "workspaces": ws,
                "flow": r[5], "channel": r[6], "version": int(r[7] or 0),
                "created": float(r[8]), "last_active": float(r[9])}

    def create(self, owner: str, *, name: Optional[str] = None, title: str = "",
               workspaces: "list[str] | None" = None, flow: Optional[str] = None,
               channel: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """Create a session owned by ``owner`` — or return the one this owner already has under
        ``name``. The owner is its first participant."""
        if not owner:
            raise ValueError("a session has an owner")
        sid = session_id or f"s-{uuid.uuid4().hex}"
        name = name or sid
        if not _NAME.fullmatch(name) or not _NAME.fullmatch(sid):
            raise ValueError("not a session name")
        with self._tx():
            got = self._x(f"SELECT {self._COLS} FROM agent_session WHERE owner = ? AND name = ?",
                          (owner, name)).fetchone()
            if got is not None:
                return self._row(got)
            now = time.time()
            self._x("INSERT INTO agent_session (id, owner, name, title, workspaces, flow, channel, "
                    "head_version, created, last_active) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (sid, owner, name, title or "", json.dumps(list(workspaces or [])), flow,
                     channel, now, now))
            self._x("INSERT INTO agent_session_participant (session_id, principal, kind, role, added) "
                    "VALUES (?, ?, 'human', 'owner', ?)", (sid, owner, now))
        return self.get(sid)

    def get(self, session_id: str) -> Optional[dict]:
        with self._lock:
            return self._row(self._x(f"SELECT {self._COLS} FROM agent_session WHERE id = ?",
                                     (session_id,)).fetchone())

    def by_name(self, owner: str, name: str) -> Optional[dict]:
        with self._lock:
            return self._row(self._x(f"SELECT {self._COLS} FROM agent_session WHERE owner = ? AND name = ?",
                                     (owner, name)).fetchone())

    def list_for(self, principal: str) -> list[dict]:
        """Every session ``principal`` participates in, most recently active first, each with the
        caller's own ``role``."""
        with self._lock:
            rows = self._x(
                f"SELECT {', '.join('s.' + c.strip() for c in self._COLS.split(','))}, p.role "
                "FROM agent_session s JOIN agent_session_participant p ON p.session_id = s.id "
                "WHERE p.principal = ? ORDER BY s.last_active DESC", (principal,)).fetchall()
        out = []
        for r in rows:
            row = self._row(r[:10])
            row["role"] = r[10]
            out.append(row)
        return out

    def update(self, session_id: str, *, title: Optional[str] = None,
               workspaces: "list[str] | None" = None, channel: "str | None | bool" = False,
               flow: "str | None | bool" = False, touch: bool = False) -> None:
        sets, args = [], []
        if flow is not False:
            sets.append("flow = ?")
            args.append(flow)
        if title is not None:
            sets.append("title = ?")
            args.append(title)
        if workspaces is not None:
            sets.append("workspaces = ?")
            args.append(json.dumps(list(workspaces)))
        if channel is not False:
            sets.append("channel = ?")
            args.append(channel)
        if touch or sets:
            sets.append("last_active = ?")
            args.append(time.time())
        if not sets:
            return
        with self._tx():
            self._x(f"UPDATE agent_session SET {', '.join(sets)} WHERE id = ?", (*args, session_id))

    def set_name(self, session_id: str, name: str) -> None:
        """Give a session another name under its owner (a name is the owner's handle for a thread).
        Refused when the owner already has a session by that name."""
        if not _NAME.fullmatch(name):
            raise ValueError("not a session name")
        with self._tx():
            row = self._x("SELECT owner FROM agent_session WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("no such session")
            if self._x("SELECT 1 FROM agent_session WHERE owner = ? AND name = ? AND id <> ?",
                       (row[0], name, session_id)).fetchone() is not None:
                raise ValueError("the owner already has a session by that name")
            self._x("UPDATE agent_session SET name = ? WHERE id = ?", (name, session_id))

    def delete(self, session_id: str) -> None:
        """Remove the session, its participants and every transcript version."""
        with self._lock:
            keys = [r[0] for r in self._x("SELECT object_key FROM agent_session_version WHERE session_id = ?",
                                          (session_id,)).fetchall()]
        with self._tx():
            self._x("DELETE FROM agent_session_version WHERE session_id = ?", (session_id,))
            self._x("DELETE FROM agent_session_participant WHERE session_id = ?", (session_id,))
            self._x("DELETE FROM agent_session_note WHERE session_id = ?", (session_id,))
            self._x("DELETE FROM agent_session_attachment WHERE session_id = ?", (session_id,))
            self._x("DELETE FROM agent_session_room WHERE session_id = ?", (session_id,))
            self._x("DELETE FROM agent_session WHERE id = ?", (session_id,))
        for k in keys:
            try:
                self._blobs.delete(k)
            except Exception:  # noqa: BLE001 — metadata is gone; an orphan object is logged, not fatal
                logger.warning("session %s: a transcript object was not deleted", session_id)

    def redact_owned(self, owner: str, redact) -> dict:
        """FORGET IN THE TRANSCRIPTS (Vexa-ai/vexa#1783): apply ``redact(text) -> (text, n)`` to every
        session ``owner`` owns — every stored transcript VERSION (each object rewritten in place at
        its own key, so no older version keeps a copy), the title, and every posted note. A turn is
        kept; only the matched spans change. Returns ``{"sessions": k, "spans": n}``."""
        sessions, spans = 0, 0
        with self._lock:
            ids = [r[0] for r in self._x("SELECT id FROM agent_session WHERE owner = ?", (owner,)).fetchall()]
        for sid in ids:
            touched = 0
            with self._lock:
                rows = self._x("SELECT object_key FROM agent_session_version WHERE session_id = ?",
                               (sid,)).fetchall()
                head = self._x("SELECT title FROM agent_session WHERE id = ?", (sid,)).fetchone()
                notes = self._x("SELECT seq, text FROM agent_session_note WHERE session_id = ?",
                                (sid,)).fetchall()
            for (key,) in rows:
                raw = self._blobs.get(key)
                if raw is None:
                    continue
                doc = json.loads(raw)
                files = decode_files(doc.get("files"))
                changed = False
                for rel, data in list(files.items()):
                    try:
                        text = data.decode("utf-8")
                    except (UnicodeDecodeError, AttributeError):
                        continue
                    new, n = redact(text)
                    if n:
                        files[rel] = new.encode("utf-8")
                        touched += n
                        changed = True
                if changed:
                    doc["files"] = encode_files(files)
                    self._blobs.put(key, json.dumps(doc).encode("utf-8"))
            if head and head[0]:
                new, n = redact(head[0])
                if n:
                    touched += n
                    with self._tx():
                        self._x("UPDATE agent_session SET title = ? WHERE id = ?", (new, sid))
            for seq, text in notes:
                new, n = redact(text or "")
                if n:
                    touched += n
                    with self._tx():
                        self._x("UPDATE agent_session_note SET text = ? WHERE session_id = ? AND seq = ?",
                                (new, sid, seq))
            if touched:
                sessions += 1
                spans += touched
        return {"sessions": sessions, "spans": spans}

    # ── participants and roles (ADR-0048 decision 4, the session gate) ───────────────────────
    def participants(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._x("SELECT principal, kind, role, added FROM agent_session_participant "
                           "WHERE session_id = ? ORDER BY added", (session_id,)).fetchall()
        return [{"principal": r[0], "kind": r[1], "role": r[2], "added": float(r[3])} for r in rows]

    def role_of(self, session_id: str, principal: str) -> Optional[str]:
        with self._lock:
            r = self._x("SELECT role FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                        (session_id, principal)).fetchone()
        return r[0] if r else None

    # ── what a person has read (a room's unread count, #1783 R3) ─────────────────────────────
    def mark_read(self, session_id: str, principal: str, at: Optional[float] = None) -> None:
        """This person has read the room up to now. Their own row only; nobody else's moves."""
        now = float(at if at is not None else time.time())
        with self._tx():
            self._x("UPDATE agent_session_participant SET read_at = ? WHERE session_id = ? AND principal = ?",
                    (now, session_id, principal))

    def unread(self, session_id: str, principal: str) -> int:
        """Messages OTHERS posted here since this person last read it (since they joined, when they
        never have). Their own lines never count; a non-participant reads 0."""
        with self._lock:
            r = self._x("SELECT COALESCE(read_at, added) FROM agent_session_participant "
                        "WHERE session_id = ? AND principal = ?", (session_id, principal)).fetchone()
            if r is None:
                return 0
            n = self._x("SELECT COUNT(*) FROM agent_session_note WHERE session_id = ? AND author <> ? "
                        "AND created > ?", (session_id, principal, float(r[0]))).fetchone()
        return int(n[0]) if n else 0

    def humans(self, session_id: str) -> list[str]:
        return [p["principal"] for p in self.participants(session_id) if p["kind"] == "human"]

    def add_participant(self, session_id: str, actor: str, principal: str, *,
                        kind: str = "human", role: str = "member") -> dict:
        """``actor`` adds ``principal``. Only the owner adds; ``owner`` is never granted twice.
        Re-adding changes the role. Returns the participant row."""
        if kind not in KINDS or role not in ROLES or role == "owner":
            raise ValueError("not a participant kind or role")
        if not principal:
            raise ValueError("no principal")
        if self.role_of(session_id, actor) != "owner":
            raise SessionDenied("not_session_owner")
        with self._tx():
            got = self._x("SELECT role FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                          (session_id, principal)).fetchone()
            if got is not None and got[0] == "owner":
                raise SessionDenied("owner_role_fixed")
            if got is None:
                self._x("INSERT INTO agent_session_participant (session_id, principal, kind, role, added) "
                        "VALUES (?, ?, ?, ?, ?)", (session_id, principal, kind, role, time.time()))
            else:
                self._x("UPDATE agent_session_participant SET role = ?, kind = ? "
                        "WHERE session_id = ? AND principal = ?", (role, kind, session_id, principal))
        return next(p for p in self.participants(session_id) if p["principal"] == principal)

    def remove_participant(self, session_id: str, actor: str, principal: str) -> bool:
        """The owner removes anyone but themself; anyone may leave."""
        role = self.role_of(session_id, principal)
        if role is None:
            return False
        if role == "owner":
            raise SessionDenied("owner_role_fixed")
        if actor != principal and self.role_of(session_id, actor) != "owner":
            raise SessionDenied("not_session_owner")
        with self._tx():
            self._x("DELETE FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                    (session_id, principal))
        return True

    def join(self, session_id: str, principal: str) -> dict:
        """``principal`` joins the session as a member themself — an OPEN room's self-join. The route
        decides who may (`rooms.can_join`); the store only refuses taking the owner's role."""
        if not principal:
            raise ValueError("no principal")
        with self._tx():
            got = self._x("SELECT role FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                          (session_id, principal)).fetchone()
            if got is None:
                self._x("INSERT INTO agent_session_participant (session_id, principal, kind, role, added) "
                        "VALUES (?, ?, 'human', 'member', ?)", (session_id, principal, time.time()))
        return next(p for p in self.participants(session_id) if p["principal"] == principal)

    # ── rooms (2026-10-15) ───────────────────────────────────────────────────────────────────
    def room(self, session_id: str) -> Optional[dict]:
        """The room row of ``session_id``, or None (a one-person chat has none)."""
        with self._lock:
            r = self._x("SELECT visibility, proactivity, workspace, listen, listen_version, updated, "
                        "level, created_by, org_id, cross_org "
                        "FROM agent_session_room WHERE session_id = ?", (session_id,)).fetchone()
        if r is None:
            return None
        try:
            listen = json.loads(r[3] or "{}")
        except ValueError:
            listen = {}
        return {"visibility": r[0], "proactivity": r[1], "workspace": r[2] or "",
                "listen": listen if isinstance(listen, dict) else {},
                "listen_version": int(r[4] or 0), "updated": float(r[5]),
                "level": r[6] or "", "created_by": r[7] or "", "org_id": r[8] or "",
                "cross_org": bool(r[9])}

    def put_room(self, session_id: str, *, visibility: Optional[str] = None,
                 proactivity: Optional[str] = None, workspace: "str | None | bool" = False,
                 listen: Optional[dict] = None, level: Optional[str] = None,
                 created_by: Optional[str] = None, org_id: Optional[str] = None,
                 cross_org: Optional[bool] = None) -> dict:
        """Create or change the room row; fields left out keep their value (a new row takes
        ``closed`` and ``quiet``). A new ``listen`` bumps its version."""
        if visibility is not None and visibility not in ROOM_VISIBILITY:
            raise ValueError("not a room visibility")
        if proactivity is not None and proactivity not in ROOM_PROACTIVITY:
            raise ValueError("not a room proactivity")
        with self._tx():
            cur = self.room(session_id)
            now = time.time()
            if cur is None:
                self._x("INSERT INTO agent_session_room (session_id, visibility, proactivity, workspace, "
                        "listen, listen_version, updated, level, created_by, org_id, cross_org) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (session_id, visibility or "closed", proactivity or "quiet",
                         (workspace or None) if workspace is not False else None,
                         json.dumps(listen or {}), 1 if listen else 0, now, level or None,
                         created_by or None, org_id or None, 1 if cross_org else 0))
            else:
                self._x("UPDATE agent_session_room SET visibility = ?, proactivity = ?, workspace = ?, "
                        "listen = ?, listen_version = ?, updated = ?, level = ?, cross_org = ? "
                        "WHERE session_id = ?",
                        (visibility or cur["visibility"], proactivity or cur["proactivity"],
                         (workspace or None) if workspace is not False else (cur["workspace"] or None),
                         json.dumps(listen if listen is not None else cur["listen"]),
                         cur["listen_version"] + (1 if listen is not None else 0), now,
                         (level if level is not None else cur["level"]) or None,
                         (1 if cross_org else 0) if cross_org is not None else (1 if cur["cross_org"] else 0),
                         session_id))
        return self.room(session_id)

    def open_rooms(self, workspace: str) -> list[dict]:
        """The OPEN rooms whose home is ``workspace``: id, title, owner — what its members are shown."""
        with self._lock:
            rows = self._x("SELECT s.id, s.title, s.owner, s.last_active FROM agent_session s "
                           "JOIN agent_session_room r ON r.session_id = s.id "
                           "WHERE r.visibility = 'open' AND r.workspace = ? ORDER BY s.last_active DESC",
                           (workspace,)).fetchall()
        return [{"id": r[0], "title": r[1], "owner": r[2], "last_active": float(r[3])} for r in rows]

    def hand_on(self, session_id: str, actor: str, to: str) -> None:
        """The owner hands the session on to another human member (rooms): they become its owner,
        the old owner a member. Refused unless ``actor`` owns it and ``to`` is a human member."""
        if self.role_of(session_id, actor) != "owner":
            raise SessionDenied("not_session_owner")
        if to == actor or self.role_of(session_id, to) not in ("member", "viewer"):
            raise SessionDenied("not_a_member")
        if not any(p["principal"] == to and p["kind"] == "human" for p in self.participants(session_id)):
            raise SessionDenied("not_a_member")
        with self._tx():
            self._x("UPDATE agent_session_participant SET role = 'member' WHERE session_id = ? AND principal = ?",
                    (session_id, actor))
            self._x("UPDATE agent_session_participant SET role = 'owner' WHERE session_id = ? AND principal = ?",
                    (session_id, to))
            self._x("UPDATE agent_session SET owner = ? WHERE id = ?", (to, session_id))

    def can_read(self, session_id: str, principal: str) -> bool:
        return self.role_of(session_id, principal) is not None

    def can_write(self, session_id: str, principal: str) -> bool:
        return self.role_of(session_id, principal) in ("owner", "member")

    # ── messages the agent was not asked to answer (ADR-0048 decision 5) ────────────────────
    #: A message longer than this is not a chat line.
    NOTE_MAX = 8000

    def add_note(self, session_id: str, author: str, text: str) -> dict:
        """Record a message posted to the session without an agent turn. Every participant reads it;
        the agent hears it on its next turn (``take_unheard``)."""
        text = (text or "").strip()
        if not text or len(text) > self.NOTE_MAX:
            raise ValueError("not a chat line")
        with self._tx():
            r = self._x("SELECT COALESCE(MAX(seq), 0) FROM agent_session_note WHERE session_id = ?",
                        (session_id,)).fetchone()
            seq = int(r[0] or 0) + 1
            now = time.time()
            self._x("INSERT INTO agent_session_note (session_id, seq, author, text, created, heard) "
                    "VALUES (?, ?, ?, ?, ?, 0)", (session_id, seq, author, text, now))
            self._x("UPDATE agent_session SET last_active = ? WHERE id = ?", (now, session_id))
        return {"seq": seq, "author": author, "text": text, "created": now}

    def notes(self, session_id: str, *, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._x("SELECT seq, author, text, created, heard FROM agent_session_note "
                           "WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                           (session_id, int(limit))).fetchall()
        return [{"seq": int(r[0]), "author": r[1], "text": r[2], "created": float(r[3]),
                 "heard": bool(r[4])} for r in reversed(rows)]

    def take_unheard(self, session_id: str) -> list[dict]:
        """The messages the agent has not heard yet, oldest first, marked heard."""
        with self._tx():
            rows = self._x("SELECT seq, author, text, created FROM agent_session_note "
                           "WHERE session_id = ? AND heard = 0 ORDER BY seq", (session_id,)).fetchall()
            self._x("UPDATE agent_session_note SET heard = 1 WHERE session_id = ? AND heard = 0",
                    (session_id,))
        return [{"seq": int(r[0]), "author": r[1], "text": r[2], "created": float(r[3])} for r in rows]

    # ── files attached to messages ───────────────────────────────────────────────────────────
    def add_attachments(self, session_id: str, poster: str, items: list[dict]) -> None:
        """Record what ``poster`` attached to a message in this session (``path`` on their desk)."""
        if not items:
            return
        now = time.time()
        with self._tx():
            for it in items:
                path = str(it.get("path") or "")
                if not path:
                    continue
                self._x("DELETE FROM agent_session_attachment WHERE session_id = ? AND path = ? "
                        "AND poster = ?", (session_id, path, poster))
                self._x("INSERT INTO agent_session_attachment (session_id, path, poster, name, created) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (session_id, path, poster, str(it.get("name") or "")[:200], now))

    def attachments(self, session_id: str) -> list[dict]:
        """Every file posted in this session, newest first: ``{path, poster, name}``."""
        with self._lock:
            rows = self._x("SELECT path, poster, name FROM agent_session_attachment WHERE session_id = ? "
                           "ORDER BY created DESC", (session_id,)).fetchall()
        return [{"path": r[0], "poster": r[1], "name": r[2]} for r in rows]

    def attachment_poster(self, session_id: str, path: str, *, prefer: str = "") -> Optional[str]:
        """Who posted ``path`` in this session (``prefer`` first when several did), or None."""
        with self._lock:
            rows = self._x("SELECT poster FROM agent_session_attachment WHERE session_id = ? AND path = ? "
                           "ORDER BY created", (session_id, path)).fetchall()
        posters = [r[0] for r in rows]
        if not posters:
            return None
        return prefer if prefer in posters else posters[0]

    # ── calls (ADR-0048 decision 6) ──────────────────────────────────────────────────────────
    _CALL_COLS = "session_id, room, meeting_id, owner, share_token, started, ended"

    @staticmethod
    def _call(r) -> Optional[dict]:
        if r is None:
            return None
        return {"session_id": r[0], "room": r[1], "meeting_id": int(r[2]), "owner": r[3],
                "share_token": r[4], "started": float(r[5]),
                "ended": float(r[6]) if r[6] is not None else None}

    def bind_call(self, session_id: str, *, room: str, meeting_id: int, owner: str, share_token: str) -> dict:
        """Record a call held in this session. Its starter must be able to write here."""
        if not self.can_write(session_id, owner):
            raise SessionDenied("not_a_member")
        now = time.time()
        with self._tx():
            self._x("DELETE FROM agent_session_call WHERE session_id = ? AND room = ?", (session_id, room))
            self._x(f"INSERT INTO agent_session_call ({self._CALL_COLS}) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (session_id, room, int(meeting_id), owner, share_token, now))
        return self.call(session_id, room)

    def call(self, session_id: str, room: str) -> Optional[dict]:
        with self._lock:
            return self._call(self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                                      "WHERE session_id = ? AND room = ?", (session_id, room)).fetchone())

    def calls_of(self, session_id: str) -> list[dict]:
        """Every call held in this session, oldest first (a room's calls, for its own agent)."""
        with self._lock:
            rows = self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                           "WHERE session_id = ? ORDER BY started", (session_id,)).fetchall()
        return [c for c in (self._call(r) for r in rows) if c]

    def active_call(self, session_id: str) -> Optional[dict]:
        """The session's current call: the earliest one not yet ended."""
        with self._lock:
            return self._call(self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                                      "WHERE session_id = ? AND ended IS NULL ORDER BY started LIMIT 1",
                                      (session_id,)).fetchone())

    def call_for_meeting(self, meeting_id) -> Optional[dict]:
        """The session a call (a meetings row) is held in — its latest binding."""
        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            return self._call(self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                                      "WHERE meeting_id = ? ORDER BY started DESC LIMIT 1", (mid,)).fetchone())

    def call_by_room(self, room: str) -> Optional[dict]:
        with self._lock:
            return self._call(self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                                      "WHERE room = ? ORDER BY started DESC LIMIT 1", (room,)).fetchone())

    # ── what the room's agent changed during a call (R1 1.5) ─────────────────────────────────
    CHANGE_BASE_MAX = 200_000
    _CHANGE_COLS = ("id, session_id, room, meeting_id, unit, on_behalf_of, workspace, path, base, "
                    "after_digest, at, undone_by, undone_at")

    def _change(self, r) -> Optional[dict]:
        if r is None:
            return None
        return {"id": r[0], "session_id": r[1], "room": r[2], "meeting_id": int(r[3] or 0) or None,
                "unit": r[4], "on_behalf_of": r[5] or "", "workspace": r[6], "path": r[7],
                "base": r[8], "after_digest": r[9], "at": float(r[10]), "undone_by": r[11] or "",
                "undone_at": float(r[12]) if r[12] is not None else None}

    def record_call_change(self, *, session_id: str, room: str, meeting_id, unit: str, on_behalf_of: str,
                           workspace: str, path: str, base: Optional[str], after_digest: str) -> dict:
        """One page write the room's agent made during this call. ``base`` is the page before
        (None: it did not exist), kept for the undo; past ``CHANGE_BASE_MAX`` it is not kept and the
        change cannot be undone here (a proposal can still restore it)."""
        cid = f"cc-{uuid.uuid4().hex[:16]}"
        if base is not None and len(base) > self.CHANGE_BASE_MAX:
            base = None
        with self._tx():
            self._x(f"INSERT INTO agent_session_call_change ({self._CHANGE_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (cid, session_id, room, int(meeting_id or 0) or None, unit, on_behalf_of or None,
                     workspace, path, base, after_digest, time.time()))
        return self.call_change(cid)

    def call_change(self, change_id: str) -> Optional[dict]:
        with self._lock:
            return self._change(self._x(f"SELECT {self._CHANGE_COLS} FROM agent_session_call_change "
                                        "WHERE id = ?", (change_id,)).fetchone())

    def call_changes(self, room: str) -> list[dict]:
        with self._lock:
            rows = self._x(f"SELECT {self._CHANGE_COLS} FROM agent_session_call_change WHERE room = ? "
                           "ORDER BY at", (room,)).fetchall()
        return [self._change(r) for r in rows]

    def undo_call_change(self, change_id: str, by: str) -> None:
        with self._tx():
            self._x("UPDATE agent_session_call_change SET undone_by = ?, undone_at = ? "
                    "WHERE id = ? AND undone_at IS NULL", (by, time.time(), change_id))

    def end_call(self, session_id: str, room: str) -> None:
        with self._tx():
            self._x("UPDATE agent_session_call SET ended = ? WHERE session_id = ? AND room = ? "
                    "AND ended IS NULL", (time.time(), session_id, room))

    def recent_calls(self, session_ids: "list[str]", *, limit: int = 50) -> list[dict]:
        """The calls held in any of these sessions, newest first — ended ones and live ones alike.
        One query, for the desktop's live and last call per workspace (``routers/desktop.py``)."""
        ids = [str(s) for s in session_ids if s][:500]
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call WHERE session_id IN ({marks}) "
                           "ORDER BY started DESC LIMIT ?", (*ids, int(limit))).fetchall()
        return [self._call(r) for r in rows]

    def call_by_meeting(self, meeting_id: int) -> Optional[dict]:
        with self._lock:
            return self._call(self._x(f"SELECT {self._CALL_COLS} FROM agent_session_call "
                                      "WHERE meeting_id = ? ORDER BY started LIMIT 1",
                                      (int(meeting_id),)).fetchone())

    # ── proposals after a call or meeting (ADR-0048 decision 7) ──────────────────────────────
    PROPOSAL_KINDS = ("page", "entity", "commitment", "note")
    PROPOSAL_BODY_MAX = 200_000
    _PROP_COLS = ("id, session_id, meeting_id, owner, kind, workspace, path, title, body, status, "
                  "decided_by, decided, created")

    @staticmethod
    def _proposal(r) -> Optional[dict]:
        if r is None:
            return None
        return {"id": r[0], "session_id": r[1], "meeting_id": int(r[2]) if r[2] is not None else None,
                "owner": r[3], "kind": r[4], "workspace": r[5], "path": r[6], "title": r[7],
                "body": r[8], "status": r[9], "decided_by": r[10],
                "decided": float(r[11]) if r[11] is not None else None, "created": float(r[12])}

    def propose(self, session_id: str, *, owner: str, kind: str, body: str, title: str = "",
                workspace: Optional[str] = None, path: Optional[str] = None,
                meeting_id: Optional[int] = None) -> dict:
        """Record a proposed change. ``owner`` decides it; nothing is written anywhere yet."""
        if kind not in self.PROPOSAL_KINDS:
            raise ValueError(f"kind must be one of {', '.join(self.PROPOSAL_KINDS)}")
        if not isinstance(body, str) or not body.strip() or len(body) > self.PROPOSAL_BODY_MAX:
            raise ValueError("not a proposal body")
        if kind in ("page", "entity"):
            if not workspace or not _NAME.fullmatch(workspace):
                raise ValueError("a page or entity proposal names its workspace")
            check_rel(path or "")
        if self.get(session_id) is None:
            raise KeyError(session_id)
        pid = f"p-{uuid.uuid4().hex[:16]}"
        with self._tx():
            self._x(f"INSERT INTO agent_session_proposal ({self._PROP_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, NULL, ?)",
                    (pid, session_id, meeting_id, owner, kind, workspace, path, title[:300], body,
                     time.time()))
        return self.proposal(pid)

    def proposal(self, proposal_id: str) -> Optional[dict]:
        with self._lock:
            return self._proposal(self._x(f"SELECT {self._PROP_COLS} FROM agent_session_proposal "
                                          "WHERE id = ?", (proposal_id,)).fetchone())

    def proposals(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._x(f"SELECT {self._PROP_COLS} FROM agent_session_proposal WHERE session_id = ? "
                           "ORDER BY created", (session_id,)).fetchall()
        return [self._proposal(r) for r in rows]

    def decide(self, proposal_id: str, actor: str, *, accept: bool, body: Optional[str] = None) -> dict:
        """The owner accepts (optionally with an edited body) or rejects an open proposal."""
        p = self.proposal(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if actor != p["owner"]:
            raise SessionDenied("not_meeting_owner")
        if p["status"] != "open":
            raise SessionDenied("already_decided")
        if body is not None and (not body.strip() or len(body) > self.PROPOSAL_BODY_MAX):
            raise ValueError("not a proposal body")
        with self._tx():
            self._x("UPDATE agent_session_proposal SET status = ?, decided_by = ?, decided = ?, "
                    "body = COALESCE(?, body) WHERE id = ? AND status = 'open'",
                    ("accepted" if accept else "rejected", actor, time.time(), body, proposal_id))
        return self.proposal(proposal_id)

    # ── transcripts (ADR-0048 decisions 2 and 3) ─────────────────────────────────────────────
    def head(self, session_id: str) -> int:
        s = self.get(session_id)
        if s is None:
            raise KeyError(session_id)
        return s["version"]

    def load_bundle(self, session_id: str, version: Optional[int] = None) -> dict:
        """``{version, files: {relpath: bytes}}`` — the head, or one named version. A session with
        no transcript yet is version 0 with no files."""
        with self._lock:
            if version is None:
                r = self._x("SELECT head_version, head_key FROM agent_session WHERE id = ?",
                            (session_id,)).fetchone()
                if r is None:
                    raise KeyError(session_id)
                version, key = int(r[0] or 0), r[1]
            else:
                r = self._x("SELECT object_key FROM agent_session_version WHERE session_id = ? AND version = ?",
                            (session_id, int(version))).fetchone()
                if r is None:
                    raise KeyError(f"{session_id}@{version}")
                key = r[0]
        if not key:
            return {"version": 0, "files": {}}
        raw = self._blobs.get(key)
        if raw is None:
            raise KeyError(f"{session_id}@{version}: object missing")
        return {"version": int(version), "files": decode_files(json.loads(raw).get("files"))}

    def save_bundle(self, session_id: str, base: int, files: dict) -> int:
        """Store ``files`` as the version after ``base``; returns the new version. Refused with
        ``SessionConflict`` when ``base`` is not the head."""
        size = sum(len(v) for v in files.values())
        if size > MAX_BUNDLE_BYTES:
            raise ValueError("transcript bundle over the size limit")
        if not files:
            raise ValueError("empty transcript bundle")
        encoded = encode_files(files)
        new = int(base) + 1
        key = f"sessions/{session_id}/v{new:08d}-{uuid.uuid4().hex[:12]}.json"
        body = json.dumps({"session": session_id, "version": new, "files": encoded}).encode("utf-8")
        self._blobs.put(key, body)
        try:
            with self._tx():
                cur = self._x("UPDATE agent_session SET head_version = ?, head_key = ?, last_active = ? "
                              "WHERE id = ? AND head_version = ?",
                              (new, key, time.time(), session_id, int(base)))
                if cur.rowcount != 1:
                    r = self._x("SELECT head_version FROM agent_session WHERE id = ?", (session_id,)).fetchone()
                    if r is None:
                        raise KeyError(session_id)
                    raise SessionConflict(session_id, int(base), int(r[0]))
                self._x("INSERT INTO agent_session_version (session_id, version, object_key, bytes, created) "
                        "VALUES (?, ?, ?, ?, ?)", (session_id, new, key, size, time.time()))
        except BaseException:
            try:
                self._blobs.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning("session %s: a refused version's object was not deleted", session_id)
            raise
        return new


def from_settings(settings) -> Optional[SessionStore]:
    """The store the deployment configured, or None (the store is off and chats keep the
    workspace layout). The database and an S3 endpoint are both required."""
    def _secret(name: str) -> str:
        v = getattr(settings, name, "")
        return (v.get_secret_value() if hasattr(v, "get_secret_value") else (v or "")).strip()

    db = _secret("sessions_db_url")
    host = (getattr(settings, "db_host", "") or "").strip()
    if not db and not host:
        return None
    endpoint = (getattr(settings, "sessions_s3_endpoint", "") or "").strip()
    if endpoint and "://" not in endpoint:
        endpoint = ("https://" if getattr(settings, "sessions_s3_secure", False) else "http://") + endpoint
    if not endpoint:
        # ADR-0038: transcripts go to S3 and nowhere else — no directory fallback.
        logger.error("session store: a database is configured but no S3 endpoint for transcripts; store off")
        return None
    if not (_secret("sessions_s3_access_key") and _secret("sessions_s3_secret_key")):
        # Never the SDK's ambient credential chain: the store's own scoped key, or the store is off.
        logger.error("session store: no VEXA_SESSIONS_S3_ACCESS_KEY / _SECRET_KEY (the store's own scoped key); store off")
        return None
    blobs: session_blobs.BlobStore = session_blobs.S3Blobs(
        endpoint=endpoint, bucket=getattr(settings, "sessions_s3_bucket", "") or "vexa-sessions",
        access_key=_secret("sessions_s3_access_key"), secret_key=_secret("sessions_s3_secret_key"),
        region=getattr(settings, "sessions_s3_region", "") or "us-east-1")
    if db:
        if db.startswith("sqlite:"):
            return SessionStore.open(db, blobs)
        u = urlparse(db)
        return SessionStore.open_postgres(blobs, host=u.hostname or "localhost", port=u.port or 5432,
                                          database=(u.path or "/").lstrip("/") or "vexa",
                                          user=unquote(u.username or ""), password=unquote(u.password or ""))
    password = _secret("sessions_db_password")
    if not password:
        logger.error("session store: DB_HOST is set but VEXA_SESSIONS_DB_PASSWORD is not; store off")
        return None
    return SessionStore.open_postgres(blobs, host=host, port=int(getattr(settings, "db_port", 5432) or 5432),
                                      database=getattr(settings, "db_name", "") or "vexa",
                                      user=(getattr(settings, "sessions_db_user", "") or "vexa_agent_sessions"),
                                      password=password)
