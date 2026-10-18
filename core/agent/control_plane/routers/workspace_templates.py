"""routers/workspace_templates.py — create a workspace from a template, and save one as a template
(Vexa-ai/vexa#1783, founder 2026-10-14).

  ``GET  /api/workspace/templates``       the templates this person can start from: the company's
                                          (`_global/workspace-templates/`) and their own
                                          (`templates/workspaces/` in Personal), each with its
                                          description, what it creates and the questions to ask.
  ``POST /api/workspace/create``          the existing create (`workspace_attach.create_workspace`),
                                          then the template's pages filled from the host's answers.
  ``POST /api/workspace/templates/save``  copy a workspace's pages into the person's own template.

The mechanism is `control_plane.workspace_templates`; this router only resolves who and where.
"""
from __future__ import annotations

from control_plane.workspace_reader import home_of

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from control_plane import system_mounts
from control_plane.ceiling import require_in_ceiling
from control_plane import workspace_templates as tpl
from control_plane.workspace_attach import create_workspace, workspace_slot_dir


class WorkspaceCreateBody(BaseModel):
    """``workspace_create`` — a new workspace, optionally from a template."""
    model_config = {"extra": "forbid"}
    name: Optional[str] = Field(default=None, max_length=80,
                                description="What the person calls the workspace, e.g. \"AI meetup October\".")
    template: Optional[str] = Field(default=None, max_length=80,
                                    description="A template name or title from workspace_templates_list. "
                                                "Omitted: a blank workspace (same as workspace_new).")
    answers: Optional[dict[str, str]] = Field(
        default=None, description="The host's answers to the template's questions, by question key. "
                                  "Ask them in the chat first; an unanswered one is left marked to decide.")
    use_in_this_chat: bool = Field(
        default=True, description="true (the default): made in a chat, the chat works in it from this turn — "
                                  "it writes there. false only when the person asked for a workspace for "
                                  "something else.")


class WorkspaceTemplateSaveBody(BaseModel):
    """``workspace_template_save`` — keep a workspace's pages as the person's own template."""
    model_config = {"extra": "forbid"}
    name: str = Field(..., min_length=1, max_length=80, description="The template's name, e.g. \"board meeting\".")
    slug: Optional[str] = Field(default=None, max_length=128,
                                description="The workspace to save, by slug. Omitted: Personal.")
    description: Optional[str] = Field(default=None, max_length=200,
                                       description="One line shown when picking a template.")
    when: Optional[str] = Field(default=None, max_length=200, description="When to use it, in a sentence.")
    replace: bool = Field(default=False, description="Overwrite the person's template of the same name.")


def build(**d) -> APIRouter:
    router = APIRouter()
    settings = d["settings"]
    subject_of = d["subject_of"]
    wsr = d["wsr"]
    _ws_sync = d["_ws_sync"]
    _read_target = d["_read_target"]
    sess = d.get("sess")
    mindex = d.get("mindex")
    workspace_registry = d.get("workspace_registry")

    def _global_dir():
        return system_mounts.global_root(settings, wsr.root)

    def _personal(subject):
        p = home_of(wsr, subject)                      # the person's home (#1783)
        return p if p.is_dir() else None

    @router.get("/api/workspace/templates")
    def ws_templates(request: Request):
        """List the workspace templates this person can start from (event organization, client
        project, meeting series, research, the company's own and the person's saved ones). Each has a
        one-line `description`, `when` to use it, the pages it `creates`, the `questions` to ask the
        host before creating, and `suggested_routines` (suggestions only, nothing is switched on).
        Offer the fitting one in one line; then ask its questions in the chat, one message, and call
        `workspace_create` with the answers."""
        subject = subject_of(request)
        items = tpl.list_templates(_global_dir(), _personal(subject))
        return {"templates": [t.as_dict() for t in items]}

    @router.post("/api/workspace/create", status_code=201)
    def ws_create(request: Request, body: WorkspaceCreateBody = Body(default=WorkspaceCreateBody())):
        """CREATE a new workspace, scaffolded from a template when `template` is given. Ask the
        template's questions first (from `workspace_templates_list`) and pass the replies as
        `answers` by key. Answer in one line: the workspace's name, the pages it has, and the
        suggested routines as an offer (they are not switched on). Made in a chat, the chat works in it
        FROM THIS TURN (`use_in_this_chat`, default true): it writes there, and once it is shared it
        reads only it. Say that in one line; never tell the person to continue in another chat. It
        starts PRIVATE: to bring people in, share it (`workspace_invite`, or an invite link)."""
        subject = subject_of(request)
        template = None
        if body.template:
            try:
                template = tpl.find(_global_dir(), _personal(subject), body.template)
            except tpl.TemplateError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
        name = (body.name or "").strip() or (template.title if template else "") or None
        try:
            result = create_workspace(wsr.root, subject, name=name)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        ws_dir = workspace_slot_dir(wsr.root, subject, result.slug)
        applied = None
        if template is not None:
            applied = tpl.apply(template, ws_dir, body.answers, workspace_name=name or result.slug)
        _ws_sync(result.slug, kind="desk", owner=str(subject), name=name, ws_dir=ws_dir)
        out = {"subject": result.subject, "slug": result.slug, "created": result.slug,
               "name": name or result.slug, "changed": True, "added": True}
        if applied is not None:
            out.update(applied)
        if body.use_in_this_chat:
            from control_plane.routers.workspace_states import use_in_chat  # noqa: PLC0415
            chat = use_in_chat(request, sess, settings, wsr.root, mindex, workspace_registry,
                               str(subject), result.slug)
            if chat:
                out["chat"] = chat
                out["say"] = f"This chat now works in {name or result.slug}: writes go there from now on."
        return out

    @router.post("/api/workspace/templates/save", status_code=201)
    def ws_template_save(request: Request, body: WorkspaceTemplateSaveBody = Body(...)):
        """SAVE a workspace's pages as the person's own template ("save this workspace as a
        template"). It lands in their Personal workspace under `templates/workspaces/<name>/`, and
        `workspace_templates_list` offers it from then on, ahead of the company's of the same name.
        Entity records, history and settings are not carried, only the pages. Company templates are
        the admin account's to edit, in `_global/workspace-templates/`."""
        # The workspace it reads is named: a delegated dispatch is held to its ceiling before anything.
        if body.slug:
            require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        personal = _personal(subject)
        if personal is None:
            raise HTTPException(status_code=409, detail="your Personal workspace is not set up yet")
        src = _read_target(request, body.slug)
        try:
            return tpl.save(src, personal, body.name, description=body.description or "",
                            when=body.when or "", replace=body.replace)
        except tpl.TemplateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return router
