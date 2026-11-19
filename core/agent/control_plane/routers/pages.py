"""routers/pages.py — the two verbs the harness turns into panel events (Vexa-ai/vexa#1783).

`open_page` (#1586) and `workspace_target` (#1611) were served only by the dogfood control rig. They
are this domain's: the person's workspaces and the chat's focus live here, and the meeting reads go
through the same access seam the live transcript stream uses. Each route ANSWERS and writes nothing —
the harness reads `opened` / `targeted` off the result (`llm/tool_events.py`) and `_binding_watch`
(`routers/chats.py`) stays the one writer of a chat's target.
"""
from __future__ import annotations

from control_plane import global_layer, identity_token, system_mounts
from control_plane import meeting_note as meeting_note_mod
from control_plane.api_shared import _is_slug
from control_plane.bodies import PageOpenBody, WorkspaceTargetBody
from control_plane.ceiling import reads_within, require_in_ceiling
from control_plane.peer_lookups import meeting_access_check, meeting_transcript_reader
from fastapi import APIRouter, Body, HTTPException, Request

MEETING_TARGETS = ("meeting:transcript", "meeting:note")


def build(**d) -> APIRouter:
    router = APIRouter()
    _read_target = d['_read_target']
    settings = d['settings']
    subject_of = d['subject_of']
    wsr = d['wsr']
    _meeting_access = meeting_access_check(d['_meeting_owner_lookup'], wsr.root)
    _meeting_words = meeting_transcript_reader(d['_meeting_transcript_lookup'], wsr.root)

    def _within(request: Request):
        return lambda slug: reads_within(request, slug)

    @router.post("/api/workspace/target")
    def ws_target(request: Request, body: WorkspaceTargetBody = Body(default=WorkspaceTargetBody())):
        """THE AGENT DOES NOT MOVE A CHAT'S WRITES (UI decision 6b, founder 2026-10-15): call
        `propose_write_target(workspace, reason)` instead — the person sees the proposal and only
        their click switches. This verb answers with that instruction and changes nothing; it is
        kept so an older prompt that still names it is told what to do rather than failing."""
        subject = subject_of(request)
        wid = (body.slug or "").strip()
        if not wid:
            return {"targeted": "", "workspace": "their own desk",
                    "say": "Writes go back to your own desk from now on."}
        if not _is_slug(wid):
            raise HTTPException(status_code=400, detail="not a workspace slug")
        require_in_ceiling(request, wid)
        if identity_token.is_delegated(request.headers):
            raise HTTPException(status_code=409, detail=(
                "Only the person switches where this chat writes. Call propose_write_target("
                "workspace, reason) — they will see Switch / Keep — and wait for their answer."))
        if wid == system_mounts.GLOBAL_SLUG:
            if not global_layer.is_admin_account(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only the admin account may write into _global")
        else:
            try:
                _read_target(request, wid, write=True)
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                raise HTTPException(status_code=403, detail="not a workspace you can write")
        return {"targeted": wid,
                "say": "Say, in one line, that this conversation now writes into that workspace.",
                "from_now_on": "writes with no workspace named go here; everything else mounted "
                               "stays readable, and a single write elsewhere still works with slug="}

    @router.post("/api/page/open")
    def page_open(request: Request, body: PageOpenBody = Body(...)):
        """OPEN something in the person's pages panel — actually put it in front of them. Call it
        whenever they ask to open, show, see or pull up a page, a note or the transcript: reading a
        file and summarising it is not opening it.

        The answer says what HAPPENED — `opened`, or `opened: false` with the reason. Say that, in
        one line; never claim you opened something this refused."""
        subject = subject_of(request)
        t = (body.target or "").strip()
        if not t:
            return {"opened": False, "reason": "say what to open"}
        kind = t.lower()
        if kind in MEETING_TARGETS:
            ref = (body.meeting or "").strip()
            if not ref.isdigit():
                return {"opened": False, "target": t,
                        "reason": "say which meeting: pass meeting=<row id> (list_meetings gives it)"}
            row = _meeting_access(subject, ref, within=_within(request))
            if row is None:
                raise HTTPException(status_code=403, detail="not authorized for this meeting")
            if kind == "meeting:transcript":
                words = _meeting_words(subject, ref, within=_within(request))
                if words is None:
                    return {"opened": False, "target": t, "reason": "could not read this meeting"}
                if not words:
                    return {"opened": False, "target": t, "reason": "no transcript for this meeting"}
                # THE ROW, NEVER THE NATIVE ID: a personal room's native id names a series.
                return {"opened": True, "target": t, "workspace": "", "path": f"meeting:{ref}",
                        "opened_what": "the live transcript"}
            note = meeting_note_mod.describe(wsr.root, subject, row) or {}
            path = str(note.get("path") or "")
            if not path:
                return {"opened": False, "target": t, "reason": "no note for this meeting yet"}
            return {"opened": True, "target": t, "workspace": "", "path": path,
                    "opened_what": path.rsplit("/", 1)[-1]}

        # A WORKSPACE PATH. The head segment is a workspace only when this person can read one by
        # that name — otherwise `kg/entities/…` would resolve to a workspace called `kg`.
        slug, path = "", t
        head, sep, rest = t.partition("/")
        if sep and rest and _is_slug(head):
            if head == "personal":
                slug, path = "", rest
            else:
                try:
                    if reads_within(request, head):
                        _read_target(request, head)
                        slug, path = head, rest
                except HTTPException:
                    pass
        try:
            content = wsr.read_at(_read_target(request, slug or None), path)
        except ValueError:
            return {"opened": False, "target": t, "reason": "not a page path"}
        except HTTPException as exc:
            return {"opened": False, "target": t, "reason": "no such page", "status": exc.status_code}
        if content is None:
            return {"opened": False, "target": t, "reason": "no such page"}
        return {"opened": True, "target": t, "workspace": slug, "path": path,
                "opened_what": path.rsplit("/", 1)[-1]}

    return router
