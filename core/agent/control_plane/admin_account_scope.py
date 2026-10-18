"""admin_account_scope.py — what the ADMIN ACCOUNT may not do, declared once and enforced once.

Founder ruling 2026-10-11: *"let's change so that admin is a special account that has global only and
this being the only one that has control to global"*. The admin account (identity's answer,
``global_layer.is_admin_account``) is the deployment's dedicated account: its chats work on `_global`
and nothing else. Three seams make that true, and this module is the third:

  1. the MOUNT TABLE — ``dispatch.build_mount_set`` gives it `_global` read-write and `_system`, and
     returns before the active set, a room or a scaffold can add anything;
  2. the WORKSPACE DOOR — ``api._read_target`` resolves no slug for it but `_global` and `_system`,
     so its own (non-existent) desk and every other workspace are refused on every read and write;
  3. THE DOORS THAT WOULD GIVE IT ONE — this list: creating, importing, activating or joining a
     workspace, and every meeting surface. Refused here, before the handler, as an app-level
     dependency (the same shape as ``route_policy.PERSON_GATE``), so no route has to remember it.

Its bots are refused by identity, not here: its ``max_concurrent`` is always 0
(admin-api ``app/admin_account.py``), and meeting-api refuses a spawn at 0.

FAILS CLOSED AT BOOT: a template this app does not serve is a typo that would leave a door open, so
``assert_served`` refuses the boot naming it. A route not listed here is NOT thereby allowed to reach
a workspace — seam 2 still applies to it.
"""
from __future__ import annotations

from typing import Callable, FrozenSet, Iterable, Optional, Tuple

from fastapi import HTTPException, Request

RouteKey = Tuple[str, str]

#: The one sentence every refusal carries, so the terminal and an agent read the same thing.
REFUSAL = ("the admin account works on the company layer (_global) only — it has no desk, no shared "
           "workspaces and no meetings. Sign in with your everyday account for those.")

REFUSED: FrozenSet[RouteKey] = frozenset({
    # a workspace of its own, or one it would join
    ("POST", "/api/workspace/new"),
    ("POST", "/api/workspace/create"),
    ("POST", "/api/workspace/templates/save"),
    ("POST", "/api/workspace/init"),
    ("POST", "/api/workspace/import"),
    ("POST", "/api/workspace/activate"),
    ("POST", "/api/workspace/swap"),
    ("POST", "/api/workspace/move"),
    ("POST", "/api/workspace/shared/new"),
    ("POST", "/api/workspace/shared/{workspace_id}/attach"),
    ("POST", "/api/workspace/shared/{workspace_id}/active"),
    ("POST", "/api/workspace/{slug}/share-enable"),
    ("POST", "/api/workspace/invites"),
    ("POST", "/api/workspace/invites/accept"),
    # (`POST /api/workspace/invite` and `/membership` are NOT here: the admin account owns no
    # workspace, so `membership_acts.assert_may_manage` refuses it every one but `_global` — and for
    # `_global` it answers 409 with what the company layer does instead: its editors live in
    # POLICIES.md, not in a member list.)
    ("GET", "/api/workspace/desk"),
    ("POST", "/api/desk/touch"),
    # meetings
    ("GET", "/api/meeting/note"),
    ("POST", "/api/meeting/note"),
    ("POST", "/api/meeting/note/attach"),
    ("GET", "/api/meeting/stream"),
    ("GET", "/api/meeting/terms"),
    ("POST", "/api/meeting/terms"),
    ("POST", "/api/meeting/terms/scan"),
})


class ScopeError(Exception):
    """A declaration agent-api must not boot with."""


def assert_served(routes: Iterable, refused: FrozenSet[RouteKey] = REFUSED) -> None:
    """Every refused (method, template) must be a route this app serves — or the boot is refused."""
    from control_plane.route_policy import served_keys   # one reading of an app's route table
    served = served_keys(routes)
    missing = sorted(k for k in refused if k not in served)
    if missing:
        raise ScopeError("admin_account_scope names routes this app does not serve (a typo leaves the "
                         f"door open): {missing}")


def _matched(request: Request) -> Optional[RouteKey]:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return (request.method.upper(), path) if path else None


def gate(is_admin_account: Callable[[str], bool]) -> Callable[[Request], None]:
    """The app-level dependency. Asks identity only for a refused route (cheap on every other one),
    and only about the subject the identity door already let through."""
    def admin_account_gate(request: Request) -> None:
        key = _matched(request)
        if key is None or key not in REFUSED:
            return
        subject = (request.headers.get("x-user-id") or "").strip()
        if subject and is_admin_account(subject):
            raise HTTPException(status_code=403, detail=REFUSAL)
    return admin_account_gate


def confined_slug(slug: Optional[str], *, global_slug: str, system_slug: str) -> bool:
    """May the admin account address this workspace slug? `_global` and `_system`, nothing else —
    the empty slug (its own desk) included."""
    return (slug or "").strip() in (global_slug, system_slug)
