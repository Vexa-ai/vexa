"""atomic_json.py — the one way agent code replaces a JSON document on disk.

A reader of the file sees either the previous document or the new one, never a partial write: the
document goes to a private (0600) temporary file beside the target, is flushed and fsynced, then
``os.replace``d over it. A write that fails leaves the old document and no temporary behind.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: str | Path, value: Any, **dump_kwargs: Any) -> None:
    """Replace ``path`` with ``value`` serialised as JSON (``dump_kwargs`` go to ``json.dump``).
    The parent directory must exist."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, **dump_kwargs)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
