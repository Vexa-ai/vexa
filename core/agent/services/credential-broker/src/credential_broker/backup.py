"""Scheduled snapshots of the broker's state volume (`python -m credential_broker.backup`).

What a snapshot holds: `metadata.sqlite` (connections, grants, audit) and, with the local backend,
`secrets.sqlite` (the AES-256-GCM ciphertext of every stored credential). Each is copied with
SQLite's online backup API from a read-only connection, so the copy is one consistent point in
time while the broker keeps writing; a raw file copy of a live WAL database is not. The copy is
checked (`PRAGMA integrity_check`), switched to a single-file journal, and only then renamed into
place as `snapshot-<UTC time>/`. The newest `keep` snapshots stay; older ones are removed.

What it never touches: any key. The store key is mounted into the broker alone and this process
opens nothing but the two database files named in `FILES`; `state-hmac.key` (consents in flight
only) is left behind on purpose. A snapshot is therefore unreadable without the store key the
operator keeps elsewhere, and restoring one needs that same key.

    python -m credential_broker.backup --source /state --dest /backup --keep 28 --interval 21600
    python -m credential_broker.backup --source /state --dest /backup --keep 28 --once

Restore: README.md § Backup and restore.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from .obs import log_event

#: The only files a snapshot reads. Nothing else in the state volume is opened.
FILES = ("metadata.sqlite", "secrets.sqlite")
SNAPSHOT = re.compile(r"^snapshot-\d{8}T\d{6}Z$")
BUSY_TIMEOUT_S = 30


class BackupError(Exception):
    pass


def _stamp(now: Optional[float] = None) -> str:
    t = datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)
    return "snapshot-" + t.strftime("%Y%m%dT%H%M%SZ")


def _copy(src_path: Path, dst_path: Path) -> None:
    uri = "file:" + quote(str(src_path)) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_S)) as src, \
            closing(sqlite3.connect(dst_path)) as dst:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise BackupError(f"{src_path.name}: integrity_check {ok}")
        dst.execute("PRAGMA journal_mode=DELETE")       # the snapshot is one self-contained file
    dst_path.chmod(0o600)


def snapshot(source: Path, dest: Path, *, now: Optional[float] = None) -> Optional[Path]:
    """Write one snapshot of `source` into `dest`, or None when `source` holds no database yet."""
    source, dest = Path(source), Path(dest)
    present = [n for n in FILES if (source / n).is_file()]
    if not present:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / _stamp(now)
    if final.exists():
        raise BackupError(f"{final.name} exists")
    work = dest / (final.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o700)
    try:
        for name in present:
            _copy(source / name, work / name)
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return final


def prune(dest: Path, keep: int) -> list[str]:
    """Remove all but the newest `keep` snapshots (and any partial one a crash left)."""
    dest = Path(dest)
    if not dest.is_dir():
        return []
    removed = []
    for p in dest.iterdir():
        if p.is_dir() and p.name.endswith(".partial"):
            shutil.rmtree(p, ignore_errors=True)
    snaps = sorted((p for p in dest.iterdir() if p.is_dir() and SNAPSHOT.match(p.name)),
                   key=lambda p: p.name, reverse=True)
    for p in snaps[max(keep, 1):]:
        shutil.rmtree(p)
        removed.append(p.name)
    return removed


def run_once(source: Path, dest: Path, keep: int) -> bool:
    try:
        made = snapshot(source, dest)
        removed = prune(dest, keep)
    except Exception as e:  # noqa: BLE001 — logged by kind; the loop tries again next interval
        log_event("state_backup_failed", level="error",
                  fields={"error": f"{type(e).__name__}: {e}"[:300]})
        return False
    log_event("state_backup", fields={"snapshot": made.name if made else "", "pruned": removed,
                                      "kept": keep, "empty_source": made is None})
    return True


def main(argv: Optional[list] = None, *, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(prog="python -m credential_broker.backup")
    ap.add_argument("--source", default="/state", type=Path)
    ap.add_argument("--dest", default="/backup", type=Path)
    ap.add_argument("--keep", default=28, type=int)
    ap.add_argument("--interval", default=21600, type=int, help="seconds between snapshots")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    if a.keep < 1 or a.interval < 60:
        ap.error("--keep must be >= 1 and --interval >= 60")
    if a.source.resolve() == a.dest.resolve() or a.source.resolve() in a.dest.resolve().parents:
        ap.error("--dest must be outside --source (a separate volume)")
    if a.once:
        return 0 if run_once(a.source, a.dest, a.keep) else 1
    # The loop never exits on a failed snapshot: in the chart it runs beside the broker in its pod,
    # and a crashing container would take the broker out of its Service.
    while True:
        run_once(a.source, a.dest, a.keep)
        sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
