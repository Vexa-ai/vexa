"""The org, as agent-api resolves it: org → its policy source, and the org a workspace belongs to.

Architecture decision A1 (one org per install for now). Identity is the one writer of orgs
(admin-api `orgs`, MIGRATION-0008); this module only RESOLVES, and with one org every answer is the
answer the install gave before orgs existed:

- ``policy_root(settings, root, org)`` is where an org's policy tier is. Every reader of the company
  layer (`system_mounts.global_root`) goes through it, so the default org resolves to the install's
  `_global` exactly as before. A second org with its own tier is a DATA change (its
  ``policy_source`` in identity, ``_orgs/<id>``), not a refactor of the readers.
- ``workspace_org(record)`` is the org a workspace belongs to: the ``org`` its record carries, else
  the default org. Every workspace that exists today carries none, so the backfill is this default:
  no file in any workspace repository is rewritten to say so.
- An org this install does not know resolves to NOTHING (``UnknownOrg``), never to the default org's
  tier: one org's policies are never served as another's.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("control_plane.orgs")

DEFAULT_ORG_ID = "default"

#: The install's company layer — the default org's policy source.
INSTALL_POLICY_SOURCE = "_global"
#: Where a per-org policy tier will live inside the store (architecture A1: `<store>/_orgs/<org>`).
ORGS_DIRNAME = "_orgs"

_ORG_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class UnknownOrg(LookupError):
    """An org this install has no policy source for. Never answered with the default org's."""


#: org id → policy source. The default org's is the install's `_global`. Later this is read from
#: identity's `orgs.policy_source` (`GET /internal/orgs/{id}`); until a second org exists there is
#: nothing to read, and a network call on every policy read would be the only behaviour change.
_SOURCES: dict[str, str] = {DEFAULT_ORG_ID: INSTALL_POLICY_SOURCE}


def is_org_id(value) -> bool:
    return isinstance(value, str) and bool(_ORG_ID.match(value))


def policy_source(org_id: Optional[str] = None) -> str:
    """The org's policy source: ``_global`` or ``_orgs/<org_id>``. Raises :class:`UnknownOrg`."""
    org = org_id or DEFAULT_ORG_ID
    source = _SOURCES.get(org)
    if source is None:
        raise UnknownOrg(org)
    return source


def set_policy_source(org_id: str, source: str) -> None:
    """Record an org's policy source (identity's ``orgs.policy_source``). Only the two shapes are
    accepted: the install tier, or the org's own ``_orgs/<org_id>`` — never another org's folder,
    never a path that leaves the store."""
    if not is_org_id(org_id):
        raise ValueError(f"{org_id!r} is not an org id")
    if source != INSTALL_POLICY_SOURCE and source != f"{ORGS_DIRNAME}/{org_id}":
        raise ValueError(f"{source!r} is not a policy source for org {org_id!r}")
    _SOURCES[org_id] = source


def forget_org(org_id: str) -> None:
    if org_id != DEFAULT_ORG_ID:
        _SOURCES.pop(org_id, None)


def policy_root(settings, root: "str | Path | None" = None, org_id: Optional[str] = None) -> Path:
    """WHERE an org's policy tier is, on this service's filesystem.

    The default org (and any org whose source is the install tier) → the install's `_global`,
    resolved by `system_mounts.install_global_root` exactly as every reader did before orgs. An org
    with its own tier → ``<store>/_orgs/<org_id>``. An unknown org raises :class:`UnknownOrg`."""
    source = policy_source(org_id)
    from control_plane import system_mounts  # noqa: PLC0415 — system_mounts resolves through here

    if source == INSTALL_POLICY_SOURCE:
        return system_mounts.install_global_root(settings, root)
    store = Path(root if root is not None else settings.workspaces_dir)
    return store / ORGS_DIRNAME / (org_id or DEFAULT_ORG_ID)


def workspace_org(record: Optional[dict]) -> str:
    """The org a workspace belongs to: the ``org`` on its record, else the default org."""
    org = str((record or {}).get("org") or "").strip()
    return org or DEFAULT_ORG_ID


def same_org(viewer_org: Optional[str], record: Optional[dict]) -> bool:
    """Is this workspace in the viewer's org? ``None`` (no org named) is the default org."""
    return (viewer_org or DEFAULT_ORG_ID) == workspace_org(record)


# ── WHO IS IN WHICH ORG: identity answers, agent-api asks (the org boundary that enforces) ────────
#
# `workspace_ids.access_for` asks THIS for the viewer's org whenever a caller does not name one, so
# every access check — every current caller and every future one — applies the org boundary
# without having to remember to. With one org per install every answer is the default org and no
# access answer changes; an individual account (identity's org of one, `p-<id>`) reads only its own
# workspaces. Identity is asked `GET /internal/users/{id}/org`, cached; when it cannot answer and
# nothing is cached, the answer is the default org — today's behaviour — because an identity that
# cannot answer has already stopped the edge from admitting anyone (every request is validated
# there first), so this fallback is never the only gate.

TTL_S = 300.0
TIMEOUT_S = 2.0


class OrgDirectory:
    def __init__(self, base: str, secret: str, *, ttl: float = TTL_S, fetch=None, clock=time.monotonic):
        self._base = base.rstrip("/")
        self._secret = secret
        self._ttl = ttl
        self._fetch = fetch or self._http
        self._clock = clock
        self._cache: dict[str, tuple[float, str]] = {}

    def _http(self, subject: str) -> dict:
        req = urllib.request.Request(f"{self._base}/internal/users/{subject}/org",
                                     headers={"X-Internal-Secret": self._secret})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode() or "{}")

    def org_of(self, subject: str) -> str:
        now = self._clock()
        hit = self._cache.get(subject)
        if hit and hit[0] > now:
            return hit[1]
        try:
            org = str(((self._fetch(subject) or {}).get("org") or {}).get("id") or "") or DEFAULT_ORG_ID
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as e:
            org = hit[1] if hit else DEFAULT_ORG_ID
            logger.warning("orgs: identity did not answer the org of subject=%s (%s); using %s",
                           subject, type(e).__name__, org)
        self._cache[subject] = (now + self._ttl, org)
        return org


_directory: Optional[OrgDirectory] = None


def configure_directory(directory: Optional[OrgDirectory]) -> None:
    global _directory
    _directory = directory


def configure_from_settings(settings) -> None:
    """Wire the directory when identity is reachable (``admin_api_url`` + the internal secret)."""
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    try:
        secret = settings.internal_api_secret.get_secret_value()
    except Exception:  # noqa: BLE001
        secret = ""
    configure_directory(OrgDirectory(url, secret) if url and secret else None)


def _subject_id(subject) -> Optional[str]:
    s = str(subject or "").strip()
    return s if s.isdigit() else None


def viewer_org(subject) -> Optional[str]:
    """The viewer's org, or None when there is no directory or the subject is not a person."""
    sid = _subject_id(subject)
    if _directory is None or sid is None:
        return None
    return _directory.org_of(sid)


def record_org(record: Optional[dict]) -> str:
    """The org a workspace belongs to, for the access check: the ``org`` its record carries; else,
    for a desk or a tree in a person's private store, its OWNER's org (an individual's own desk is in
    their org of one); else the default org."""
    explicit = str((record or {}).get("org") or "").strip()
    if explicit and explicit != DEFAULT_ORG_ID:
        return explicit
    owner = _subject_id((record or {}).get("owner"))
    if _directory is not None and owner is not None and (record or {}).get("kind") == "desk":
        return _directory.org_of(owner)
    return explicit or DEFAULT_ORG_ID
