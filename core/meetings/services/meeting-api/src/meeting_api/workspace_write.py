"""workspace_write.py — may this caller put a meeting INTO a shared workspace?

Binding a meeting to a workspace (``data.workspace_id``) publishes it to every member: the list,
the meeting page, the live transcript and, if its owner allows, the recording. That is a WRITE into
the workspace, so it takes write access there — role contributor or owner — not just membership. A
read-only member (viewer) who could bind would be adding content to a workspace they may only read.

The answer comes from the signed identity: identity's ``/internal/validate`` lists the caller's
writable memberships, the gateway signs them (gateway-identity.v1 ``writable_workspaces``) and the
identity door rebuilds ``x-user-writable-workspaces`` from the verified claims, so a request body
cannot forge it. An absent header means the caller may write into no shared workspace — the strict
reading, so an identity minted before the claim existed fails closed.
"""
from __future__ import annotations

from typing import Mapping, Optional

from fastapi import HTTPException

WRITABLE_HEADER = "x-user-writable-workspaces"


def writable_workspaces(headers: Mapping[str, str]) -> "set[str]":
    raw = headers.get(WRITABLE_HEADER) or ""
    return {w.strip() for w in raw.split(",") if w.strip()}


def require_writable(workspace_id: Optional[str], headers: Mapping[str, str]) -> None:
    """Refuse (403) binding to a workspace the caller cannot write. ``None`` (unbind) always passes.

    BINDING IS A PERSON'S ACT (R1801-8). It publishes a meeting to every member of a workspace, so a
    worker dispatched with nobody in the loop (an unwatched regime) is refused with the shared
    refusal on every path that binds — planned create, both PATCH forms, the bind route — exactly as
    `require_person` refuses it on the routes that carry that dependency."""
    from . import identity_token

    if workspace_id and identity_token.is_unwatched(headers):
        raise HTTPException(status_code=403, detail=identity_token.REFUSAL)
    if workspace_id and workspace_id not in writable_workspaces(headers):
        raise HTTPException(
            status_code=403,
            detail=f"you need edit access to workspace '{workspace_id}' to put a meeting in it")
