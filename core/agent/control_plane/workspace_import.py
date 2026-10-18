"""Durable, subject-scoped repository import jobs shared by HTTP and MCP clients.

The caller injects the import operation; this module owns status, serialization and
retry identity. Credentials stay in the operation closure, never in a job receipt.
"""
from __future__ import annotations

import contextvars
import fcntl
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from control_plane.repo_ref import normalize
from control_plane.workspace_attach import _safe_subject_dir
from shared.atomic_json import write_json_atomic
from shared.git_redaction import redact


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(root: str | Path, subject: str) -> Path:
    _safe_subject_dir(Path(root), subject)
    path = Path(root) / '.imports' / subject
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def ssh_form(url: str) -> str | None:
    """The deploy-key (SSH) form of a GitHub https URL, or None for any other URL."""
    if url.startswith('https://github.com/'):
        return url.replace('https://github.com/', 'git@github.com:', 1)
    return None


def repository_url(repo: str, *, use_deploy_key: bool) -> str:
    """The URL an import clones FIRST.

    A GitHub https URL becomes its SSH form only when this import uses a deploy key on purpose —
    it named the workspace whose key to reuse (``credential_workspace``). Merely HAVING a key does
    not: a public repository must still import over https for a person who once minted one. When an
    https clone is then refused for want of a credential, the route retries over SSH with the key
    (``ssh_form``)."""
    url = normalize(repo)
    if use_deploy_key:
        return ssh_form(url) or url
    return url


def workspace_slug(repo: str, ref: str) -> str:
    """Separate imported roots from legacy wrapped slots; different refs stay independent."""
    name = repo.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')
    name = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')[:40] or 'repository'
    digest = hashlib.sha256(f'{repo}\n{ref}'.encode()).hexdigest()[:12]
    return f'{name}-{digest}'


def status(root: str | Path, subject: str, operation_id: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{32}', operation_id):
        raise HTTPException(404, 'Unknown import operation')
    store = _store(root, subject)
    path = store / f'{operation_id}.json'
    if not path.exists():
        raise HTTPException(404, 'Unknown import operation')
    result = json.loads(path.read_text())
    if result['status'] in ('queued', 'running'):
        with (store / 'lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return result
            # The worker may have completed between our first read and lock acquisition.
            result = json.loads(path.read_text())
            if result['status'] not in ('queued', 'running'):
                return result
            # No worker owns this operation. Do not silently retry an uncertain mutation.
            result.update(status='interrupted', updated_at=_now(),
                          error='Import was interrupted. Check the workspace before retrying.')
            write_json_atomic(path, result)
    return result


def start(root: str | Path, subject: str, repo: str, ref: str,
          operation: Callable[[], dict]) -> dict:
    store = _store(root, subject)
    operation_id = hashlib.sha256(f'{repo}\n{ref}'.encode()).hexdigest()[:32]
    path = store / f'{operation_id}.json'
    lock = (store / 'lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        if path.exists():
            return json.loads(path.read_text())
        raise HTTPException(409, 'Another repository import is running. Wait for it to finish.')
    if path.exists():
        previous = json.loads(path.read_text())
        if previous['status'] in ('completed', 'interrupted', 'running', 'queued'):
            lock.close()
            return status(root, subject, operation_id)
    job = {'operation_id': operation_id, 'status': 'queued', 'repo': repo, 'ref': ref,
           'created_at': _now(), 'updated_at': _now()}
    write_json_atomic(path, job)

    def run():
        try:
            job.update(status='running', updated_at=_now())
            write_json_atomic(path, job)
            result = operation()
            job.update(status='completed', result=result, updated_at=_now())
        except HTTPException as exc:
            job.update(status='failed', error=redact(str(exc.detail)),
                       error_status=exc.status_code, updated_at=_now())
        except Exception:
            job.update(status='failed', error='Repository import failed. Check the workspace before retrying.',
                       error_status=500, updated_at=_now())
        finally:
            try:
                write_json_atomic(path, job)
            finally:
                lock.close()

    # The request's context travels with the work, so the Git credential reads it makes still
    # act for the person who asked (broker_client.ForwardedIdentity).
    worker = threading.Thread(target=contextvars.copy_context().run, args=(run,),
                              name='workspace-import', daemon=True)
    try:
        worker.start()
    except Exception:
        lock.close()
        raise
    return {**job}
