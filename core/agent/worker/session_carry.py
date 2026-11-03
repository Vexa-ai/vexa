"""session_carry.py — the worker's half of ADR-0048 decision 3: a conversation is handed in, lives
outside every mount for the turn, and is handed back.

agent-api puts a session's current transcript bundle on the turn's input entry (``session_bundle``:
``{id, version, files: {relpath: base64}}``). ``Carry.receive`` materialises it into a private
directory under the worker's own HOME — never under ``/workspace`` or any mount — and from then on
that directory is the continuity root every harness writes under (``engine._continuity_root``):
claude-code's ``.claude/projects``, codex's ``.claude/codex/sessions``, openai-agent's transcript,
the resume pointer and the turn records. After the turn ``bundle_event`` collects the directory and
returns the ``session-bundle`` output event agent-api saves as the next version.

NAMES. The pointer and the turn records are named after the chat's local session name
(``<session>.session``), which differs per participant: the owner may call a thread ``main`` and a
second participant addresses it by its store id. The bundle therefore carries them under one
canonical name (``CANONICAL``) and this module maps it on the way in and back on the way out.

ORDER. A delivery older than what this worker already holds is ignored: the worker that just sent
version N+1 must not be wound back by a turn whose bundle was read before that save landed. A newer
delivery replaces the local copy whole. The exception is a delivery marked ``replace``: agent-api
sets it when the store refused this conversation's last hand-back, so the local copy is ahead of a
head that never took it. Then the delivery wins, whatever its version, and the next hand-back is
based on the head it carries.

FALLBACK, for one release. A session the store holds nothing for yet (version 0, no files) adopts
the old layout from the previous continuity root — this session's pointer, turn records and the
transcripts named by its id — so the first save after an upgrade carries the whole conversation.

Nothing here follows a link: every read and write goes through ``workspace_paths``.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger("agent-worker.session_carry")

CANONICAL = "conversation"
_PLUMBING = (".claude",)
_SESSIONS = ".claude/sessions"
_REL = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._@+=-]*(/[A-Za-z0-9._-][A-Za-z0-9._@+=-]*)*$")
#: A bundle larger than this is not collected (the store's own bound is higher; the resume budget
#: restarts a thread long before either).
MAX_COLLECT_BYTES = 48 << 20
_SUFFIXES = (".session", ".turns.jsonl")
#: Where the files posted in the conversation land for a turn (``shared/attachments.py`` STORED).
ATTACHMENTS = "attachments"
_ATTACHMENT = re.compile(r"^(assets|uploads)/[0-9a-f]{16}-[A-Za-z0-9._-]{1,160}$")


def default_root() -> Path:
    """Where the conversation lives during a turn: the worker's own HOME, outside every mount."""
    return Path(os.environ.get("HOME", "/root")) / ".vexa-session"


def _plain(rel: str) -> bool:
    return bool(_REL.fullmatch(rel or "")) and not any(p in (".", "..") for p in rel.split("/"))


class Carry:
    def __init__(self, root: "Path | None" = None, *, session: str) -> None:
        self.root = Path(root) if root is not None else default_root()
        self.session = session
        self.session_id: Optional[str] = None
        self.version = 0
        self.active = False
        #: the newest version agent-api has handed this worker — a `replace` older than it is a
        #: queued delivery dispatched before that hand-over, never the head to wind back to
        self.received = 0
        self._digest: Optional[str] = None

    # ── names ────────────────────────────────────────────────────────────────────────────────
    def _to_local(self, rel: str) -> str:
        for suf in _SUFFIXES:
            if rel == f"{_SESSIONS}/{CANONICAL}{suf}":
                return f"{_SESSIONS}/{self.session}{suf}"
        return rel

    def _to_canonical(self, rel: str) -> Optional[str]:
        """The bundle name for a local file, or None for a file that is not this session's."""
        if rel.startswith(_SESSIONS + "/") and rel.count("/") == 2:
            for suf in _SUFFIXES:
                if rel == f"{_SESSIONS}/{self.session}{suf}":
                    return f"{_SESSIONS}/{CANONICAL}{suf}"
            return None
        return rel

    # ── the files posted in the conversation ───────────────────────────────────────────────────
    def attachment_path(self, rel: str) -> Optional[str]:
        """Where a file posted in this conversation is for this turn, or None. Never collected back
        into the bundle: it lives outside ``.claude`` and agent-api hands it in again every turn."""
        if not self.active or not _ATTACHMENT.fullmatch(rel or ""):
            return None
        if not wpaths.is_file_inside(self.root, f"{ATTACHMENTS}/{rel}"):
            return None
        return str(self.root / ATTACHMENTS / rel)

    def _receive_attachments(self, files) -> None:
        if wpaths.is_dir_inside(self.root, ATTACHMENTS):
            wpaths.remove_tree(self.root / ATTACHMENTS)
        for rel, b64 in (files or {}).items() if isinstance(files, dict) else ():
            if not _ATTACHMENT.fullmatch(rel or ""):
                log.warning("session bundle: an attachment path was refused")
                continue
            try:
                wpaths.write_bytes_inside(self.root, f"{ATTACHMENTS}/{rel}", base64.b64decode(b64), mode=0o600)
            except (ValueError, OSError, wpaths.PathRefused) as exc:
                log.warning("session bundle: an attachment was not materialised (%s)", type(exc).__name__)

    # ── in ───────────────────────────────────────────────────────────────────────────────────
    def receive(self, bundle: dict, *, legacy_root: "Path | None" = None) -> None:
        if not isinstance(bundle, dict) or not bundle.get("id"):
            return
        try:
            self._receive_files(bundle, legacy_root=legacy_root)
        finally:
            if self.active and bundle["id"] == self.session_id:
                self._receive_attachments(bundle.get("attachments"))

    def _receive_files(self, bundle: dict, *, legacy_root: "Path | None" = None) -> None:
        try:
            version = int(bundle.get("version") or 0)
        except (TypeError, ValueError):
            return
        same = bundle["id"] == self.session_id
        if bundle.get("replace") is True and self.active and same and version < self.received:
            log.info("session bundle v%d (replace) is older than v%d already handed; ignored",
                     version, self.received)
            return
        if bundle.get("replace") is True and self.active and same and version != self.version:
            log.info("session bundle v%d replaces the local v%d the store did not take",
                     version, self.version)
        elif self.active and same and version < self.version:
            log.info("session bundle v%d is older than the local v%d; kept the local copy",
                     version, self.version)
            return
        elif self.active and same and version == self.version and bundle.get("replace") is not True:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        wpaths.clear_tree_inside(self.root)
        files = bundle.get("files") or {}
        for rel, b64 in files.items():
            if not _plain(rel):
                log.warning("session bundle: a path was refused")
                continue
            try:
                data = base64.b64decode(b64)
                wpaths.write_bytes_inside(self.root, self._to_local(rel), data, mode=0o600,
                                          allow=_PLUMBING)
            except (ValueError, OSError, wpaths.PathRefused) as exc:
                log.warning("session bundle: a file was not materialised (%s)", type(exc).__name__)
        if version == 0 and not files and legacy_root is not None:
            self._adopt_legacy(Path(legacy_root))
        self.received = max(self.received, version) if same else version
        self.session_id, self.version, self.active = bundle["id"], version, True
        self._digest = self._hash(self.collect()) if files else None

    def _adopt_legacy(self, legacy: Path) -> None:
        name = f"{self.session}.session"
        text = wpaths.read_text_inside(legacy, f"{_SESSIONS}/{name}", max_bytes=1 << 16, allow=_PLUMBING)
        if text is None and self.session == "main":
            text = wpaths.read_text_inside(legacy, ".claude/.session", max_bytes=1 << 16, allow=_PLUMBING)
        if text is None:
            return
        sid = text.strip()
        wanted = [f"{_SESSIONS}/{name}", f"{_SESSIONS}/{self.session}.turns.jsonl"]
        if sid and _plain(sid):
            for slug in wpaths.list_dirs_inside(legacy, ".claude/projects", allow=_PLUMBING):
                wanted.append(f".claude/projects/{slug}/{sid}.jsonl")
            for rel in wpaths.walk_files_inside(legacy, ".claude/codex/sessions", allow=_PLUMBING):
                if sid in rel.rsplit("/", 1)[-1]:
                    wanted.append(rel)
        copied = 0
        for rel in wanted:
            raw = wpaths.read_bytes_inside(legacy, rel, allow=_PLUMBING)
            if raw is None:
                continue
            try:
                wpaths.write_bytes_inside(self.root, rel, raw, mode=0o600, allow=_PLUMBING)
                copied += 1
            except (OSError, wpaths.PathRefused):
                continue
        if text is not None and not wpaths.is_file_inside(self.root, f"{_SESSIONS}/{name}", allow=_PLUMBING):
            wpaths.write_text_inside(self.root, f"{_SESSIONS}/{name}", text, mode=0o600, allow=_PLUMBING)
        log.info("session %s: adopted %d file(s) from the old layout", self.session, copied)

    # ── out ──────────────────────────────────────────────────────────────────────────────────
    def collect(self) -> dict:
        out: dict = {}
        total = 0
        if not self.root.is_dir():
            return out
        for rel in wpaths.walk_files_inside(self.root, ".claude", allow=_PLUMBING):
            canon = self._to_canonical(rel)
            if canon is None or not _plain(canon) or rel.rsplit("/", 1)[-1].startswith("."):
                continue
            raw = wpaths.read_bytes_inside(self.root, rel, allow=_PLUMBING)
            if raw is None:
                continue
            total += len(raw)
            if total > MAX_COLLECT_BYTES:
                log.warning("session %s: transcript over the collect limit; not handed back", self.session)
                return {}
            out[canon] = raw
        return out

    @staticmethod
    def _hash(files: dict) -> str:
        h = hashlib.sha256()
        for k in sorted(files):
            h.update(k.encode() + b"\0" + hashlib.sha256(files[k]).digest())
        return h.hexdigest()

    def bundle_event(self) -> Optional[dict]:
        """The ``session-bundle`` event for what changed since the last hand-over, or None."""
        if not self.active or not self.session_id:
            return None
        files = self.collect()
        if not files:
            return None
        digest = self._hash(files)
        if digest == self._digest:
            return None
        ev = {"type": "session-bundle", "session_id": self.session_id, "base": self.version,
              "files": {k: base64.b64encode(v).decode("ascii") for k, v in files.items()}}
        self._digest = digest
        self.version += 1
        return ev


# ── the scratch folder of a shared turn with no workspace in common (ADR-0048 decision 4) ──────
_SCRATCH = ".vexa-scratch"
_ONE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def shared_scratch(session: str) -> Path:
    """This session's own working folder under the worker's HOME, made if absent. A turn in a shared
    chat whose participants share no writable workspace works here — never on anyone's desk, and
    never in a folder another session uses."""
    if not _ONE.fullmatch(session or "") or session in (".", ".."):
        raise ValueError("not a session name")
    home = Path(os.environ.get("HOME", "/root"))
    fd = wpaths.dir_fd_inside(home, (_SCRATCH, session), create=True, mode=0o700)
    os.close(fd)
    return home / _SCRATCH / session


def discard(path: "Path | None") -> None:
    """Remove a scratch or carry folder when the worker is done with it. Never follows a link."""
    if path is None:
        return
    try:
        wpaths.remove_tree(path, ignore_errors=True)
    except OSError:
        log.warning("a session folder was not removed")
