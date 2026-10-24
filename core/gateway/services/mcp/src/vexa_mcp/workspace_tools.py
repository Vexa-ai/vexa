"""workspace_tools.py — `meeting_bind_workspace`: put a meeting, and what comes out of it, in a workspace.

Where a meeting's write-up lands is decided by two facts the platform already keeps, and this tool
sets both through routes that already exist, with the caller's own key:

  * the meeting's PAGE (`kg/entities/meeting/<name>.md`) — the one its sidebar shows, Update notes
    rewrites and the post-meeting report lands in. A page in workspace W, attached to the meeting
    (agent `POST /agent/meeting/note/attach`, the verb behind `meeting_attach_page`), makes the
    report land in W. A page that is not there yet is created first (`PUT /agent/workspace/file`,
    a commit like any other page write).
  * for a SHARED workspace, the meeting row's `workspace_id` (meetings `PATCH /meetings/{id}` while
    it is planned, `POST /meetings/{platform}/{native}/workspace` once the bot has it) — so every
    member sees it live and gets its write-up.

The workspace is named as the person names it — its slug, its name, or `personal` — and resolved
against the person's own listing (`GET /agent/workspace/shared`, the verb behind `workspaces`).
Registered only where the agent domain is deployed: without it there are no workspaces to bind to.
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .paths import path_segment

ANNOTATIONS: Dict[str, Dict[str, Any]] = {
    # Adds a page (or adopts one) and a binding; never deletes. Re-running it with the same arguments
    # leaves the same state. Stays inside Vexa.
    "meeting_bind_workspace": {"title": "Bind meeting to workspace", "readOnlyHint": False,
                               "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    # The rest are assembled from the agent domain's manifest (core/agent/mcp.tools.v1.json) — the
    # workspace verbs a person's own client drives — annotated here until manifests carry their own (#1852).
    "workspace_search": {"title": "Search workspace pages", "readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
    "workspace_list": {"title": "List workspaces", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": False},
    "workspaces": {"title": "Workspaces and memberships", "readOnlyHint": True, "destructiveHint": False,
                   "idempotentHint": True, "openWorldHint": False},
    "workspace_tree": {"title": "List workspace pages", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": False},
    "workspace_read": {"title": "Read workspace page", "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": False},
    # Creates a page or replaces it WHOLE (committed, so the history keeps the old text): a replace
    # can drop words from the current page, hence destructive. The same write twice is one state.
    "workspace_write": {"title": "Write workspace page", "readOnlyHint": False, "destructiveHint": True,
                        "idempotentHint": True, "openWorldHint": False},
    "meeting_attach_page": {"title": "Attach page to meeting", "readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
}

MEETING_DIR = "kg/entities/meeting"
_NOTE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md\Z")
_PERSONAL = {"personal", "desk", "my desk"}

MakeRequest = Callable[..., Awaitable[Any]]


class BindWorkspace(BaseModel):
    meeting_db_id: int = Field(..., description="The meeting (its `meeting_db_id` from list_meetings or calendar_status).")
    workspace: str = Field(..., description="Where its write-up goes: a workspace's slug or name as `workspaces` "
                                            "lists it, or 'personal'.")
    page_path: Optional[str] = Field(
        None, description="An existing page to use as the meeting's page (kg/entities/meeting/<name>.md in that "
                          "workspace). Omitted: the meeting's page there, created if it is not there yet.")
    replace: bool = Field(False, description="true when the meeting already has a page elsewhere with words in it "
                                             "and you merged what it held: that page stops being its page.")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:60].strip("-")


def page_path_for(row: Mapping[str, Any]) -> str:
    """The page name a meeting gets when the binding creates one: `<day>-<title>.md`, else `meeting-<id>.md`."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    day = str(data.get("scheduled_at") or row.get("start_time") or row.get("created_at") or "")[:10]
    title = _slug(data.get("title") or "")
    stem = "-".join(p for p in (day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else "", title) if p)
    name = f"{stem or 'meeting'}-{row.get('id')}.md" if not title else f"{stem}.md"
    return f"{MEETING_DIR}/{name}"


def _stub(row: Mapping[str, Any]) -> str:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    title = str(data.get("title") or f"Meeting {row.get('id')}").replace("\n", " ").strip()
    day = str(data.get("scheduled_at") or row.get("start_time") or row.get("created_at") or "")[:10]
    front = [f"title: {title}"] + ([f"date: {day}"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else [])
    return "---\n" + "\n".join(front) + "\n---\n\n# " + title + "\n"


def install(app: FastAPI, *, base_url: str, make_request: MakeRequest, get_api_key: Callable[..., Any]) -> None:
    """Register `meeting_bind_workspace` on ``app`` (before ``FastApiMCP`` derives the surface)."""

    @app.post("/meeting-workspace", operation_id="meeting_bind_workspace")
    async def meeting_bind_workspace(data: BindWorkspace, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """
        Put a meeting in a workspace, so its write-up lands there: its page (the one the post-meeting
        report is written into) becomes a page in that workspace, created if needed, and for a shared
        workspace the meeting itself joins it, so every member sees it live and gets the write-up.

        Use it when the person says which workspace a meeting belongs to — before it starts or after.
        A meeting has one page: if it already has one with words in it elsewhere, the answer names it
        (409); merge what you need into the page here, then call again with replace=true.
        """
        row = await make_request("GET", f"{base_url}/meetings/{path_segment(data.meeting_db_id)}", api_key)
        if not isinstance(row, dict):
            raise HTTPException(status_code=502, detail="meeting_bind_workspace: the meeting could not be read")
        wanted = data.workspace.strip()
        listing = await make_request("GET", f"{base_url}/agent/workspace/shared", api_key)
        rows = [r for r in (listing or {}).get("all") or [] if isinstance(r, dict)]
        if wanted.lower() in _PERSONAL:
            place = next((r for r in rows if r.get("kind") == "personal"), {"slug": "", "kind": "personal",
                                                                          "name": "Personal"})
        else:
            place = next((r for r in rows if str(r.get("slug") or "") == wanted), None) or next(
                (r for r in rows if str(r.get("name") or "").strip().lower() == wanted.lower()), None)
        if place is None:
            raise HTTPException(status_code=404, detail={
                "refused": "no such workspace of yours",
                "workspace": wanted,
                "yours": [{"slug": r.get("slug"), "name": r.get("name"), "kind": r.get("kind")} for r in rows],
                "said": "Name one of the workspaces listed (slug or name), or 'personal'."})
        kind = str(place.get("kind") or "")
        slug = "personal" if kind == "personal" else str(place.get("slug") or "")

        shared = False
        if kind == "shared":
            try:
                await make_request("PATCH", f"{base_url}/meetings/{path_segment(data.meeting_db_id)}", api_key,
                                   {"workspace_id": slug})
            except HTTPException as exc:
                if exc.status_code != 409 or not row.get("native_meeting_id"):
                    raise
                # No longer planned: the bot owns the row, and the native route binds a live meeting.
                await make_request(
                    "POST", f"{base_url}/meetings/{path_segment(row.get('platform'))}/"
                            f"{path_segment(row.get('native_meeting_id'))}/workspace",
                    api_key, {"workspace_id": slug})
            shared = True

        path = (data.page_path or "").strip() or page_path_for(row)
        name = path[len(MEETING_DIR) + 1:] if path.startswith(MEETING_DIR + "/") else ""
        if not _NOTE_NAME.match(name) or name == "index.md":
            raise HTTPException(status_code=422, detail=(
                f"meeting_bind_workspace: a meeting page lives at {MEETING_DIR}/<name>.md (got {path!r})"))
        created = False
        try:
            await make_request("GET", f"{base_url}/agent/workspace/file", api_key,
                               params={"path": path, "slug": slug})
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            if data.page_path:
                raise HTTPException(status_code=404, detail=f"meeting_bind_workspace: no page {path!r} in {slug}")
            await make_request("PUT", f"{base_url}/agent/workspace/file", api_key,
                               {"path": path, "content": _stub(row), "slug": slug})
            created = True
        attached = await make_request("POST", f"{base_url}/agent/meeting/note/attach", api_key,
                                      {"path": path, "workspace": slug, "meeting_id": str(data.meeting_db_id),
                                       "replace": bool(data.replace)})
        return {
            "meeting_db_id": data.meeting_db_id,
            "workspace": {"slug": slug, "name": place.get("name"), "kind": kind},
            "shared_with_members": shared,
            "page": {"path": path, "workspace": (attached or {}).get("workspace", slug), "created": created,
                     "recorded": bool((attached or {}).get("recorded"))},
        }
