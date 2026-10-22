"""mount_audience.py — agent-api's writer of each running unit's audience (``shared/audience.py``).

The worker's commit hold counts who besides the person reads each mount. It is told at the dispatch
and on every turn, and that is as fresh as the turn. This module keeps it fresh to the commit:

* :func:`publish` — at every dispatch: the unit's readers (``private_taint.mount_readers``, the
  membership store) into its key, and the unit into the set of every shared workspace it mounts. A
  mount already named ``revoked`` stays named while this dispatch does not mount it again: the unit's
  running worker may still hold it, and it is what ends that worker (``worker.engine.RevokedMounts``).
* :func:`refresh` — the membership store's roster-change hook (``workspace_membership``
  ``set_roster_listener``): every unit mounting that workspace gets its readers re-read at once, and
  a mount its subject no longer holds by membership is named ``revoked``. A unit whose roster cannot
  be re-read is written ``unknown`` (every mount held), never left with the old audience.

Never raises: a key that could not be written leaves the worker on the turn's own stamp (as before)
or, when a refresh failed after a dispatch wrote it, on ``unknown``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from shared import audience

log = logging.getLogger("agent_api.mount_audience")

#: The mount role a shared workspace's membership grants (``workspace_attach.SHARED_ROLE``).
MEMBER_MOUNT = "shared"


def _membership_mounts(root: Path, mounts: list) -> list[dict]:
    """``[{path, workspace, write}]`` for every mount that is a shared workspace held by membership."""
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415

    out = []
    for m in mounts or []:
        if not isinstance(m, dict) or m.get("role") != MEMBER_MOUNT or not m.get("path"):
            continue
        p = Path(str(m["path"]))
        if p.parent.resolve() != Path(root).resolve() or not wpaths.is_workspace_name(p.name):
            continue
        out.append({"path": str(p), "workspace": p.name, "write": bool(m.get("write", True))})
    return out


def _revoked(root: Path, subject: str, held: list[dict]) -> list[str]:
    """The mounts ``subject`` no longer holds: not a member any more, or a viewer of a mount it writes.
    Raises ``MembershipError`` when a roster cannot be read."""
    from control_plane import workspace_membership as membership  # noqa: PLC0415

    out = []
    for m in held:
        role = membership.is_member(Path(root), m["workspace"], subject)
        if role is None or (m["write"] and role not in ("contributor", "owner")):
            out.append(m["path"])
    return out


def publish(r, root: Path, unit: str, subject: str, mounts: list, readers: Optional[dict]) -> None:
    """Write ``unit``'s audience as the dispatch read it, and index it under its shared workspaces."""
    if r is None or not unit:
        return
    held = _membership_mounts(root, mounts) if subject else []
    record = {"subject": str(subject), "mounts": [{"path": str(m.get("path")), "role": m.get("role"),
                                                   "write": bool(m.get("write", True))}
                                                  for m in mounts or [] if isinstance(m, dict) and m.get("path")]}
    still_revoked = _carried(r, unit, record["mounts"])
    try:
        pipe = r.pipeline()
        pipe.set(audience.unit_key(unit), audience.encode(readers if isinstance(readers, dict) else {},
                                                          revoked=still_revoked,
                                                          unknown=not isinstance(readers, dict)),
                 ex=audience.TTL_SEC)
        pipe.set(_record_key(unit), json.dumps(record), ex=audience.TTL_SEC)
        for m in held:
            pipe.sadd(audience.index_key(m["workspace"]), unit)
            pipe.expire(audience.index_key(m["workspace"]), audience.TTL_SEC)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — the worker then holds on the turn's own stamp
        log.warning("audience of %s not published (%s)", unit, type(exc).__name__)


def _carried(r, unit: str, mounts: list) -> set[str]:
    """The mounts ``unit``'s key names ``revoked`` that ``mounts`` no longer includes: the unit's
    running worker may still hold them, so they stay named until it ends."""
    paths = {str(Path(str(m.get("path")))) for m in mounts or [] if isinstance(m, dict) and m.get("path")}
    try:
        prior = audience.decode(r.get(audience.unit_key(unit))) or {}
    except Exception:  # noqa: BLE001 — the key is rewritten either way; only the carried mark is lost
        return set()
    return {p for p in prior.get("revoked") or set() if p not in paths}


def _record_key(unit: str) -> str:
    """What agent-api keeps to re-read a unit's audience: its subject and its mounts (never the worker's)."""
    return f"{audience.PREFIX}:record:{unit}"


def refresh(r, root: Path, workspace_id: str) -> int:
    """``workspace_id``'s roster changed: re-read the audience of every unit that mounts it. Returns
    how many units were rewritten."""
    if r is None or not workspace_id:
        return 0
    from control_plane import private_taint  # noqa: PLC0415
    from control_plane.workspace_membership import MembershipError  # noqa: PLC0415

    try:
        units = sorted(x.decode() if isinstance(x, bytes) else str(x)
                       for x in (r.smembers(audience.index_key(workspace_id)) or []))
    except Exception as exc:  # noqa: BLE001
        log.error("audience of %s's units not refreshed: the index could not be read (%s)",
                  workspace_id, type(exc).__name__)
        return 0
    done = 0
    for unit in units:
        try:
            raw = r.get(_record_key(unit))
        except Exception:  # noqa: BLE001
            raw = None
        if not raw:
            try:
                r.srem(audience.index_key(workspace_id), unit)
            except Exception:  # noqa: BLE001
                pass
            continue
        mounts: list = []
        try:
            rec = json.loads(raw)
            subject, mounts = str(rec.get("subject") or ""), list(rec.get("mounts") or [])
            readers = private_taint.mount_readers(Path(root), mounts, subject)
            revoked = _revoked(Path(root), subject, _membership_mounts(root, mounts))
            value = audience.encode(readers, revoked=[*revoked, *_carried(r, unit, mounts)])
        except (MembershipError, ValueError, TypeError) as exc:
            log.warning("audience of %s unknown after %s changed (%s): every mount held",
                        unit, workspace_id, type(exc).__name__)
            value = audience.encode({}, revoked=_carried(r, unit, mounts), unknown=True)
        try:
            r.set(audience.unit_key(unit), value, ex=audience.TTL_SEC)
            done += 1
        except Exception as exc:  # noqa: BLE001
            log.error("audience of %s not refreshed after %s changed (%s)", unit, workspace_id,
                      type(exc).__name__)
    return done
