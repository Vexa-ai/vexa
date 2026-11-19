"""migrate.py — one-shot copy of a workspace volume into the bucket, with verification.

    python -m workspaces.store.migrate --source /workspaces [--verify-only] [--allow-uncommitted]

Run with agent-api stopped (scaled to 0): the volume must not change underneath. The source is never
written (no marker files). Every repository is published as a full bundle (``replace``) and every
platform file copied; then a fresh, empty cache is hydrated from the bucket and compared with the
source repository by repository: the same refs, the same HEAD tree, the same number of commits, and
platform files byte for byte. Uncommitted changes in a source repository are not git state and
would not survive: they are listed and fail the run unless ``--allow-uncommitted``.

Exit status 0 only when everything verified. The report is one JSON object on stdout.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from workspaces.shared import gitexec
from workspaces.shared import workspace_paths as wpaths

from .boot import StoreMisconfigured, s3_settings
from .lock import WorkspaceLocks
from .objects import S3Objects, self_test
from .observer import transient
from .repostore import RepoStore, local_refs, scan_cache

log = logging.getLogger("workspaces.store.migrate")


def _git(repo, *args):
    return gitexec.run_git(str(repo), *args, check=True).stdout.strip()


def _fingerprint(repo: Path) -> dict:
    refs, head = local_refs(repo)
    if not refs:
        return {"refs": {}, "head": head, "tree": "", "commits": 0}
    return {"refs": refs, "head": head, "tree": _git(repo, "rev-parse", "HEAD^{tree}"),
            "commits": int(_git(repo, "rev-list", "--count", "--branches", "--tags"))}


def migrate(source: Path, objects, locks, *, verify_only: bool = False,
            allow_uncommitted: bool = False) -> dict:
    repos, files = scan_cache(source)
    repos = [r for r in repos if not transient(r)]
    report: dict = {"source": str(source), "repos": len(repos), "files": len(files),
                    "uncommitted": {}, "mismatches": [], "published": 0, "copied": 0}
    store = RepoStore(objects=objects, locks=locks, cache_root=source, markers=False)
    for rel in repos:
        dirty = gitexec.run_git(str(source / rel), "status", "--porcelain", check=True,
                                env={"GIT_OPTIONAL_LOCKS": "0"}).stdout.splitlines()
        if dirty:
            report["uncommitted"][rel] = dirty[:20]
    if report["uncommitted"] and not allow_uncommitted:
        report["ok"] = False
        report["error"] = ("uncommitted changes in source repositories would be lost; commit them or "
                           "pass --allow-uncommitted")
        return report
    if not verify_only:
        for rel in repos:
            with locks.hold(rel) as held:
                if store.publish(rel, held=held, replace=True) is not None:
                    report["published"] += 1
        for rel in files:
            data = wpaths.read_bytes_inside(source, rel)
            if data is not None:
                store.put_file(rel, data)
                report["copied"] += 1
    with tempfile.TemporaryDirectory(prefix="wsstore-verify-") as tmp:
        fresh = RepoStore(objects=objects, locks=locks, cache_root=Path(tmp))
        fresh.hydrate_all()
        for rel in repos:
            want = _fingerprint(source / rel)
            if not want["refs"]:
                continue                       # an empty repository has nothing to carry
            got = _fingerprint(Path(tmp) / rel) if (Path(tmp) / rel).is_dir() else None
            if got != want:
                report["mismatches"].append({"repo": rel, "want": want, "got": got})
        for rel in files:
            if wpaths.read_bytes_inside(source, rel) != wpaths.read_bytes_inside(tmp, rel):
                report["mismatches"].append({"file": rel})
    report["ok"] = not report["mismatches"]
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m workspaces.store.migrate")
    ap.add_argument("--source", default=os.environ.get("VEXA_WORKSPACES_DIR", "/workspaces"))
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--allow-uncommitted", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        settings = s3_settings()
    except StoreMisconfigured as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    objects = S3Objects(settings)
    probe = self_test(objects, where=f"workspace store {settings.endpoint or 'AWS'}/{settings.bucket}")
    if not probe.ok:
        print(json.dumps({"ok": False, "error": probe.error}))
        return 2
    redis_client = None
    if os.environ.get("REDIS_URL"):
        import redis  # noqa: PLC0415

        redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    report = migrate(Path(args.source), objects, WorkspaceLocks(redis_client),
                     verify_only=args.verify_only, allow_uncommitted=args.allow_uncommitted)
    report["self_test"] = probe.as_dict()
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
