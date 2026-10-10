"""private_dir.py — where a worker keeps a per-dispatch secret: outside every mount, readable only by
the user that needs it.

A worker's working directory is usually a mounted workspace — a desk, a shared workspace, ``_global``
— that other people read through agent-api's file routes and that every turn commits. A secret
written under it (the vexa MCP attachment, which carries the worker's delegation token) can be read
by everyone who can read that workspace. So a per-dispatch secret goes in a directory made for it:

* under this process's own temporary directory (``tempfile.gettempdir()``: the container's private
  ``/tmp`` on docker and Kubernetes, the child's own on Lite), never under a mount — a temporary
  directory that turns out to be inside one of the mounts it is given is refused;
* with an unguessable name, mode 0700 — and 0711 when the harness runs as a separate tools user, so
  that user can open the one file it is told the name of, and can neither list the directory nor put
  anything in it. Each file written there is 0600 and given to the user that reads it
  (``llm.ports.hand_fd_to_tools``);
* removed when the process exits.

This module imports no product code; the caller passes the mounts to refuse and the reader's uid.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Tuple


class NotPrivate(RuntimeError):
    """No directory outside every mount could be made for a secret."""


def _inside(path: Path, roots: Iterable[Path]) -> Optional[Path]:
    for root in roots:
        try:
            root = Path(root).resolve()
        except OSError:
            continue
        if path == root or root in path.parents:
            return root
    return None


def make(*, refuse_under: Iterable[os.PathLike] = (), reader: Optional[Tuple[int, int]] = None,
         prefix: str = "vexa-secrets-") -> Path:
    """A new private directory for this process's secrets, or :class:`NotPrivate`.

    ``refuse_under`` — the mounts the worker was given (its workspace, its continuity root, every
    active mount): the directory must not be inside any of them. ``reader`` — the ``(uid, gid)`` of
    a separate user that must open a file in it (the harness's tools user), or None when the
    harness runs as this process's own user."""
    base = Path(tempfile.gettempdir()).resolve()
    roots = [Path(r) for r in refuse_under if r]
    hit = _inside(base, roots)
    if hit is not None:
        raise NotPrivate(f"the temporary directory {base} is inside the mount {hit}")
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(base)))        # 0700, this process's own
    atexit.register(shutil.rmtree, str(path), True)
    if reader is not None and reader[0] != os.geteuid():
        os.chmod(path, 0o711)                                          # traverse, never list
    return path
