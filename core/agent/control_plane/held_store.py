"""held_store.py — what the privacy hold withheld, kept encrypted in agent-api and off every mount (D6, H7).

A write from a marked session into a surface other people read is held (`private_taint`). The held
content used to sit on the person's desk under ``.vexa/held-writes/`` as base64, and a post-meeting
run mounts other attendees' desks read-only — so the exact content that was withheld was readable by
somebody else's run (H7). It now lives here: in agent-api's Redis, AES-GCM encrypted under a key
derived from the deployment's internal secret, bound to the person and the code (associated data),
with a TTL. Nothing of it is on a desk, so no mount can carry it.

Who reads it: the person, from their own signed-in session (``GET /api/held``, the share card's
source), and agent-api itself when the person confirms a held write (``confirm <code>``) and the
platform writes it on their behalf. A worker never reads it back.

Fails closed: without a store or a secret the content is not kept at all — the hold still holds, and
the person is told to ask again in their chat (the model still has what it wanted to write).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("agent_api.held_store")

#: How long a held item waits for the person.
HELD_TTL_SEC = 3 * 86400
PREFIX = "vexa:held"
#: The legacy on-desk location (pre-0.13.4). Swept off every desk it is found on.
LEGACY_DIR = ".vexa/held-writes"
_INFO = b"vexa-held-writes.v1"


def item_key(subject: str, code: str) -> str:
    return f"{PREFIX}:{subject}:{code}"


def index_key(subject: str) -> str:
    return f"{PREFIX}:index:{subject}"


class HeldStore:
    def __init__(self, client, secret: str, *, ttl: int = HELD_TTL_SEC) -> None:
        if not secret:
            raise ValueError("a held store needs the internal secret")
        self.client = client
        self.ttl = int(ttl)
        self._key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
            secret.encode("utf-8"))

    def _aad(self, subject: str, code: str) -> bytes:
        return f"{subject}\x00{code}".encode("utf-8")

    def put(self, subject: str, code: str, *, surface: str, sources, payload: dict,
            kind: str = "egress") -> bool:
        """Keep ``payload`` (whatever the surface needs to release it) for the person. Returns
        whether it was kept."""
        subject, code = str(subject), str(code)
        nonce = os.urandom(12)
        body = json.dumps({"payload": payload, "sources": sorted(sources or [])}).encode("utf-8")
        ct = AESGCM(self._key).encrypt(nonce, body, self._aad(subject, code))
        row = json.dumps({"v": 1, "code": code, "surface": str(surface), "kind": str(kind),
                          "at": time.time(), "nonce": base64.b64encode(nonce).decode(),
                          "ct": base64.b64encode(ct).decode()})
        try:
            pipe = self.client.pipeline()
            pipe.set(item_key(subject, code), row, ex=self.ttl)
            pipe.sadd(index_key(subject), code)
            pipe.expire(index_key(subject), self.ttl)
            pipe.execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("held write %s not kept (%s)", code, type(exc).__name__)
            return False

    def _open(self, subject: str, code: str, raw) -> Optional[dict]:
        try:
            row = json.loads(raw)
            body = AESGCM(self._key).decrypt(base64.b64decode(row["nonce"]), base64.b64decode(row["ct"]),
                                             self._aad(subject, code))
            inner = json.loads(body)
        except Exception:  # noqa: BLE001 — tampered, foreign or corrupt: not this person's
            return None
        return {"code": code, "surface": row.get("surface"), "kind": row.get("kind"), "at": row.get("at"),
                "sources": inner.get("sources") or [], "payload": inner.get("payload") or {}}

    def get(self, subject: str, code: str) -> Optional[dict]:
        try:
            raw = self.client.get(item_key(str(subject), str(code)))
        except Exception:  # noqa: BLE001
            return None
        return self._open(str(subject), str(code), raw) if raw else None

    def list(self, subject: str) -> list[dict]:
        try:
            codes = sorted(self.client.smembers(index_key(str(subject))) or [])
        except Exception:  # noqa: BLE001
            return []
        out = []
        for code in codes:
            code = code.decode() if isinstance(code, bytes) else str(code)
            item = self.get(subject, code)
            if item is None:
                try:
                    self.client.srem(index_key(str(subject)), code)
                except Exception:  # noqa: BLE001
                    pass
                continue
            out.append(item)
        return sorted(out, key=lambda r: r.get("at") or 0, reverse=True)

    def discard(self, subject: str, code: str) -> bool:
        try:
            n = self.client.delete(item_key(str(subject), str(code)))
            self.client.srem(index_key(str(subject)), str(code))
            return bool(n)
        except Exception:  # noqa: BLE001
            return False

    def take(self, subject: str, code: str) -> Optional[dict]:
        item = self.get(subject, code)
        if item is not None:
            self.discard(subject, code)
        return item


_STORE: list = []


def configure(store: Optional[HeldStore]) -> None:
    _STORE.clear()
    if store is not None:
        _STORE.append(store)


def current() -> Optional[HeldStore]:
    return _STORE[0] if _STORE else None


def summary(item: dict) -> dict:
    """What the person's list shows: the surface, where, when and why — the content only on open."""
    p = item.get("payload") or {}
    return {"code": item["code"], "surface": item.get("surface"), "kind": item.get("kind"),
            "at": item.get("at"), "sources": item.get("sources") or [],
            "workspace": p.get("workspace"), "target": p.get("target") or p.get("path"),
            "paths": sorted((p.get("files") or {}).keys()) if isinstance(p.get("files"), dict) else None}


def migrate_desk(desk: Path, subject: str) -> int:
    """Move legacy ``.vexa/held-writes/*.json`` off a desk: into the encrypted store when there is
    one, and DELETED from the desk either way (privacy over convenience — the model still has what
    it wanted to write, and the person can ask again). Returns how many files left the desk."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    allow = (".vexa",)
    if not wpaths.is_dir_inside(desk, LEGACY_DIR, allow=allow):
        return 0
    store = current()
    moved = 0
    for name in wpaths.list_files_inside(desk, LEGACY_DIR, suffix=".json", allow=allow):
        rel = f"{LEGACY_DIR}/{name}"
        try:
            row = json.loads(wpaths.read_text_inside(desk, rel, allow=allow) or "null")
        except (OSError, ValueError, wpaths.PathRefused):
            row = None
        if store is not None and isinstance(row, dict) and row.get("code"):
            store.put(subject, str(row["code"]), surface="page", kind="files", sources=row.get("sources") or [],
                      payload={"workspace": row.get("workspace"), "files": row.get("files") or {}})
        try:
            if wpaths.unlink_inside(desk, rel, allow=allow):
                moved += 1
        except (OSError, wpaths.PathRefused):
            logger.warning("legacy held write could not be removed from a desk")
    return moved


def sweep_root(root: Path) -> int:
    """Every desk directly under the workspaces root, swept of legacy held writes (agent-api boot)."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415
    n = 0
    for name in wpaths.list_dirs_inside(root, ""):
        n += migrate_desk(Path(root) / name, name)
    return n
