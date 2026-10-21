"""private_writes.py — the privacy hold on the files a turn writes itself (D6, SC4, H7).

agent-api holds writes from a session that read the person's private data into surfaces other people
read (``control_plane/private_taint.py``). A turn can also write pages with its own file tools,
straight onto its mounts, and those writes meet the platform only at the post-turn commit
(``ports.run_harness_turn``). This is the same rule at that seam:

* THE MARK IS PERSISTED, NOT THIS PROCESS'S (`shared/taint.py`). The gateway/MCP edge marks the unit
  and its conversation in Redis from the label on every private read; this worker reads that mark
  through its own Redis user (read-only on those keys, ``workload_redis``). Its own observations — a
  call to a personal MCP connector (any server but vexa) and, as a belt, the vexa tools that return
  private data — are written to the one taint key the worker may write, so a worker idle exit or a
  respawn forgets nothing. A mark that cannot be read counts as held (fail closed).
* at the commit, a marked session's changes to a mount whose readers are not only the person are NOT
  committed. The readers come from the roster as it was BEFORE the turn (``HEAD:policy/members.json``,
  joined with the work tree's) — a turn cannot shrink its own audience by editing the roster.
* WHAT IS HELD NEVER LANDS ON A DESK (H7). It is handed to agent-api by reference (a hand-back key
  of this unit, short TTL), which keeps it ENCRYPTED and off every mount (``control_plane/held_store``)
  for the person's share card; the mount is put back as it was, and the turn says what it withheld,
  from where, and the code that releases it. When the hand-off fails nothing is kept anywhere: the
  turn says so and the person asks again in their chat.
* when the person's message says ``confirm <code>``, agent-api writes the held files and commits
  them before the turn runs (``private_taint.release_confirmed_files``).
* a marked session's commit into a multi-reader mount carries a fixed body, never the agent's reply.

WHAT IS HELD IS WHAT CARRIES THE READ (friction 2, app.dev 2026-10-16). The harness adapter hands
the guard the DIGESTS of each private read's answer (``private_digests`` on its ``tool-result``,
``shared/private_fingerprint.py``; never the text, and never past ``run_harness_turn``), and only a
changed file whose added text carries one of them is held: a company page about CircleCI written
after a mail search commits. A read whose answer the worker did not see (a harness that does not
report it, a call with no result) holds the whole of the turn's change to that workspace, as before.

SAID INSIDE THE TURN (friction 1, same day: the agent told the person "saved" twice when nothing
was). The withheld line streams before the turn's ``done`` and rides on it (``reply``,
``withheld``); the next turn's prompt opens with what was not saved; and every turn of a marked
session is told the rule up front and pointed at ``workspace_write``, which refuses a held write at
once with the approval card instead of after the turn (``preamble``).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

from llm import workspace_paths as wpaths
from llm.gitexec import run_git
from shared.private_fingerprint import Fingerprint
from shared.taint import TTL_SEC

log = logging.getLogger(__name__)

HELD_DIR = ".vexa/held-writes"
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CONFIRM_RE = re.compile(r"\bconfirm\s+([a-z0-9]{6})\b", re.IGNORECASE)

#: vexa tools that return the person's private data — a BELT beside the edge's data label, which
#: is the source of truth (an unlabelled read is private there).
PRIVATE_VEXA_TOOLS = {
    "gmail_search": "mail", "gmail_read": "mail", "gmail_thread": "mail", "gmail_inbox": "mail",
    "mail_inbox": "mail", "mail_read": "mail",
    "calendar_events": "calendar", "secret_service_call": "connected service",
    "connections_status": "connections", "onboarding_research": "mail",
    "crm_read": "crm", "crm_search": "crm", "crm_history": "crm", "card_list": "crm", "card_read": "crm",
    "desktop_read": "crm", "company_context": "crm",
    "get_meeting_transcript": "transcript", "list_meetings": "transcript", "transcript_terms": "transcript",
    "search_transcripts": "transcript", "get_meeting_chat": "transcript",
    "people_followups": "person", "people_discover": "person", "customer_profile": "person",
    "home_history": "person", "notifications_list": "notifications",
    "chat_read": "chat", "chats_search": "chat", "ref_resolve": "refs",
}
#: Paths never held: runtime plumbing and the attachment drawer, which never commit anyway, and
#: `policy/`, which the policy guard owns. `.vexa/` IS held: the turn's commit takes it like any
#: page, so a marked turn's write there is a write every member reads.
_SKIP_PREFIXES = (".claude/", "uploads/", "policy/")


def private_source(tool: str) -> Optional[str]:
    """The private source a tool call reads, or None. ``mcp__<server>__<name>`` names its server."""
    t = str(tool or "")
    if not t.startswith("mcp__"):
        return None
    parts = t.split("__")
    if len(parts) < 3:
        return None
    server, name = parts[1], "__".join(parts[2:])
    if server != "vexa":
        return f"connected service ({server})"
    return PRIVATE_VEXA_TOOLS.get(name)


def _roster(raw: Optional[str], owner: str) -> list[str]:
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(m.get("email") or m.get("subject")) for m in (rows if isinstance(rows, list) else [])
            if isinstance(m, dict) and m.get("subject") and str(m.get("subject")) != str(owner)]


def other_readers(mount: Path, owner: str) -> list[str]:
    """Who besides ``owner`` reads ``mount``: the roster committed BEFORE the turn joined with the
    work tree's, so a turn that edits ``policy/members.json`` cannot shrink its own audience."""
    mount = Path(mount)
    head = None
    if wpaths.is_dir_inside(mount, ".git", allow=(".git",)):
        r = run_git(mount, "show", "HEAD:policy/members.json")
        head = r.stdout if r.returncode == 0 else None
    tree = wpaths.read_text_inside(mount, "policy/members.json", allow=("policy",))
    return sorted(set(_roster(head, owner)) | set(_roster(tree, owner)))


def _git(work: Path, *args: str) -> str:
    return run_git(work, *args, check=True).stdout


def _changed(work: Path) -> list[str]:
    """Every path the turn changed in ``work`` (tracked edits and deletions, new files)."""
    out = set()
    for blob in (_git(work, "diff", "--name-only", "-z", "HEAD"),
                 _git(work, "diff", "--cached", "--name-only", "-z", "HEAD"),
                 _git(work, "ls-files", "--others", "--exclude-standard", "-z")):
        out |= {p for p in blob.split("\0") if p}
    return sorted(p for p in out if not p.startswith(_SKIP_PREFIXES))


def _in_head(work: Path, rel: str) -> bool:
    return run_git(work, "cat-file", "-e", f"HEAD:{rel}").returncode == 0


_VEXA = (".vexa",)


#: How long a held write waits, by reference, for agent-api to take it into its encrypted store.
HANDBACK_TTL_SEC = 3600
#: The commit body a marked session's commit into a multi-reader mount carries (never the reply).
NEUTRAL_BODY = "agent turn (body withheld: this session read private data)"

_WORKER_STORE: list = []


def configure_worker(client, unit: str) -> None:
    """The worker's Redis (its own ACL user) and the unit it runs as — set once at worker start."""
    _WORKER_STORE.clear()
    if client is not None and unit:
        _WORKER_STORE.append((client, str(unit)))


class Guard:
    """One worker's view: which private data its session read (persisted), and where held writes go."""

    def __init__(self, owner: str, held_root: Optional[Path] = None, *, unit: str = "",
                 store=None, handback=None):
        # ``held_root`` is accepted for callers of the pre-0.13.4 shape and ignored: nothing held is
        # ever written to a desk again (H7).
        self.owner = str(owner or "")
        self.unit = str(unit or "")
        self.store = store                  # shared.taint.TaintStore over the worker's Redis, or None
        self.handback = handback            # (code, payload) -> ref | None
        self._marked: dict[str, float] = {}
        self._released: dict[str, set] = {}       # mount path -> paths the person released this turn
        self._fp = Fingerprint()                  # what the private reads gave the agent (digests)
        self._pending: dict[str, str] = {}        # call id of a private read still without its result
        self._unsaid: list[dict] = []             # held writes the next turn has not been told of yet
        # the sources whose every read THIS process saw with digests: a mark from the store this
        # worker did not observe (a respawn, an edge-only read) holds the whole change (FD3)
        self._fp_sources: set = set()

    @classmethod
    def from_env(cls, env: dict, mounts: list[dict]) -> "Guard":
        owner = str(env.get("VEXA_OWNER") or "")
        if not _WORKER_STORE:
            return cls(owner)
        client, unit = _WORKER_STORE[0]
        from shared import stream_bounds  # noqa: PLC0415
        from shared.taint import TaintStore  # noqa: PLC0415

        def handback(code: str, payload: dict) -> Optional[str]:
            key = stream_bounds.handback_key(unit, f"held-{code}")
            try:
                client.set(key, json.dumps(payload), ex=HANDBACK_TTL_SEC)
                return key
            except Exception:  # noqa: BLE001 — nothing kept; the turn says to ask again
                log.warning("held write %s could not be handed to agent-api", code)
                return None
        return cls(owner, unit=unit, store=TaintStore(client), handback=handback)

    # ── marking ──────────────────────────────────────────────────────────────────────────────────
    def observe(self, ev: dict) -> None:
        if ev.get("type") == "tool-call":
            found = private_source(str(ev.get("tool") or ""))
            if found:
                if not self.sources():
                    self._fp = Fingerprint()      # a fresh mark: what expired is forgotten with it
                    self._fp_sources = set()
                self._marked[found] = time.time()
                if self.store is not None and self.unit:
                    try:
                        self.store.mark(self.unit, [found], worker=True)
                    except Exception:  # noqa: BLE001 — the in-process mark still holds this worker
                        log.warning("privacy mark could not be persisted from the worker")
                self._pending[str(ev.get("callId") or "")] = found
        elif ev.get("type") == "tool-result":
            call = str(ev.get("callId") or "")
            if call in self._pending:
                found_for_call = self._pending.pop(call, None)
                digests = ev.get("private_digests")
                if isinstance(digests, list):
                    self._fp.add_digests(str(d) for d in digests)
                    self._fp_sources.add(found_for_call)
                else:
                    self._fp.add(None)            # the answer was not seen: hold every shared write

    def sources(self) -> list[str]:
        from shared.taint import UNVERIFIABLE  # noqa: PLC0415
        now = time.time()
        found = {k for k, at in self._marked.items() if now - at <= TTL_SEC}
        if self.store is not None and self.unit:
            try:
                found |= set(self.store.read(self.unit))
            except Exception:  # noqa: BLE001 — a mark that cannot be read is held
                found.add(UNVERIFIABLE)
        return sorted(found)

    def commit_message(self, mount: Path, msg: str) -> str:
        """A marked session's commit into a mount other people read carries a fixed body."""
        if self.sources() and other_readers(Path(mount), self.owner):
            return NEUTRAL_BODY
        return msg

    def _seen_all(self) -> bool:
        """Every private read's answer reached the fingerprint (else the whole change is held): no
        read without its digests, and every source the session is marked with (persisted, by any
        marker) was seen here with digests."""
        if not (self._fp.complete and not self._pending):
            return False
        return set(self.sources()) <= self._fp_sources

    def _carries(self, mount: Path, rel: str) -> bool:
        """Does the turn's change to ``rel`` add something a private read gave the agent? A
        deletion adds nothing."""
        if not wpaths.is_file_inside(mount, rel, allow=_VEXA):
            return False
        data = wpaths.read_bytes_inside(mount, rel, allow=_VEXA) or b""
        before = run_git(mount, "show", f"HEAD:{rel}").stdout if _in_head(mount, rel) else ""
        return bool(self._fp.carried(data.decode("utf-8", "replace"), before or ""))

    # ── saying it ───────────────────────────────────────────────────────────────────────────────
    def preamble(self, mounts: Iterable[Path]) -> str:
        """What a turn is told before it starts: the writes the last turn did not save (once), and,
        while the session is marked, which workspaces others read and how to write them."""
        lines: list[str] = []
        for held in self._unsaid:
            lines.append(f"Your last turn's file writes to {held['workspace']} "
                         f"({', '.join(held['paths'][:5])}) were NOT saved: they carried the person's "
                         f"private {' and '.join(held['sources'])}, and others read {held['workspace']}. "
                         f"They wait for the person's approval"
                         + (f" (code {held['code']})." if held.get("code") else ".")
                         + " Never say they were saved.")
        self._unsaid = []
        found = self.sources()
        shared = []
        if found:
            for m in mounts:
                m = Path(m)
                if wpaths.is_dir_inside(m, ".git", allow=(".git",)) and other_readers(m, self.owner):
                    shared.append(m.name)
        if shared:
            lines.append(
                f"This chat has read the person's private {' and '.join(found)}. Anything from it — a "
                f"name, an address, an amount, a number, a sentence — written with your file tools "
                f"into {', '.join(sorted(shared))} (other people read it) is NOT saved: it is held "
                f"for the person's approval when the turn ends. Write pages there with "
                f"workspace_write: it answers at once, and a held write shows the person an approval "
                f"card. Say a page is saved only when the write answered that it was.")
        return ("[platform] " + "\n[platform] ".join(lines) + "\n\n") if lines else ""

    # ── holding ──────────────────────────────────────────────────────────────────────────────────
    def hold(self, mount: Path) -> Optional[dict]:
        """Withhold a marked session's change to ``mount`` when others read it. Returns what was
        withheld (``{code, workspace, paths, readers, sources, ref}``) or None."""
        mount = Path(mount)
        if not wpaths.is_dir_inside(mount, ".git", allow=(".git",)):
            return None
        readers = other_readers(mount, self.owner)
        if not readers:
            return None
        released = self._released.get(str(mount), set())
        paths = [p for p in _changed(mount) if p not in released]
        if paths and self._seen_all():
            paths = [p for p in paths if self._carries(mount, p)]   # only what carries the reads
        if not paths:
            return None
        found = self.sources()
        if not found:
            return None
        files: dict[str, Optional[str]] = {}
        for rel in paths:
            data = (wpaths.read_bytes_inside(mount, rel, allow=_VEXA)
                    if wpaths.is_file_inside(mount, rel, allow=_VEXA) else None)
            files[rel] = base64.b64encode(data).decode() if data is not None else None
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        ref = None
        if self.handback is not None:
            ref = self.handback(code, {"code": code, "workspace": mount.name, "files": files,
                                       "sources": found, "at": time.time()})
        for rel in paths:
            run_git(mount, "reset", "-q", "HEAD", "--", rel)
            if _in_head(mount, rel):
                _git(mount, "checkout", "HEAD", "--", rel)
            else:
                wpaths.unlink_inside(mount, rel, allow=_VEXA)
        out = {"code": code if ref else "", "workspace": mount.name, "paths": paths,
               "readers": readers, "sources": found}
        if ref:
            out["ref"] = ref
        self._unsaid.append(out)
        return out

    def release_confirmed(self, text: str) -> list[dict]:
        """agent-api releases what the person confirmed, before the turn runs
        (``private_taint.release_confirmed_files``); nothing is kept here to release."""
        return []


def withheld_line(held: dict) -> str:
    """What the person reads at the end of the turn."""
    who = ", ".join(held["readers"][:5])
    pages = ", ".join(held["paths"][:5]) + (" …" if len(held["paths"]) > 5 else "")
    how = (f" Reply `confirm {held['code']}` to write it there." if held.get("code")
           else " Ask me again in a chat with your own desk mounted to write it.")
    return (f"\n\nWithheld from {held['workspace']}: {pages} — not saved, whatever this reply said "
            f"above. This chat read your private "
            f"{' and '.join(held['sources'])}, and {held['workspace']} is also read by {who}, so "
            f"nothing was written there.{how}")


def released_line(rows: Iterable[dict]) -> str:
    rows = list(rows)
    if not rows:
        return ""
    return "".join(f"Released to {r['workspace']} as you confirmed: {', '.join(r['paths'])}.\n" for r in rows)
