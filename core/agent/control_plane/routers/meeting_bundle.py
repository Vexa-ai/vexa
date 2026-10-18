"""routers/meeting_bundle.py — a meeting's workspace and notes page, in and out of a meeting bundle.

  GET  /api/meeting/bundle-parts?meeting_id=   the PARTS archive for an export: the bound workspace's
                                               tree and the meeting's page on the caller's desk.
                                               204 when the meeting has neither.
  POST /api/meeting/bundle-restore?meeting_id= the bundle (application/zip) a meeting was just
                                               imported from: its workspace part becomes a new
                                               private workspace of the caller, its notes page the
                                               meeting's page on the caller's desk.

OWNER ONLY, BOTH. The meetings domain decides access (`peer_lookups.meeting_access_check`); a row it
returns with `shared` true is a meeting the caller reads through a share or a workspace — they see
it, and they are refused (403), because neither the workspace nor the page is theirs to carry away
or to land. A row it does not return is refused the same way the other meeting routes refuse it.
The restore is a WRITE into the person's workspaces, so it is also a `person` verb in
`core/agent/routes.v1.json` (`route_policy`): an unwatched delegated worker cannot run it.

The logic is `control_plane/meeting_bundle.py`; the format is the contract's vendored codec.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from control_plane import meeting_bundle as bundle_mod
from control_plane import workspace_membership as membership_mod
from control_plane.ceiling import reads_within
from control_plane.peer_lookups import meeting_access_check
from control_plane.workspace_attach import create_workspace, workspace_slot_dir
from shared import meeting_bundle_codec as codec
from workspaces.shared import entities as entities_mod

_STATUS = {"too_large": 413, "duplicate_import": 409}


def build(**d) -> APIRouter:
    router = APIRouter()
    _meeting_note_recorder = d['_meeting_note_recorder']
    _meeting_owner_lookup = d['_meeting_owner_lookup']
    subject_of = d['subject_of']
    wsr = d['wsr']
    _meeting_access = meeting_access_check(_meeting_owner_lookup, wsr.root)

    def _owned_row(request: Request, meeting_id: str) -> dict:
        subject = subject_of(request)   # 401 without a (gateway-injected) identity — fail closed
        row = _meeting_access(subject, meeting_id, within=lambda slug: reads_within(request, slug))
        if row is None:
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        if row.get("shared") is not False:
            raise HTTPException(status_code=403, detail="only the meeting's owner can move its workspace and notes")
        return row

    def _commit(root, rels):
        if rels:
            entities_mod.commit_entity(root, rels, subject_path=rels[0], created=True)

    @router.get("/api/meeting/bundle-parts")
    def bundle_parts(request: Request, meeting_id: str = Query(...)):
        row = _owned_row(request, meeting_id)
        subject = subject_of(request)
        ws_id = str(((row.get("data") or {}).get("workspace_id")) or "").strip()
        ws_dir: Optional[object] = None
        if ws_id and reads_within(request, ws_id):
            try:
                if membership_mod.is_member(wsr.root, ws_id, subject) is not None:
                    ws_dir = membership_mod._ws_dir(wsr.root, ws_id)
            except membership_mod.MembershipError:
                ws_dir = None
        try:
            files, notes, skipped = bundle_mod.snapshot(wsr.root, subject, row, workspace_dir=ws_dir)
        except bundle_mod.RestoreRefused as e:
            raise HTTPException(status_code=e.status, detail={"code": e.code, "detail": e.detail})
        if not files and notes is None:
            return Response(status_code=204, headers={"X-Vexa-Skipped-Files": str(len(skipped))})
        return Response(content=codec.write_parts(files, notes), media_type="application/zip", headers={
            "X-Vexa-Workspace-Files": str(len(files)),
            "X-Vexa-Notes-Page": "1" if notes is not None else "0",
            # Named, not dropped: a file whose name or size a bundle cannot carry is counted here.
            "X-Vexa-Skipped-Files": str(len(skipped)),
            "Cache-Control": "no-store",
        })

    @router.post("/api/meeting/bundle-restore", status_code=201)
    async def bundle_restore(request: Request, meeting_id: str = Query(...)):
        row = _owned_row(request, meeting_id)
        subject = subject_of(request)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > codec.MAX_BUNDLE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "too_large", "detail": "the bundle is too large"})
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > codec.MAX_BUNDLE_BYTES:
                raise HTTPException(status_code=413, detail={"code": "too_large", "detail": "the bundle is too large"})
        try:
            parsed = codec.read_bundle(bytes(body))
            out = bundle_mod.restore(
                wsr.root, subject, row, parsed, record_note=_meeting_note_recorder,
                create_workspace=create_workspace, slot_dir=workspace_slot_dir, commit=_commit)
        except codec.BundleRefused as e:
            raise HTTPException(status_code=_STATUS.get(e.code, 422), detail=e.body())
        except bundle_mod.RestoreRefused as e:
            raise HTTPException(status_code=e.status, detail={"code": e.code, "detail": e.detail})
        return JSONResponse(status_code=201, content=out)

    return router
