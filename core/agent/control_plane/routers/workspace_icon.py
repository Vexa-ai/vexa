"""routers/workspace_icon.py — a workspace's image over HTTP (UI decision 15f, Vexa-ai/vexa#1783).

    GET  /api/workspaces/{slug}/identity/icon       the image: its own, else the bound company's logo
                                                    (media proxy), else 404 — the client then draws
                                                    the monogram. `?proposal=1` shows a pending one.
    GET  /api/workspaces/{slug}/identity/state      what it is now (`workspace` · `company` · none) and
                                                    any pending proposal
    PUT  /api/workspaces/{slug}/identity/icon       set it: an `svg`, base64 `data_b64` + `type`, a
                                                    `url` the server fetches, `site` (find that site's
                                                    logo), `use: company_logo`, or `reset`
    POST /api/workspaces/{slug}/identity/proposal   the owner accepts or declines a pending proposal

WHY `identity/icon` AND NOT `{slug}/icon`: the shorter path overlaps `/api/workspaces/by-slug/{slug}`
(one concrete URL, `/api/workspaces/by-slug/icon`, would match both) — the same reason
`git/history` carries its extra segment; `tests/test_route_table.py` refuses the overlap.

READ = THE FILE READ'S GUARD (`_read_target`), to the letter: an image is readable exactly where the
workspace's pages are. WRITE = `_read_target(write=True)`: owner or contributor of a shared
workspace, the owner of a private one. In a SHARED workspace (a roster of two or more) a DELEGATED
caller — an agent — proposes instead of writing, and only the owner, in person, accepts.

Every fetch goes through `asset_source.fetch_asset` (the outbound URL guard on every hop, size cap);
nothing is hot-linked, and the bytes served are the bytes stored, under the asset route's sandbox CSP.
"""
from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from control_plane import home_media
from control_plane import workspace_icon as wi
from control_plane import workspace_membership as membership_mod
from control_plane.ceiling import is_delegated
from shared.gitexec import run_git

logger = logging.getLogger("agent_api.workspace_icon")

HEADERS = {
    "Cache-Control": "private, max-age=60",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "Content-Disposition": 'inline; filename="icon"',
}


class IconSetBody(BaseModel):
    """One way to set a workspace's image. Give exactly one of `svg`, `data_b64`, `url`, `site`,
    `use` or `reset`."""
    model_config = ConfigDict(extra="forbid")
    svg: Optional[str] = Field(default=None, max_length=wi.MAX_BYTES * 2, description=(
        "An SVG mark you designed: one glyph or motif and a colour that suits the topic, a square "
        "viewBox, legible at 24 px on dark and light. Scripts, event handlers, foreignObject and "
        "external references are removed."))
    data_b64: Optional[str] = Field(default=None, max_length=wi.MAX_BYTES * 2, description=(
        "A PNG or WebP (or SVG) the person uploaded, base64-encoded; `type` names which."))
    type: Optional[str] = Field(default=None, description="`png`, `webp` or `svg`, with `data_b64`.")
    url: Optional[str] = Field(default=None, max_length=2048, description=(
        "An image address the server fetches and stores in the workspace (never hot-linked)."))
    site: Optional[str] = Field(default=None, max_length=253, description=(
        "A company's or project's site (domain). The server finds its logo — apple-touch-icon, the "
        "page's icon links, favicon, og:image — and stores it."))
    use: Optional[str] = Field(default=None, description=(
        "`company_logo`: store the bound company's logo (README `domain:`) as the image."))
    reset: Optional[bool] = Field(default=None, description=(
        "Remove the image; the workspace shows its company's logo or its monogram again."))
    propose: Optional[bool] = Field(default=None, description=(
        "Store it as a PROPOSAL the owner accepts (on the workspace's card) instead of setting it — "
        "what you do for a workspace that was just created, so the person sees and chooses it."))


class IconProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept: bool = Field(description="true to make the proposed image the workspace's, false to decline it.")


def build(**d) -> APIRouter:
    router = APIRouter()
    _read_target = d["_read_target"]
    subject_of = d["subject_of"]
    wsr = d["wsr"]
    _global_root = d["_global_root"]

    def _slug(slug: str) -> Optional[str]:
        return None if slug in ("", "personal", "desk") else slug

    def _target(request: Request, slug: str, *, write: bool = False) -> Path:
        if slug == "_system":
            raise HTTPException(status_code=403, detail="_system has no image")
        try:
            return _read_target(request, _slug(slug), write=write)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid workspace") from None

    def _roster(slug: str) -> list[dict]:
        s = _slug(slug)
        if not s or s.startswith("_"):
            return []
        try:
            return membership_mod.read_members(wsr.root, s)
        except Exception:  # noqa: BLE001 — not a shareable workspace: no roster
            return []

    def _role(slug: str, subject: str) -> Optional[str]:
        return next((m.get("role") for m in _roster(slug) if m.get("subject") == subject), None)

    def _owner(request: Request, slug: str) -> bool:
        """The owner: by the roster of a shared workspace; for one with no roster (private, or the
        person's own), whoever may WRITE it — never a reader of the company layer."""
        if _roster(slug):
            return _role(slug, subject_of(request)) == "owner"
        try:
            _target(request, slug, write=True)
            return True
        except HTTPException:
            return False

    def _shared(slug: str) -> bool:
        return len(_roster(slug)) >= 2

    def _company(ws: Path) -> Optional[tuple[bytes, str]]:
        key = wi.company_logo_key(ws)
        if not key or not home_media.enabled(_global_root()):
            return None
        return home_media.get(key)

    def _commit(ws: Path, paths: list[str], message: str, author: str) -> None:
        if not (ws / ".git").is_dir():
            return
        staged = [p for p in dict.fromkeys(paths) if run_git(ws, "add", "--", p).returncode == 0]
        if staged:
            run_git(ws, "-c", f"user.name={author or 'vexa'}", "-c", "user.email=terminal@vexa.local",
                    "commit", "-q", "-m", message, "--", *staged)

    @router.get("/api/workspaces/{slug}/identity/icon")
    def icon(slug: str, request: Request, proposal: int = 0):
        """The workspace's image — its own (`.vexa/icon.*`), else its company's logo through the
        media proxy, else 404 (draw the monogram). `proposal=1` serves a pending proposal instead."""
        ws = _target(request, slug)
        got = wi.read_proposal(ws) if proposal else (wi.read_icon(ws) or _company(ws))
        if not got:
            raise HTTPException(status_code=404, detail="no image")
        return Response(content=got[0], media_type=got[1], headers=HEADERS)

    @router.get("/api/workspaces/{slug}/identity/state")
    def state(slug: str, request: Request):
        """What this workspace's image is now: `source` is `workspace` (its own file), `company`
        (its bound company's logo) or null (the client draws the monogram); `proposal` is a change
        an agent proposed and the owner has not answered; `domain` is the bound company's site."""
        ws = _target(request, slug)
        source = "workspace" if wi.icon_path(ws) else ("company" if _company(ws) else None)
        return {"slug": slug, "source": source, "path": wi.icon_path(ws),
                "domain": wi.company_domain(ws), "proposal": wi.proposal(ws),
                "shared": _shared(slug), "owner": _owner(request, slug)}

    @router.put("/api/workspaces/{slug}/identity/icon")
    def set_icon(slug: str, request: Request, body: IconSetBody = Body(...)):
        """Set the workspace's image. A person, or an agent in a private workspace, sets it and the
        change is one commit in the workspace. An agent in a SHARED workspace PROPOSES it: the
        answer is 202 `proposed`, the owner accepts in one click, and you say so in one line."""
        ws = _target(request, slug, write=True)
        subject = subject_of(request)
        agent = is_delegated(request)
        try:
            if body.reset:
                if agent and _shared(slug):
                    raise HTTPException(status_code=403, detail=(
                        "in a shared workspace an agent proposes a new image; resetting it is the owner's"))
                _commit(ws, wi.reset_icon(ws), "workspace image: reset", subject)
                return {"set": True, "reset": True}
            raw, kind, source = _bytes(ws, body)
        except wi.IconRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        try:
            if body.propose or (agent and _shared(slug)):
                meta = wi.propose(ws, raw, kind, by=subject, source=source)
                logger.info("workspace image proposed slug=%s by=%s", slug, subject)
                return JSONResponse(status_code=202, content={"proposed": True, "proposal": meta})
            touched = wi.write_icon(ws, raw, kind)
        except wi.IconRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        _commit(ws, touched, f"workspace image ({source or 'uploaded'})", subject)
        return {"set": True, "path": wi.icon_path(ws), "source": source}

    def _bytes(ws: Path, body: IconSetBody) -> tuple[bytes, str, str]:
        if body.svg is not None:
            return body.svg.encode("utf-8"), "svg", "designed"
        if body.data_b64 is not None:
            try:
                raw = base64.b64decode(body.data_b64, validate=True)
            except (binascii.Error, ValueError):
                raise wi.IconRefused("data_b64 is not base64") from None
            return raw, str(body.type or ""), "uploaded"
        if (body.url or body.site) and not home_media.enabled(_global_root()):
            raise wi.IconRefused("external_images is off for this company: no image is fetched from the web; "
                                 "design an SVG mark or upload one", status=403)
        if body.url:
            from shared import asset_source  # noqa: PLC0415
            try:
                content, ctype, _final = asset_source.fetch_asset(body.url)
            except asset_source.AssetFetchError as exc:
                raise wi.IconRefused(str(exc), status=400 if exc.kind == "refused" else 424) from None
            stored, kind = wi.from_fetched(content, ctype)
            return stored, kind, body.url
        if body.site:
            found = wi.find_site_logo(body.site)
            if not found:
                raise wi.IconRefused(f"{body.site} offers no logo that could be fetched; design an SVG mark",
                                     status=404)
            return found[0], found[1], found[2]
        if body.use == "company_logo":
            got = _company(ws)
            if not got:
                raise wi.IconRefused("this workspace has no company logo (README `domain:`)", status=404)
            stored, kind = wi.from_fetched(got[0], got[1])
            return stored, kind, f"company logo {wi.company_domain(ws)}"
        raise wi.IconRefused("give one of svg, data_b64, url, site, use=company_logo or reset")

    @router.post("/api/workspaces/{slug}/identity/proposal")
    def answer(slug: str, request: Request, body: IconProposalBody = Body(...)):
        """The owner's answer to a proposed image: accept (it becomes the workspace's, one commit)
        or decline (it is dropped). The owner's own act — an agent never accepts its own proposal."""
        ws = _target(request, slug)      # the ceiling and the read guard answer first, as on every route
        if is_delegated(request):
            raise HTTPException(status_code=403, detail={
                "status": "refused", "reason": "owner_accepts",
                "instruction": "the owner accepts or declines a proposed image themselves"})
        subject = subject_of(request)
        if not _owner(request, slug):
            raise HTTPException(status_code=403, detail="only the workspace's owner answers a proposed image")
        if not wi.proposal(ws):
            raise HTTPException(status_code=404, detail="no proposed image")
        if not body.accept:
            wi.drop_proposal(ws)
            return {"set": False, "declined": True}
        by = (wi.proposal(ws) or {}).get("by", "")
        touched = wi.accept_proposal(ws) or []
        _commit(ws, touched, f"workspace image (proposed by {by or 'an agent'}, accepted)", subject)
        return {"set": True, "path": wi.icon_path(ws)}

    return router
