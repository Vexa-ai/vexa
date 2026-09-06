"""jobs.py — a long act that does NOT hold the chat (Vexa-ai/vexa#1584).

On 2026-09-06 the founder pressed Create and Extend four times in the minutes panel. The agent read,
searched and wrote for 30-120 seconds each time — 38 tool calls across the four acts — and the
composer was busy for the whole of it: he could not ask anything until each act landed. The act was
never the problem. Running it INSIDE the turn was.

A JOB is agent work that runs outside the turn loop: the turn returns at once with one short line,
the job runs on its own thread with its own harness session and its own step count, and its result
arrives later as a line in the chat and a page that refreshed itself.

WHY IT LIVES HERE AND NOT IN AN ADAPTER. The runner sits ABOVE the harness — it calls the same
turn-shaped function `serve` already injects — so it is ONE implementation for every runner rather
than a feature written three times that stays right in one of them. `claude-code`'s native subagent
tool was considered and rejected for the same reason it cannot be the contract: it runs to
completion inside the turn, which is the behaviour this file exists to remove.

The whole contract — the event vocabulary, the reader that stays open, what a job does and does not
share with its chat — is `llm/JOBS.md`.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Callable, Iterator, Optional

log = logging.getLogger("agent_api.worker")

#: How the lines read. A job's whole visible surface is three sentences, so they are written here
#: rather than composed at three call sites — and never by a model: the acknowledgement is the one
#: thing that must arrive before any model has been asked anything.
_VERBS: dict[str, tuple[str, str]] = {
    "create": ("Writing", "written"),
    "extend": ("Extending", "extended"),
    # The same press, on a passage of a transcript (Vexa-ai/vexa#1596). Its target reads
    # `meeting 41 · “…”`, so the lines come out "Extending meeting 41 · “…” — I'll say when it's
    # there." and "meeting 41 · “…” — extended."
    "extend_transcript": ("Extending", "extended"),
}
_DEFAULT_VERBS = ("Working on", "done")


def _verbs(kind: str) -> tuple[str, str]:
    return _VERBS.get((kind or "").strip().lower(), _DEFAULT_VERBS)


def started_line(kind: str, target: str) -> str:
    return f"{_verbs(kind)[0]} {target} — I'll say when it's there."


def done_line(kind: str, target: str) -> str:
    return f"{target} — {_verbs(kind)[1]}."


def failed_line(kind: str, target: str, why: str) -> str:
    detail = (why or "").strip().splitlines()[0][:200] if why else ""
    return f"{_verbs(kind)[0]} {target} failed{f': {detail}' if detail else '.'}"


def refused_line(kind: str, target: str) -> str:
    return f"There is already something running on {target} — I'll finish that one first."


def restarted_line(kind: str, target: str) -> str:
    return f"{_verbs(kind)[0]} {target} stopped when the agent restarted — ask again and I'll redo it."


class JobRunner:
    """The worker's background jobs: spawn, refuse a duplicate, emit progress, tell the chat.

    ``emit`` is the worker's own XADD onto ``unit:<id>:out`` — jobs ride the channel turns already
    ride, which is what makes progress reach the terminal with no second relay. ``turn`` is the
    job's turn function; ``serve`` injects one built with ``session_continuity=False`` so a job
    never writes the chat's transcript (two writers on one transcript is the same failure as two
    writers on one page).

    ``register_dir`` makes "a restart cancels them and the chat is told" true rather than hoped: a
    file per running job, removed when it ends, scanned at boot. Without one the promise is a
    comment.

    ``session`` IS THE JOB'S OWNER, AND IT IS LOAD-BEARING (Vexa-ai/vexa#1613). The register lives
    under the subject's continuity root, which every one of that person's chats shares — so a
    worker booting for chat B read chat A's LIVE jobs out of it, told B's reader they had died, and
    deleted A's records on the way past. Measured on the dogfood stack 2026-09-06: job
    ``j-58b3833e`` started and failed in ``pchat-mtppgd4w`` and was reported a second time, as a
    restart casualty, on ``pchat-mtpphl4o``; ``j-192ed731`` did the same across ``meet-147`` and
    ``pchat-mtpthvmp``. The founder met it as *"some leak to empty chat"*.

    So every record carries the session that owns it, every event says which session it belongs to,
    and the boot scan reports — and deletes — only its own. A foreign record is left exactly
    where it is, for the worker that owns it.
    """

    def __init__(self, *, emit: Callable[[dict], None], turn: Callable[[str], Iterator[dict]],
                 register_dir: Optional[Path] = None, session: str = "") -> None:
        self._emit = emit
        self._turn = turn
        self._dir = Path(register_dir) if register_dir else None
        self._session = str(session or "")
        self._lock = threading.Lock()
        self._running: dict[str, str] = {}          # target → job_id
        self._threads: list[threading.Thread] = []

    def _own(self, ev: dict) -> dict:
        """Stamp the owning session onto an event. Every job event carries it, so a reader that is
        looking at a DIFFERENT conversation can drop it without having to know anything else."""
        if self._session:
            ev["session"] = self._session
        return ev

    # -- lifecycle ---------------------------------------------------------------------------
    def spawn(self, kind: str, target: str, brief: str, *, turn_id: str = "") -> dict:
        """Start a job and return the event that was emitted — ``job-started`` or ``job-refused``.

        ONE JOB PER TARGET, refused rather than queued: two agents writing one file is the failure
        `graph/sg/Operating-Loops.md` names in a line, and a queue would mean pressing Extend twice
        costs four minutes before the first answer."""
        key = (target or "").strip() or (kind or "job")
        with self._lock:
            if key in self._running:
                ev = self._own({"type": "job-refused", "kind": kind, "target": target,
                                "line": refused_line(kind, target)})
                if turn_id:
                    ev["turn_id"] = turn_id
                self._emit(ev)
                return ev
            job_id = f"j-{uuid.uuid4().hex[:8]}"
            self._running[key] = job_id
        self._note(job_id, kind, target)
        ev = self._own({"type": "job-started", "job_id": job_id, "kind": kind, "target": target,
                        "line": started_line(kind, target)})
        if turn_id:
            ev["turn_id"] = turn_id
        self._emit(ev)
        th = threading.Thread(target=self._run, args=(job_id, key, kind, target, brief),
                              daemon=True, name=f"job-{job_id}")
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(th)
        th.start()
        return ev

    def busy(self) -> bool:
        """Is any job still running? The serve loop asks before it lets an idle read reap the
        container — a job is a thread in THIS process, so exiting under one is killing it."""
        with self._lock:
            return bool(self._running)

    def join_all(self) -> None:
        """Block until every job has finished — called on every way ``serve`` can return, the same
        discipline ``_join_trailers`` already applies to the write-back trailer."""
        while True:
            with self._lock:
                th = next((t for t in self._threads if t.is_alive()), None)
            if th is None:
                return
            th.join()

    def cancelled_at_boot(self) -> list[dict]:
        """Every job the last process was running when it died, told to the chat as a failure.

        Jobs survive nothing, by construction — but a chip that spins forever is worse than a job
        that stopped, so the register's leftovers are read once at boot, reported and deleted.

        OURS ONLY (Vexa-ai/vexa#1613). The directory is shared by every chat this person has, and a
        record belonging to another conversation is very often a job that is RUNNING RIGHT NOW in
        it. Reporting one here says a live job died, in a chat that never asked for it, and deleting
        it takes the real owner's ability to report it when it does die. Both were happening; the
        founder saw the lines land in a brand-new empty chat.

        So a record is OURS when it names this session — and, when this runner was given no session
        at all, when it is the only runner there is: an unnamed runner has no second conversation to
        confuse itself with, so it behaves exactly as it did before this field existed. A record
        naming somebody else is left where it is. A record naming nobody, read by a runner that has
        a name, is from an older build: it is cleaned up in silence, because there is nothing
        truthful to say about whose chat it belonged to."""
        out: list[dict] = []
        if self._dir is None or not self._dir.exists():
            return out
        for path in sorted(self._dir.glob("*.json")):
            try:
                rec = json.loads(path.read_text())
            except (OSError, ValueError):
                rec = {}
            owner = str(rec.get("session") or "")
            if self._session and owner != self._session:
                if owner:
                    continue                   # another chat's job — not ours to report or remove
                self._drop(path)               # pre-owner record: cleaned up, never announced
                continue
            self._drop(path)
            kind, target = str(rec.get("kind") or ""), str(rec.get("target") or "")
            ev = self._own({"type": "job-failed", "job_id": str(rec.get("job_id") or path.stem),
                            "kind": kind, "target": target, "line": restarted_line(kind, target)})
            self._emit(ev)
            out.append(ev)
        return out

    # -- the job itself ----------------------------------------------------------------------
    def _run(self, job_id: str, key: str, kind: str, target: str, brief: str) -> None:
        ok, why = True, ""
        try:
            for ev in self._turn(brief):
                # TAGGED WITH THE JOB, NEVER THE TURN. A job's tool calls are the JOB's step count;
                # a consumer that keys on `turn_id` (the terminal's reducer does) must not fold them
                # into the chat turn that has already been answered.
                self._emit(self._own({**ev, "job_id": job_id}))
                if ev.get("type") == "done":
                    ok = ev.get("ok", True) is not False
                    why = str(ev.get("reason") or ev.get("reply") or "")
        except Exception as exc:  # noqa: BLE001 — a job that dies says it died; never silence
            log.warning("job %s (%s %s) failed: %s: %s", job_id, kind, target,
                        type(exc).__name__, exc)
            ok, why = False, f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._running.pop(key, None)
            self._forget(job_id)
            self._emit(self._own(
                {"type": "job-done" if ok else "job-failed", "job_id": job_id,
                 "kind": kind, "target": target, "ok": ok,
                 "line": done_line(kind, target) if ok else failed_line(kind, target, why)}))

    # -- the on-disk register ----------------------------------------------------------------
    def _note(self, job_id: str, kind: str, target: str) -> None:
        if self._dir is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / f"{job_id}.json").write_text(
                json.dumps({"job_id": job_id, "kind": kind, "target": target,
                            "session": self._session}))
        except OSError as exc:
            log.warning("could not record job %s (%s) — a restart will not report it", job_id, exc)

    def _drop(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _forget(self, job_id: str) -> None:
        if self._dir is None:
            return
        try:
            (self._dir / f"{job_id}.json").unlink(missing_ok=True)
        except OSError:
            pass
