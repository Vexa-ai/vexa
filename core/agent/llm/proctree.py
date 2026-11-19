"""proctree.py — a harness turn is ONE process tree, and stopping the turn stops all of it.

Live app.dev, 2026-10-15 (user 2, chat pchat-mv9n3xc1): the model ran a catastrophic `grep` over
megabytes of transcripts. The CLI's 120 s Bash timeout did not kill it — the CLI moved it to the
background — and the person's Stop only closed the browser's request. The `grep` (a child of the
CLI's `bash -l`) ran on for 14+ minutes, the turn never completed, its session lease never released,
and every later message was refused with a 409 until an operator killed the pid by hand.

So the CLI is started in a session of its own (``start_new_session``) and killing a turn means:
every process descended from the CLI, every process still in the CLI's session or in the session
or process group of any descendant (a shell the CLI started ``detached`` has its own), each frozen
first (SIGSTOP, so nothing forks a replacement while the list is read) and then killed (SIGKILL).
Linux ``/proc`` only; anywhere else it falls back to the CLI's own process group.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from typing import Iterable

logger = logging.getLogger("llm.proctree")


def _table() -> dict[int, tuple[int, int, int, str]]:
    """pid → (ppid, pgid, sid, state) for every live process this process can see."""
    out: dict[int, tuple[int, int, int, str]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                raw = fh.read().decode("ascii", "replace")
        except OSError:
            continue
        # the command name is in parentheses and may itself hold spaces or parentheses
        rest = raw[raw.rfind(")") + 2:].split()
        if len(rest) < 4:
            continue
        try:
            out[int(name)] = (int(rest[1]), int(rest[2]), int(rest[3]), rest[0])
        except ValueError:
            continue
    return out


def members(root: int, *, sids: Iterable[int] = ()) -> set[int]:
    """Every live (non-zombie) process of ``root``'s turn: its descendants, and whatever shares a
    session or process group with ``root`` or with any of them. ``root`` itself included if alive."""
    table = _table()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _pg, _sid, _st) in table.items():
        children.setdefault(ppid, []).append(pid)
    tree, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in tree:
            continue
        tree.add(p)
        todo.extend(children.get(p, ()))
    groups, sessions = {root}, {root, *sids}
    for p in tree:
        if p in table:
            groups.add(table[p][1])
            sessions.add(table[p][2])
    # never our own group or session: a child that was NOT started in a session of its own shares
    # them with this worker, and "kill its session" would then mean "kill the worker"
    me = os.getpid()
    for own in (os.getpgid(0), os.getsid(0)):
        groups.discard(own)
        sessions.discard(own)
    if 1 in table:
        groups.discard(table[1][1])
        sessions.discard(table[1][2])
    found = {p for p, (_pp, pg, sid, st) in table.items()
             if (p in tree or pg in groups or sid in sessions) and st != "Z"}
    found.discard(me)
    found.discard(1)
    return found


def _killpg_unless_ours(pgid: int) -> None:
    try:
        if pgid in (os.getpgid(0), os.getsid(0)):
            return
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_tree(root: int, *, sids: Iterable[int] = ()) -> int:
    """Kill ``root``'s whole turn (see ``members``); the number of processes signalled. Repeats
    until nothing is left (a process forked between two reads is caught on the next pass)."""
    total: set[int] = set()
    for _ in range(6):
        pids = members(root, sids=sids) if os.path.isdir("/proc") else set()
        if not pids:
            _killpg_unless_ours(root)   # no /proc (or nothing visible): the CLI's own group
            break
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for p in pids:
                try:
                    os.kill(p, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        _killpg_unless_ours(root)
        total |= pids
        time.sleep(0.05)
    if total:
        logger.info("turn process tree of %d killed: %d process(es)", root, len(total))
    return len(total)
