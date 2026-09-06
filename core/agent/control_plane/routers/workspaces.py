"""routers/workspaces.py — Everything a workspace is: read and write its files, its git state, its identity, the
mount set, attach and swap, sharing, membership and invites, and the credentials that
make a remote reachable.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import claims as claims_mod
from control_plane import deploy_keys as deploy_keys_mod
from control_plane import front_page as front_page_mod
from control_plane import git_credentials as git_creds
from control_plane import global_layer
from control_plane import membership_acts
from control_plane import publish as publish_mod
from control_plane import repo_ref
from control_plane import scaffolds as scaffolds_mod
from control_plane import system_mounts
from control_plane import workspace_credentials as wcreds
from control_plane import workspace_ids as ids_mod
from control_plane import workspace_membership as membership_mod
from control_plane.api_shared import (
    ArchiveBody, GitTokenBody, InviteAcceptBody, InviteCreateBody, MAX_UPLOAD_BYTES,
    RoleSetBody, SharedActiveBody, SharedAttachBody, SharedNewBody, WorkspaceActivateBody,
    WorkspaceDeactivateBody, WorkspaceMoveBody, WorkspaceNewBody, WorkspacePublishBody,
    WorkspaceInviteBody, WorkspaceMembershipBody,
    WorkspacePullBody, WorkspacePurposeBody, WorkspacePushBody, WorkspaceRemoveBody,
    WorkspaceRenameBody, WorkspaceSwapBody, _upload_filename, logger)
from control_plane.workspace_attach import (
    CloneError, activate_workspace, active_workspaces, attach_shared_workspace,
    attached_workspaces, create_shared_workspace_dir, create_workspace,
    deactivate_workspace, delete_workspace, ensure_workspace_private,
    ensure_workspace_shareable, rename_workspace, set_archived, set_shared_active,
    shared_active_mounts, shared_attached_state, swap_workspace, workspace_slot_dir)
from control_plane.workspace_git_sync import (
    RemoteSyncError, detach_home, pull_origin, push_origin, remote_status)
from control_plane.workspace_membership import MembershipError
from control_plane.workspace_publish import (
    PublishError, RepoExistsError, publish_workspace, published_remote_url)
from control_plane.workspace_purpose import read_purpose, write_purpose
from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, Response, UploadFile
from pathlib import Path
from workspaces.shared import entities as entities_mod
from workspaces.shared import workspace_paths as wpaths
from shared import asset_source as assets_mod
from shared import friction as friction_mod
from shared import page_images
from shared.git_redaction import redact as redact_secrets
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from typing import Optional
import hashlib
import os


#: HOW FAR BACK THE FRONT PAGE LOOKS for a change a person can read (Vexa-ai/vexa#1634). Deep enough
#: to walk past a run of plumbing commits — an identity write, a roster write, a policy commit — and
#: shallow enough that the answer is still "recently". A workspace whose last twenty commits are all
#: plumbing has genuinely had no page changed lately, and the line says so by naming no page.
LAST_CHANGE_SCAN = 20


def build(**d) -> APIRouter:
    """The workspaces routes, bound to one app's dependencies."""
    router = APIRouter()
    _clone_fn = d['_clone_fn']
    _credential_refusal = d['_credential_refusal']
    # THE ADDRESS → SUBJECT RESOLVER (Vexa-ai/vexa#1632). Already built for the meeting room, and
    # the same question one door over: does this deployment know this address? Here the answer
    # decides whether an invite link is handed back in the chat or mailed, and a resolver that
    # cannot answer means EXTERNAL — the fail-closed direction, which mails rather than silently
    # handing the agent a link it will tell somebody is already theirs.
    _email_subject_lookup = d['_email_subject_lookup']
    _entity_mounts = d['_entity_mounts']
    _internal_caller = d['_internal_caller']
    _manage_dir = d['_manage_dir']
    _member_error = d['_member_error']
    _pc = d['_pc']
    _read_target = d['_read_target']
    _repo = d['_repo']
    _require_shared_write = d['_require_shared_write']
    _workspace_key = d['_workspace_key']
    _ws_is_member = d['_ws_is_member']
    _ws_sync = d['_ws_sync']
    mindex = d['mindex']
    settings = d['settings']
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    wsr = d['wsr']

    @router.get("/api/workspace/tree")
    def ws_tree(request: Request, hidden: bool = False, slug: Optional[str] = None):
        try:
            return {"files": wsr.tree_at(_read_target(request, slug), hidden=hidden)}
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
    @router.post("/api/workspace/upload")
    async def ws_upload(request: Request, files: list[UploadFile] = File(...)):
        if not files:
            raise HTTPException(status_code=400, detail="no files uploaded")
        subject = subject_of(request)
        try:
            ws = wsr.workspace_dir(subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        pending: list[tuple[Path, bytes, str, str]] = []
        for file in files:
            try:
                content = await file.read()
            finally:
                await file.close()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"{file.filename or 'upload'} exceeds 25MB")
            safe_name = _upload_filename(file.filename)
            digest = hashlib.sha256(content).hexdigest()
            stored_name = f"{digest[:16]}-{safe_name}"
            # A PICTURE GOES WHERE PICTURES GO (#1612). `uploads/` is the attachment drawer — a
            # thing the turn reads once; `assets/` is what a page REFERENCES, and a reference has to
            # keep working after the conversation that produced it is over. So an attached image
            # lands beside the ones the agent fetches, and everything else keeps its drawer.
            folder = assets_mod.ASSETS_DIR if assets_mod.is_image_path(safe_name) else "uploads"
            into = ws / folder
            into.mkdir(parents=True, exist_ok=True)
            target = (into / stored_name).resolve()
            if into.resolve() not in target.parents:
                raise HTTPException(status_code=400, detail="invalid filename")
            pending.append((target, content, stored_name, f"{folder}/{stored_name}"))
        uploaded: list[dict[str, str]] = []
        for target, content, stored_name, path in pending:
            target.write_bytes(content)
            uploaded.append({"name": stored_name, "path": path})
        return {"files": uploaded}
    def _write_dir(request: Request, subject, rel: str, slug: Optional[str]) -> Path:
        """WHICH workspace dir a write to ``rel`` lands in, or the refusal. The mount rules, in one
        place: own baseline/_system always; a shared workspace needs contributor+; `_global` only
        the admin allowlist; `kg/templates/` never.

        Extracted from ``ws_file_write`` unchanged when the ASSET writes arrived, because the
        alternative was a second copy of an authorization rule — and a permission check that exists
        twice is a permission check that will disagree with itself. One caller was already enough
        to make it worth a name; three is not a choice."""
        try:
            wpaths.relative_parts(rel)   # absolute · `..` · `.git`/`.vexa` — one rule, every route
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # `kg/templates/` is the SHAPE of an entity, not one. It is hidden from every tree, so a tab
        # can only be open on it deliberately — and a save from that tab would silently rewrite the
        # shape every future entity is copied from.
        if rel.startswith("kg/templates/"):
            raise HTTPException(status_code=403, detail="kg/templates/ holds entity shapes, not records")
        if slug == system_mounts.GLOBAL_SLUG:
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only an org admin may edit _global")
            candidates = [Path(settings.workspaces_dir) / system_mounts.GLOBAL_SLUG,
                          Path(settings.global_system_workspace_path or "/nonexistent")]
            target = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), None)
            if target is None:
                raise HTTPException(status_code=404, detail="the organisation tier is not writable here")
            return target
        if slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError:
                pass  # not shared — fall through; _read_target 403s anything outside the active set
        return _read_target(request, slug, write=True)

    def _screen_images(request: Request, values: list[str], *, path: str = "",
                       tool: str = "") -> list[str]:
        """VERIFY EVERY EXTERNAL IMAGE ADDRESS BEFORE IT REACHES A PAGE (Vexa-ai/vexa#1624).

        The OeNB README carried `![OeNB logo](https://upload.wikimedia.org/…/ÖNB_Logo.svg)`, an
        address the agent invented and nobody ever requested; it answers 404. This is the two
        page-writing doors — `workspace_write` (PUT /api/workspace/file) and `entity_upsert` — asking
        the question the writer did not: does this address answer, with an image? A dead one is cut
        out and the prose around it kept, because the sentence was not the mistake.

        It costs one regex on the overwhelming majority of writes, which carry no external image
        reference at all, and it never fails the write: a page saved without a picture is a page,
        and a save refused because a CDN was slow is a lost edit.

        THEN IT FILES THE FRICTION, because the writer cannot. An agent reports what it noticed
        (`worker/friction.py` §1), and the whole defect here is that it noticed nothing — so the
        record is written by the door that caught it, naming the address, exactly as the harness
        files what the model failed to (`friction.py` §3, the same argument one layer down)."""
        clean, dropped = page_images.screen_values(values)
        if not dropped:
            return list(values)
        logger.info("page images: dropped %d unverified address(es) from %s (%s)",
                    len(dropped), path or "a workspace write",
                    "; ".join(f"{d.url} → {d.reason}" for d in dropped[:3]))
        try:
            rec = friction_mod.normalize({
                **page_images.friction_report(dropped, path=path, tool=tool),
                "subject": str(subject_of(request)),
            })
            publish_mod.post_friction(rec)
        except Exception:  # noqa: BLE001 — a report is never worth failing the write it describes
            logger.warning("page images: could not file the friction for %s", path or "(no path)")
        return clean

    def _commit(target: Path, paths: list[str], message: str) -> "str | None":
        """Commit exactly the paths named, so history stays honest and a concurrent write in the
        same workspace is not swept into somebody else's message (the index is a shared surface).
        Returns the workspace's HEAD afterwards, or None when it is not a repository.

        STAGED ONE PATH AT A TIME, and that is not a style choice. `git add -- a b` refuses the
        WHOLE call when any pathspec matches nothing git knows about — and a REMOVAL names a path
        that is already gone from disk, which is unmatched whenever the file was never committed
        (a page the harness `Write`-tool created earlier in this same turn, before the mount commit
        runs). One such path would have sunk the other's staging silently, leaving a moved page on
        disk and nothing in history. Per path, `check=False`, and the commit then names only what
        actually staged."""
        import subprocess as _sp
        if not (target / ".git").is_dir():
            return None

        def _git(*args: str):
            return _sp.run(["git", "-C", str(target), *args], check=False, capture_output=True,
                           text=True)

        staged = [p for p in paths if p and _git("add", "--", p).returncode == 0]
        if staged:
            _git("-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                 "commit", "-m", message, "--", *staged)
        return _git("rev-parse", "HEAD").stdout.strip() or None

    def _store_asset(request: Request, rel: str, slug: Optional[str], content: bytes,
                     source: str) -> dict:
        """Put BYTES in a workspace under ``assets/`` and record where they came from.

        The source index is written in the same commit as the file: an asset and the answer to
        "where is this from" are one fact, and a crash between two commits would leave a picture in
        a customer's workspace with no provenance at all."""
        subject = subject_of(request)
        target = _write_dir(request, subject, rel, slug)
        if len(content) > assets_mod.MAX_ASSET_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"{rel} exceeds {assets_mod.MAX_ASSET_BYTES // (1024 * 1024)}MB")
        try:
            f = wpaths.resolve_inside(target, rel)   # …and again WITH the root, for the symlink half
            index = wpaths.resolve_inside(target, assets_mod.SOURCES_INDEX)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
        existing = index.read_text(encoding="utf-8") if index.is_file() else ""
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(assets_mod.record_source(existing, rel, source), encoding="utf-8")
        _commit(target, [rel, assets_mod.SOURCES_INDEX], f"asset {rel} ({source or 'uploaded'})")
        return {"path": rel, "bytes": len(content), "source": source,
                "content_type": assets_mod.media_type_for(rel)}

    @router.get("/api/workspace/asset")
    def ws_asset(request: Request, path: str, slug: Optional[str] = None,
                 if_none_match: Optional[str] = Header(default=None, alias="If-None-Match")):
        """SERVE one workspace file AS ITSELF — the route `![logo](assets/oenb-logo.svg)` renders
        through (Vexa-ai/vexa#1612).

        The scoping is `ws_file`'s, deliberately and to the letter: a page and the pictures in it
        come through the same owner- and membership-scoped door, so an image can never be readable
        where the document that references it is not. What differs is the ANSWER — bytes with the
        media type the extension names, rather than JSON with text in it.

        THREE HEADERS EARN THEIR PLACE. `nosniff` so the browser executes the file as what the
        workspace calls it and not as what it looks like; a `default-src 'none'` sandbox so a
        workspace `.svg` opened directly is a picture and not a script running on our origin; and an
        ETag over (size, mtime) with a short max-age, because agents rewrite workspace files under a
        stable path — a long cache would show yesterday's chart on today's page, and no cache at all
        would re-download every logo on every scroll."""
        try:
            base = _read_target(request, slug)
            f = wpaths.resolve_inside(base, path)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not (f.exists() and f.is_file()):
            raise HTTPException(status_code=404, detail="not found")
        stat = f.stat()
        etag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": f'inline; filename="{_upload_filename(f.name)}"',
        }
        if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(content=f.read_bytes(), media_type=assets_mod.media_type_for(path),
                        headers=headers)

    @router.post("/api/workspace/asset")
    def ws_asset_fetch(request: Request, body: dict = Body(...)):
        """FETCH a remote image INTO the workspace — the act behind the external-image placeholder,
        and behind the rig's `fetch_asset`.

        The server fetches, never the reader's browser: that is the whole rule (#1612). It is also
        the only place that CAN — a page in a bank's workspace must not make that browser talk to a
        third party, and a browser cannot store the result in a workspace anyway.

        AND IT ANSWERS WITH WHOSE FAULT IT WAS (Vexa-ai/vexa#1624). Every failure here used to be
        a 400 — our status code for the reader's request — so a page whose address answers 404 told
        them `400: … answered 404`, which reads as a broken button rather than a dead link. Now the
        three cases are three answers: **400** when the URL itself is refused (it is the request
        that is wrong), **424** when the remote answered and answered badly — its own code carried
        as `upstream_status`, so the client can say *the site answered 404* in words — and **502**
        when nothing usable came back at all."""
        url = str(body.get("url") or "").strip()
        slug = str(body.get("slug") or "").strip() or None
        try:
            content, ctype, final_url = assets_mod.fetch_asset(url)
        except assets_mod.AssetFetchError as exc:
            if getattr(exc, "kind", "") == "refused":
                raise HTTPException(status_code=400, detail=str(exc)) from None
            upstream = getattr(exc, "status", None)
            raise HTTPException(
                status_code=424 if upstream else 502,
                detail={"error": "asset_upstream" if upstream else "asset_unreachable",
                        "message": str(exc), "url": getattr(exc, "url", "") or url,
                        "upstream_status": upstream}) from None
        rel = assets_mod.asset_path_for(url, ctype, str(body.get("path") or ""))
        return _store_asset(request, rel, slug, content, final_url or url)

    @router.put("/api/workspace/asset")
    async def ws_asset_upload(request: Request, file: UploadFile = File(...),
                              path: str = Form(default=""), slug: str = Form(default="")):
        """A PERSON'S image, dropped or pasted onto a page — stored exactly where the agent's is.

        One directory for pictures, whoever put them there: a reader who drops a chart into a page
        and an agent that fetches a logo into it must produce references of the same shape, or the
        page has two kinds of image and only one of them survives being moved.

        PUT on the same path as the read rather than a `/asset/upload` of its own, because the
        terminal's proxy for this route is a STATIC App-Router segment that shadows its sibling
        catch-all: a deeper path under it would need a second proxy file to exist at all, and a
        route whose reachability depends on a directory nobody remembers is a 405 waiting to
        happen (the comment at the top of `[...seg]/route.ts` is that lesson already learned once)."""
        try:
            content = await file.read()
        finally:
            await file.close()
        rel = assets_mod.asset_path_for(file.filename or "", file.content_type or "",
                                        path or _upload_filename(file.filename))
        return _store_asset(request, rel, slug.strip() or None, content, "uploaded here")

    @router.get("/api/workspace/file")
    def ws_file(request: Request, path: str, slug: Optional[str] = None):
        try:
            content = wsr.read_at(_read_target(request, slug), path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"path": path, "content": content}
    @router.put("/api/workspace/file")
    def ws_file_write(request: Request, body: dict = Body(...)):
        """WRITE one doc — the terminal's in-place page editor (Codex-style). Authorization mirrors the
        MOUNT rules, not the read rules: own baseline/_system always; a shared workspace needs
        contributor+; `_global` only the admin allowlist. Commits so history stays honest."""
        import shutil as _sh  # noqa: F401 — parity with ws_reset's import style
        import subprocess as _sp
        subject = subject_of(request)
        rel = str(body.get("path") or "").strip()
        slug = str(body.get("slug") or "").strip() or None
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="need a relative path and string content")
        # #1624: an image address nobody checked never reaches the page (see `_screen_images`).
        content = _screen_images(request, [content], path=rel, tool="workspace_write")[0]
        target = _write_dir(request, subject, rel, slug)
        try:
            f = wpaths.resolve_inside(target, rel)   # …and again WITH the root, for the symlink half
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        if (target / ".git").is_dir():
            _sp.run(["git", "-C", str(target), "add", rel], check=False, capture_output=True)
            _sp.run(["git", "-C", str(target), "-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                     "commit", "-m", f"edit {rel} (terminal page editor)"], check=False, capture_output=True)
        return {"path": rel, "written": True}

    # ── REMOVING AND MOVING A PAGE (Vexa-ai/vexa#1621) ───────────────────────────────────────────
    #
    # Founder, session 176, 13:36Z: *"remove from personal"* — and there was no verb for it. The
    # agent moving the OeNB dossier off the desk had `workspace_write`, which creates or overwrites,
    # and two read-only routes; so it collapsed each of the seven pages to a one-line pointer and had
    # to report that "removed" meant "collapsed". The files stayed on the desk (friction
    # `fr_a373e9448d2909a6`).
    #
    # A DELETE IS HISTORY, NEVER A LOSS. Both verbs commit in the workspace, so the bytes are one
    # `git show` away forever — which is what makes removing a page a safe thing for an agent to do
    # at all, and why neither of these needs a confirmation step the model would have to invent.

    def _movable_dir(request: Request, subject, rel: str, slug: Optional[str]) -> Path:
        """WHICH workspace dir a REMOVAL (or the source/target half of a move) lands in, or the
        refusal. `_write_dir`'s rules — path guard, `kg/templates/`, `_global` admin-only, shared
        needs contributor+, anything outside the active set 403s — plus ONE more.

        `_system` IS NOT A REMOVAL TARGET, ever. It is read-write in the mount stack because chat
        continuity, sessions, settings and `identity.md` live there and the platform writes them;
        that is exactly why an agent verb must not be able to delete out of it. The write route
        deliberately lets `_system` through (a turn recording who it is helping writes there); the
        remove route deliberately does not, at either end of a move. Same reason `_global` is
        admin-only rather than open: the tiers that are not a person's own pages are not a place a
        turn tidies up."""
        if str(slug or "") == system_mounts.SYSTEM_SLUG:
            raise HTTPException(status_code=403, detail=(
                "`_system` holds chats, sessions and identity — the platform writes that tier; "
                "pages are removed from a desk or a workspace, never from it"))
        return _write_dir(request, subject, rel, slug)

    def _kg_index_after(target: Path, *rels: str) -> list[str]:
        """Refresh `kg/INDEX.md` when one of ``rels`` was an ENTITY page, and say so as a pathspec.

        The index is mounted into every dispatch (`worker/engine.entity_index_preamble`), so a page
        removed without it is a page the next turn is still told the workspace holds — and the turn
        after that either rewrites it or cites a file that is not there. Only touched for
        `kg/entities/…`: `write_index` CREATES the file, and a workspace with no entities must not
        grow one because somebody deleted a draft."""
        if not any(str(r).startswith(entities_mod.ENTITIES_DIR + "/") for r in rels if r):
            return []
        try:
            return [entities_mod.write_index(target, target.name)]
        except OSError:
            return []

    #: What is left at the old path when a page moves WITHIN one workspace. A `[[wikilink]]` in this
    #: workspace resolves by path or title, so the link keeps landing — on a page that says where the
    #: real one went. ACROSS workspaces there is no stub, per the containment rule: the other
    #: workspace's readers are not ours to leave a note for, and a stub in a customer's tree naming a
    #: path in ours is a reference they cannot follow.
    def _pointer_stub(new_rel: str) -> str:
        name = new_rel.rsplit("/", 1)[-1]
        return (f"---\nmoved_to: {new_rel}\n---\n\n"
                f"Moved to [{name}]({new_rel}).\n\n"
                f"This page now lives at `{new_rel}`. Its history is in this workspace's git log.\n")

    @router.post("/api/workspace/remove")
    def ws_file_remove(request: Request, body: WorkspaceRemoveBody = Body(...)):
        """REMOVE one page from a workspace, as a commit — the verb behind `workspace_delete`.

        NOT `DELETE /api/workspace/file`, which is what this was written as first: `DELETE
        /api/workspace/{slug}` destroys a whole workspace and `{slug}` matches the literal `file`,
        so the two would match one URL and registration order would pick the winner (see
        `WorkspaceRemoveBody`, and the gate that caught it).

        Refuses a folder (this removes A PAGE, and a recursive delete an agent can call is a
        different decision nobody has made), a path outside the workspace, and every mount the write
        route refuses plus `_system` (see `_movable_dir`). A path that is not there is a 404 — the
        agent is meant to read that and say so, not retry."""
        subject = subject_of(request)
        rel = str(body.path or "").strip()
        slug = (body.slug or "").strip() or None
        target = _movable_dir(request, subject, rel, slug)
        try:
            f = wpaths.resolve_inside(target, rel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if f.is_dir():
            raise HTTPException(status_code=400, detail="that is a folder — this removes one page")
        if not f.is_file():
            raise HTTPException(status_code=404, detail="not found")
        f.unlink()
        sha = _commit(target, [rel, *_kg_index_after(target, rel)],
                      f"{target.name}: {rel} — removed"[:72])
        return {"path": rel, "deleted": True, "workspace": slug or "", "commit": sha}

    @router.post("/api/workspace/move")
    def ws_file_move(request: Request, body: WorkspaceMoveBody = Body(...)):
        """MOVE one page — the verb behind `workspace_move`.

        WITHIN one workspace it is a rename with a POINTER STUB left at the old path, so a
        `[[wikilink]]` written before the move still lands somewhere that says where the page went.
        ACROSS workspaces it is a WRITE IN THE TARGET AND A DELETE IN THE SOURCE — two repositories,
        two commits, no stub (the containment rule: a link out of another workspace is the reader's
        problem to hold, and a stub pointing at a path they cannot open is worse than a dead link).

        BOTH ENDS ARE AUTHORIZED BEFORE ANYTHING IS WRITTEN, and both through `_movable_dir`, so a
        read-only end refuses the whole move rather than half-performing it: `_system` always,
        `_global` unless the caller is an org admin, a shared workspace unless they are a
        contributor. Half a move is the one outcome worse than no move — the page would exist twice,
        or nowhere."""
        subject = subject_of(request)
        src_rel = str(body.from_ or "").strip()
        dst_rel = str(body.to or "").strip()
        src_slug = (body.slug or "").strip() or None
        # NO `to_slug` MEANS THE SAME WORKSPACE — the ordinary rename. Defaulting it to the caller's
        # desk instead would turn every rename inside a shared workspace into a silent extraction of
        # a page out of it, which is the failure `_writeback_workspace_note` already documents for
        # `entity_upsert`'s slug default.
        dst_slug = (body.to_slug or "").strip() or src_slug
        src = _movable_dir(request, subject, src_rel, src_slug)
        dst = _movable_dir(request, subject, dst_rel, dst_slug)
        try:
            src_f = wpaths.resolve_inside(src, src_rel)
            dst_f = wpaths.resolve_inside(dst, dst_rel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if src_f.is_dir():
            raise HTTPException(status_code=400, detail="that is a folder — this moves one page")
        if not src_f.is_file():
            raise HTTPException(status_code=404, detail="not found")
        if src_f.resolve() == dst_f.resolve():
            raise HTTPException(status_code=400, detail="the page is already there")
        same_workspace = src.resolve() == dst.resolve()
        content = src_f.read_bytes()
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        dst_f.write_bytes(content)
        # A STUB IS A PAGE, so only a page gets one. A markdown pointer written over `assets/logo.png`
        # is a broken picture wearing a helpful sentence.
        stub = same_workspace and src_rel.lower().endswith(".md")
        if stub:
            src_f.write_text(_pointer_stub(dst_rel), encoding="utf-8")
        else:
            src_f.unlink()
        subject_line = f"{dst.name}: {dst_rel} — moved from {src_rel}"[:72]
        if same_workspace:
            sha = _commit(src, [src_rel, dst_rel, *_kg_index_after(src, src_rel, dst_rel)],
                          subject_line)
            return {"from": src_rel, "to": dst_rel, "workspace": src_slug or "",
                    "to_workspace": dst_slug or "", "moved": True,
                    "pointer": src_rel if stub else None, "commit": sha, "source_commit": sha}
        # TWO REPOSITORIES, TWO COMMITS — the target first, so the moment between them is one where
        # the page exists twice rather than not at all.
        to_sha = _commit(dst, [dst_rel, *_kg_index_after(dst, dst_rel)], subject_line)
        from_sha = _commit(src, [src_rel, *_kg_index_after(src, src_rel)],
                           f"{src.name}: {src_rel} — moved out"[:72])
        return {"from": src_rel, "to": dst_rel, "workspace": src_slug or "",
                "to_workspace": dst_slug or "", "moved": True, "pointer": None,
                "commit": to_sha, "source_commit": from_sha}

    @router.post("/api/workspace/entity")
    def ws_entity_upsert(request: Request, body: dict = Body(...)):
        """UPSERT one knowledge-graph entity — PRD decision 24, the single call behind `entity_upsert`.

        Creates or updates `kg/entities/<kind>/<slug>.md` AS A CARD (decision 24.6): a summary, the
        kind's sections, `## Connected` chips both ways, `## Sources`, `## Open questions`, and the
        dated log at the end under `## Timeline`. A page in the old flat shape is re-rendered into
        the card on its next touch, entries preserved. Then it refreshes `kg/INDEX.md` and commits
        both by pathspec with the F31 subject shape. Authorization is `ws_file_write`'s, because it
        is the same act — a write into a workspace — and two spellings of one authorization rule is
        how the second one ends up weaker.

        This endpoint exists so the MCP tool can be a THIN FORWARD (PRD §3.3: every host-reaching rig
        tool is a missing endpoint in an owning service wearing a shell command). `workspace_write`'s
        `docker exec` double is the shape this deliberately does not copy.
        """
        subject = subject_of(request)
        kind = str(body.get("kind") or "").strip().lower()
        name = str(body.get("name") or "").strip()
        source = str(body.get("source") or "").strip()
        slug = str(body.get("slug") or "").strip() or None
        raw_facts = body.get("facts")
        if isinstance(raw_facts, str):
            raw_facts = [raw_facts]
        facts = [str(f) for f in (raw_facts or []) if str(f).strip()]
        summary = str(body.get("summary") or "").strip()
        questions = [str(q) for q in (body.get("open_questions") or ())]
        # #1624 — the card's free text goes through the same door a plain page does, in ONE call so
        # a logo named in both the summary and a fact costs the host one question, not two.
        screened = _screen_images(request, [summary, *facts, *questions],
                                  path=f"kg/entities/{kind}/{name}", tool="entity_upsert")
        summary, facts, questions = screened[0], screened[1:1 + len(facts)], screened[1 + len(facts):]

        global_target: Path | None = None
        if slug == system_mounts.GLOBAL_SLUG:
            # `_global` is the company layer. It used to refuse every entity write ("entities belong
            # on a desk, not in it"), and the walk of 2026-09-06 showed what that costs: the setup
            # agent, told to give the company a connected graph, wrote the company's own page onto
            # the administrator's desk, where nobody else will ever read it. The five files stay
            # thin; the graph they link to lives HERE, written by the one identity that may write
            # here at all — the same test the file route runs two screens up.
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403,
                                    detail="only an org admin may write company-tier pages into _global")
            candidates = [Path(settings.workspaces_dir) / system_mounts.GLOBAL_SLUG,
                          Path(settings.global_system_workspace_path or "/nonexistent")]
            global_target = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), None)
            if global_target is None:
                raise HTTPException(status_code=404, detail="the organisation tier is not writable here")
        elif slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError:
                pass  # not shared — _read_target 403s anything outside the active set
        target = global_target or _read_target(request, slug, write=True)
        try:
            # THE MOUNT SET IS HANDED IN so a `[[Name]]` whose page lives in another mounted
            # workspace is stored as `[[ws:<id>/<entity-id>]]` (PRD decision 26.3). Without it the
            # link resolves by TITLE in whichever mount the reader searches first, and dies the
            # moment either workspace is renamed — which is the ordinary case, not the edge.
            # `dates` (decision 31 §3): the whitelisted temporal frontmatter — `scheduled_at`,
            # `held_at`, `report_delivered_at`. Filtered and normalised in `upsert_entity`, so a
            # caller cannot write an arbitrary key by naming it here.
            # THE CARD (decision 24.6). `summary`, `fields`, `section`, `connections` and
            # `open_questions` are what turn an append into a page: a fact arrives knowing which
            # section it belongs in, and a named company becomes an edge in both directions.
            # Everything is optional — a caller that passes only `facts` still gets a card, with
            # the facts in `## Timeline`, which is the shape the migration produces anyway.
            result = entities_mod.upsert_entity(
                target, kind, name, facts, source,
                mounts=_entity_mounts(subject), dates=body.get("dates"),
                summary=summary,
                fields=body.get("fields") if isinstance(body.get("fields"), dict) else None,
                section=str(body.get("section") or "").strip(),
                connections=body.get("connections") or (),
                open_questions=questions)
        except entities_mod.EntityMalformed as e:
            # 400, and CAUGHT FIRST — `EntityMalformed` is a subclass, so the broader clause below
            # would swallow it. This is the one refusal that IS a retry: an argument the writer
            # could not read (a `connections` entry with no name), whose detail names the shape, so
            # the agent's next call is the same facts in a form that lands. Before
            # Vexa-ai/vexa#1589 it was an uncaught KeyError — a 500 naming nothing, with a
            # half-written edge already on the neighbour's page.
            raise HTTPException(status_code=400, detail=str(e))
        except entities_mod.EntityRefused as e:
            # 422, not 400: the request is well-formed and the REFUSAL is the product — the agent is
            # meant to read the sentence and fix the fact, not to retry the call.
            raise HTTPException(status_code=422, detail=str(e))
        index_rel = entities_mod.write_index(target, slug or str(subject))
        sha = None
        if result.get("changed"):
            sha = entities_mod.commit_entity(
                target, [result["path"], index_rel, *result.get("back_links", ())],
                subject_path=result["path"], created=bool(result.get("created")),
                author=(str(subject), f"{subject}@vexa.local"))
        return {**result, "index": index_rel, "commit": sha}
    @router.get("/api/workspace/git")
    def ws_git(request: Request, slug: Optional[str] = None):
        """Author-attributed source-control state (branch · working changes · recent commits) of a
        workspace. No ``slug`` → the caller's own primary. A ``slug`` addresses a SHARED workspace the
        caller is a member of (same authorized resolution as tree/file reads) — its commits carry
        ``author`` + ``kind`` so the terminal can show OTHER members' agent pushes as they land."""
        try:
            target = _read_target(request, slug)  # authorizes: a slug outside the caller's mount set → 403
            return wsr.git_state_at(target, viewer=subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
    @router.get("/api/workspace/git/show")
    def ws_git_show(request: Request, sha: str, slug: Optional[str] = None, path: Optional[str] = None):
        """Unified diff of ONE commit (optionally one file) — same authorized resolution as ws_git — so
        the terminal can highlight exactly what a commit changed. The optional ``path`` is a pathspec
        and goes through the same guard as every other caller-supplied path."""
        try:
            target = _read_target(request, slug)  # authorizes: a slug outside the caller's mount set → 403
            return wsr.git_diff_at(target, sha, path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path or subject")
    @router.get("/api/workspaces/{slug}/git/history")
    def ws_history(slug: str, request: Request, path: Optional[str] = None, limit: int = 20):
        """A WORKSPACE'S RECENT COMMITS, optionally filtered to one page — the history the workspace
        README's front page shows (Vexa-ai/vexa#1623: *"if it's a workspace readme we want to have
        data — shared with whom, controls like github sync, git history lookup"*).

        WHY `git/history` AND NOT `{slug}/history`, which is what #1623 asked for. The shorter path
        OVERLAPS `/api/workspaces/by-slug/{slug}`: one concrete URL — `/api/workspaces/by-slug/history`
        — matches both patterns, so which handler answers would be decided by the order the routers
        happen to be included in. `test_route_table.py` exists to refuse exactly that, and it is
        right to: *"the failure would be a request answered by the wrong handler rather than an error
        anybody sees"*. One more literal segment removes the ambiguity and keeps the slug where the
        issue put it. The alternative — an allowlist entry in the gate that caught this — would have
        traded a real invariant for a URL nobody outside this build consumes yet.

        SCOPED EXACTLY LIKE THE FILE READ, and by the same call rather than by a similar rule:
        ``_read_target`` is what decides, so there is no page whose history a subject can read and
        whose text they cannot. A slug outside the caller's mount set 403s there; a colleague's desk
        falls through to the read-only path there; `_global` answers every subject there. Nothing
        about who may read what is decided in this handler — which is the point, because a second
        spelling of an authorization rule is a second answer waiting to be given.

        TWO NARROWINGS, both deliberate and both refusals rather than widenings:

        ``_system`` IS REFUSED. ``_read_target`` resolves it happily — it is the caller's OWN private
        tier, and reading one's own chats and settings is not a leak. But `_global/POLICIES.md` says
        of it, in as many words, *"`_system` is read by no agent for anybody else — chats, sessions
        and settings are the one genuinely private tier"*, and this route exists to put a workspace's
        history on a page. Sessions are not a workspace's history; there is no `_system` README and
        no panel that would show this. Refusing it here costs nothing anybody asked for.

        ``personal`` IS THE DESK. The terminal's desk tab carries no slug at all (its file reads pass
        none, and ``_read_target`` answers the caller's primary for an absent one) — but a path
        segment cannot be absent, so the desk needs a name here. ``personal`` is the one the rest of
        the system already uses for exactly this (``workspace_attach.PERSONAL_ALIAS``, the MCP, the
        panel's own breadcrumb), and it resolves through the same absent-slug branch rather than
        through a second lookup."""
        if slug == system_mounts.SYSTEM_SLUG:
            raise HTTPException(status_code=403, detail=(
                "_system is sessions and settings, not a workspace with a history"))
        target_slug = None if slug in ("", "personal") else slug
        try:
            target = _read_target(request, target_slug)  # authorizes exactly as the file read does
            return {"slug": slug,
                    **wsr.git_history_at(target, path=path, limit=limit,
                                         viewer=subject_of(request))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid path or subject")

    def _page_head(base, rel: str) -> Optional[str]:
        """The top of one page in a workspace, RAW — the bytes a title is read out of.

        Not ``wsr.read_at``: that prepends the template and unfilled banners, which is right for a
        page an agent is about to read and wrong for this, because a banner in front of the file
        pushes its own front matter out of the leading ``---`` position and takes the ``title:``
        with it. Guarded by the same ``resolve_inside`` every path from a caller goes through."""
        try:
            p = wpaths.resolve_inside(base, rel)
        except (wpaths.PathRefused, OSError, ValueError):
            return None
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                return fh.read(front_page_mod._HEAD_BYTES)
        except OSError:
            return None

    @router.get("/api/workspaces/{slug}/git/last-change")
    def ws_last_change(slug: str, request: Request, path: Optional[str] = None):
        """THE NEWEST COMMIT AS A SENTENCE — what changed, by its TITLE, and who by, by their NAME.

        Founder, 2026-09-06, on the front-page strip (Vexa-ai/vexa#1634): *"never spoke about how to
        make it right, helpful and nice."* The strip was reading a git log out loud — a commit
        subject, an author id, a file count — and the line a person needs is *Changed 14 minutes ago
        by Jane Smith: the policies wizard ask*. Neither half of that is in a git log, so neither
        half can be composed in the client:

          * a commit's **subject** names the file the turn was about while the commit touches
            several (`_global`'s README history is full of `MISSING.md, OBJECTIVES.md +13`), so the
            changed THING is read off the changed pages themselves — their ``title:``, their first
            heading, or their own file name — and several pages become a count;
          * a commit's **author** is the principal a mount commits as (``%an`` = the subject id, D4),
            so the PERSON is resolved through their own page and the company directory, and an
            address is never the answer (``front_page.person_name``).

        SCOPED BY `_read_target`, exactly like `git/history` beside it and the file read beside that —
        the same call, not a similar rule, so this route can describe no commit whose page a subject
        could not open. `_system` is refused here for the reason it is refused there: sessions and
        settings are not a workspace's history, and there is no README to put this on.

        `path` narrows to one page, the way the history's filter does — *when did THIS page last
        change* — and goes through the same guard before it reaches git.

        THE NEWEST COMMIT THAT CHANGED A PAGE, not simply the newest commit. Every workspace's log
        carries plumbing — `<slug>: workspace identity` writing `.vexa/workspace.json`, `policy:
        contributor for … ` writing the roster — and those are real commits that changed nothing a
        person can open. *Changed 2 minutes ago by Vexa: nothing* is the repository-facts sentence
        this issue exists to remove, so the scan walks back over the recent log and answers with the
        first commit that touched a page. When none of them did, the newest commit is described with
        no pages at all and the line degrades to *Changed 2 minutes ago* — which is true."""
        if slug == system_mounts.SYSTEM_SLUG:
            raise HTTPException(status_code=403, detail=(
                "_system is sessions and settings, not a workspace with a history"))
        target_slug = None if slug in ("", "personal") else slug
        try:
            target = _read_target(request, target_slug)   # authorizes exactly as the file read does
            history = wsr.git_history_at(target, path=path, limit=LAST_CHANGE_SCAN,
                                         viewer=subject_of(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid path or subject")
        commits = history.get("commits") or []
        if not commits:
            # NOTHING HAS BEEN COMMITTED HERE, which is an ordinary state of a new workspace and not
            # a failure. The panel says so in words; a 404 would make it render an error.
            return {"slug": slug, "path": history.get("path"), "change": None}
        newest = next((c for c in commits
                       if any(front_page_mod.is_page(p, slug) for p in (c.get("files") or []))),
                      commits[0])
        return {"slug": slug, "path": history.get("path"),
                "change": front_page_mod.describe_commit(
                    newest, slug=slug,
                    read_page=lambda rel: _page_head(target, rel),
                    name_of=lambda author: front_page_mod.person_name(wsr.root, author))}

    @router.get("/api/people/me")
    def people_me(request: Request):
        """WHO THE READER IS, by name — their own `self: true` person page, then the directory.

        One string, and the same resolution the last-change route uses for everybody else, so the
        product cannot call one person two things on one screen. It is here because the front page's
        first sentence needs it: the company layer's line reads *everyone at <company> reads it,
        <first name> writes it*, and the administrator reading their own instance should meet their
        own name rather than the word "administrator".

        `name` is null when nothing has been written down. That is the answer, not an error — the
        line falls back to the role, and no address is ever put in a name's place."""
        subject = str(subject_of(request))
        name = front_page_mod.person_name(wsr.root, subject)
        return {"subject": subject, "name": name,
                "first_name": front_page_mod.first_name(name)}

    @router.post("/api/workspace/git/reset")
    def ws_git_reset(request: Request, body: dict = Body(...)):
        """UNDO commits a run is known to have made on THIS subject's own desk, back to a witnessed
        sha — the internal tier only (Vexa-ai/vexa#1606).

        WHAT IT IS FOR, precisely. `process_meeting` records the organiser's desk HEAD before the
        post-meeting turn and refuses the step if it moved, because decision 22 says that run writes
        no desk. When it fired, the recovery was a HUMAN: reset that repository to the sha in the
        error message, then re-fire the reaction. This is the first half of that recovery, so the
        step can perform it and retry itself instead of stopping a meeting's minutes on a repair
        nobody is awake to make.

        NO ``slug``, EVER — the caller's own primary desk and nothing else. A reset that could name a
        workspace would be a way to rewrite a shared desk, or somebody else's, from the internal
        tier; the one caller this exists for is holding a witness it took itself, of the one desk its
        own dispatch could have moved.

        THE INTERNAL-TIER GATE IS THE WHOLE TRUST BOUNDARY, exactly as it is for the meeting room: a
        browser client through the gateway holds no such secret, so no signed-in person can discard
        their own history by calling this, and no session cookie can be replayed into it.

        BACKWARD ONLY (`wsr.git_reset_to`): the sha must be an ancestor of the current HEAD, so this
        can only remove commits made after the witness — never move a desk onto history it has not
        done. A refusal comes back as `{"reset": false, "detail": …}` with 200, because the caller's
        next move on a refusal is to SAY why, not to retry."""
        if not _internal_caller(request):
            raise HTTPException(status_code=403,
                                detail="resetting a desk to a witnessed sha is an internal-tier capability")
        sha = str((body or {}).get("sha") or "").strip()
        if not sha:
            raise HTTPException(status_code=400, detail="`sha` is required — the witness to reset to")
        try:
            target = wsr.workspace_dir(subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        out = wsr.git_reset_to(target, sha)
        if out.get("reset"):
            logger.warning("workspace reset: subject=%s %s -> %s (reason=%s)", subject_of(request),
                           str(out.get("before"))[:9], str(out.get("after"))[:9],
                           str((body or {}).get("reason") or "")[:120])
        return out
    @router.post("/api/workspace/init", status_code=201)
    def ws_init(request: Request):
        """EAGERLY provision this subject's workspace tiers — the "on account creation" seam (so the
        Personal baseline + the private `_system` tier exist BEFORE the first dispatch, instead of being
        lazily seeded on first turn). Materializes the baseline from the VALIDATED workspace-seed template
        (shared.seeding.seed_workspace) and ensures `_system` (system_mounts.ensure_system_workspace).
        Idempotent — existing tiers (`.git` present) are returned untouched, so it's safe to call on every
        login. The same seams the worker uses lazily on first dispatch, surfaced as a control."""
        subject = subject_of(request)
        ws = wsr.workspace_dir(subject)
        # Select the seed out of the registry root (default template for now; per-request template
        # selection lands with the second seed). VEXA_WORKSPACE_SEED_DIR still overrides.
        seed_dir = resolve_seed_dir(
            settings.default_template if settings is not None else None,
            seeds_root=settings.workspace_seeds_dir if settings is not None else None,
        )
        problems = validate_seed(seed_dir)
        if problems:
            raise HTTPException(status_code=500, detail="invalid workspace seed: " + "; ".join(problems))
        existed = (ws / ".git").exists()
        seed_workspace(ws, seed_dir)
        # The PRIVATE SYSTEM tier (`_system`) — always-mounted, holds the light identity reference. Ensure
        # it up front too so identity + chats/settings have a home from the very first turn. Idempotent.
        system_existed = (system_mounts.system_store_path(wsr.root, subject) / ".git").exists()
        system_mounts.ensure_system_workspace(str(wsr.root), subject)
        # The desk's id + registry row. Named by the address that signed in when the gateway gave
        # us one: a desk called "Desk 126" is better than `126` and worse than the person's own
        # address, and this is the one seam that has the address.
        _ws_sync(str(subject), kind="desk", owner=str(subject), ws_dir=ws,
                 name=(request.headers.get("x-user-email") or "").strip() or None)
        # THE DESK EXISTS AND NOBODY HAS FINISHED SETTING IT UP — the agent domain's fact to tell
        # (PRD ruling 9; the carrier flows' `desk_setup` reacts to). Told ONLY on the call that
        # created the desk: this route is idempotent and called on every login, so a publish per
        # call would put one card on the queue per sign-in, forever, for the very person who never
        # finished — and a card a person sees daily is a card they learn to ignore.
        #
        # `.scaffolded` is the marker a finished setup leaves (`flows_steps.common.scaffolded`); a
        # desk that already carries one is waiting for nothing. It cannot normally be there on a
        # freshly seeded desk, and it is checked anyway because the alternative — asking somebody
        # to finish what they already finished — is the one failure a queue card cannot survive.
        #
        # Fire-and-forget. A publish is not a dependency: the return value is dropped, the call is
        # bounded at 2s and swallowed, and a deployment with no flows domain provisions this desk
        # in exactly the same way.
        if not existed and not (ws / ".scaffolded").exists():
            publish_mod.publish(publish_mod.EVENT_DESK_UNSCAFFOLDED,
                                publish_mod.desk_source_id(subject),
                                publish_mod.desk_refs(subject))
        return {"workspace": str(ws), "seeded": not existed, "already_initialized": existed,
                "system_seeded": not system_existed}

    @router.post("/api/claims")
    def write_claims(request: Request, body: dict = Body(...)):
        """Record what an agent believes about this person's company as PROPOSED — and tell flows,
        once per claim.

        THIS ROUTE EXISTS SO THE FACT HAS A PRODUCER. The book was written through
        `PUT /api/workspace/file`, a generic route that holds bytes and knows nothing about what
        they mean — so the one moment worth telling anybody about, a claim being proposed, was
        indistinguishable from any other file write, and `claim.proposed` had no publisher. A
        generic route cannot publish a specific fact without inspecting paths and guessing at
        contents, which is how a file route quietly becomes a state machine nobody declared.

        ONE EVENT PER CLAIM, and only for claims this call actually added: `await_claim` looks a
        `claim_id` up in the book and blocks on that claim's own words, so one event for a batch
        would be one card for three questions with no way to answer two of them.

        It lives in the workspaces router because the book is a file on a desk and everything this
        needs — the subject, the reader, the desk path — is already closed over here; the state
        machine itself is `control_plane.claims`, which is the concern."""
        subject = subject_of(request)
        batch = (body or {}).get("claims") or []
        if not isinstance(batch, list) or not batch:
            raise HTTPException(status_code=400, detail="claims must be a non-empty list")
        result = claims_mod.propose(wsr.workspace_dir(subject), batch)
        for cid in result["ids"]:
            publish_mod.publish(publish_mod.EVENT_CLAIM_PROPOSED,
                                publish_mod.claim_source_id(subject, cid),
                                publish_mod.claim_refs(subject, cid))
        return result
    @router.get("/api/workspaces/by-slug/{slug}")
    def ws_id_by_slug(slug: str, request: Request):
        """The identity of a workspace addressed the OLD way — by slug. What the terminal calls to
        put a NAME where it used to print a directory name (F49: the chat header read `126`)."""
        subject = subject_of(request)
        rec = workspace_registry.by_slug(slug) or _ws_sync(slug)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        return ids_mod.view(rec, access, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member))
    @router.get("/api/workspaces/{workspace_id}")
    def ws_id_resolve(workspace_id: str, request: Request):
        """`{id, name, kind, access}` for one workspace id, from THIS reader's point of view.

        Never 404s and never 403s: `not-yours` and `gone` are ANSWERS (decision 26.3), and a status
        code would make the client render an error where the design says render a greyed chip."""
        subject = subject_of(request)
        rec = workspace_registry.get(workspace_id)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        return ids_mod.view(rec, access, workspace_id=workspace_id, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member))
    @router.post("/api/workspaces/{workspace_id}/rename")
    def ws_id_rename(workspace_id: str, request: Request, body: dict = Body(default={})):
        """Rename a workspace. The id does not move, and neither does anything pointing at it —
        that is the single behaviour PRD decision 26 was asked for, and this is the route that
        exercises it.

        WHO (founder ruling, 2026-09-02): the group's OWNER, and instance admins. Nobody else, and
        deliberately not a desk's own owner — a desk's name comes from the address that signed in,
        and renaming one is a question about identity display that has not been asked yet.

        AUDITED: who, old, new, when, kept on the record (capped) and logged. A rename is the one
        operation whose whole point is that nothing else changes, which means the only way to see
        that it happened at all is to have written it down."""
        subject = subject_of(request)
        try:
            rec = ids_mod.rename_audited(
                workspace_registry, workspace_id, str(body.get("name") or ""), by=str(subject),
                is_admin=bool(global_layer.is_admin(settings, str(subject))),
                root=wsr.root, is_member=_ws_is_member)
        except KeyError:
            raise HTTPException(status_code=404, detail="no workspace with that id")
        except ids_mod.RenameRefused as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {**ids_mod.view(rec, ids_mod.ACCESS_READABLE, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member)),
            "renamed_from": (rec.get("renames") or [{}])[-1].get("from")}
    @router.get("/api/workspace/attached")
    def ws_attached(request: Request):
        """The subject's attachment view: the active slug + the parked workspaces available to swap back
        to, plus ``published_url`` — where the ACTIVE workspace was published (the ``vexa-publish``
        remote's token-free URL), or null when it never was. The client renders a published workspace
        with a link to its GitHub home instead of the publish action."""
        subject = subject_of(request)
        try:
            state = attached_workspaces(wsr.root, subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        state["published_url"] = published_remote_url(wsr.workspace_dir(subject))
        return state
    @router.post("/api/workspace/swap")
    def ws_swap(request: Request, body: WorkspaceSwapBody = Body(default=WorkspaceSwapBody())):
        """Attach a CUSTOM external git repo as this subject's active workspace (swap). The currently
        active workspace is PARKED (kept, never destroyed) so it can be swapped back to; the requested
        repo is restored from a prior park or cloned fresh. Omit ``repo`` to swap back to the seed.

        Mounting is by-folder (``<root>/<subject>`` is what the next dispatch mounts), so the swapped
        tree takes effect on the subject's next turn — no dispatch change needed."""
        subject = subject_of(request)
        repo = _repo(body.repo)      # 422 before any git process exists
        key = deploy_keys_mod.workspace_key(subject=subject)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                result = swap_workspace(wsr.root, subject, repo, body.ref or "main",
                                        slug=body.slug or None, fresh=body.fresh, token=cred.token,
                                        clone=_clone_fn(cred))
        except repo_ref.RepoRefError as exc:
            raise HTTPException(status_code=422, detail=exc.sentence)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            # Already token-redacted (P15). A private repo we hold no credential for lands here — and
            # the answer is the workspace's public key to add, not a prompt for a secret.
            raise _credential_refusal(f"git clone failed: {redact_secrets(exc)}", subject, None, repo or "")
        return {
            "subject": result.subject,
            "active": result.active_slug,
            "repo": result.repo,
            "ref": result.ref,
            "swapped": result.swapped,
            "cloned": result.cloned,
            "parked": result.parked_slug,
            "nested": result.nested,
        }
    @router.get("/api/workspace/desk")
    def ws_desk(request: Request):
        """IS THERE A DESK HERE YET, and has anybody worked in it (Vexa-ai/vexa#1613).

        The terminal used to answer this for itself by reading `.scaffolded` — and got it wrong for
        the founder on 2026-09-06: a brand-new chat offered him *"My email is …, set up a workspace
        for me"* over a desk that had existed since 13:30 with company, person and project entities
        in it. `.scaffolded` is written by exactly ONE route (the personal onboarding conversation,
        as its final act) and `flows_defs/production.py` says of it in as many words: *"survives as
        a harmless marker; it gates nothing"* (decision 22a). Its absence therefore means "that
        particular conversation did not happen", which is not the same question and has not been the
        same answer since the desk got other ways to come into existence.

        `desk_state` IS the question, and it has been on the server the whole time — the same three
        words (`new` · `pile` · `warm`) a scaffold's facts block already tells the agent about the
        person it is opening to. It reads the FILES, so it cannot disagree with what is there.

        The marker is returned beside it rather than dropped: a finished setup is still a positive
        fact and the fastest one, and a client that has both can fail closed on either."""
        subject = subject_of(request)
        try:
            state = scaffolds_mod.desk_state(wsr.root, subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        marker = (Path(wsr.root) / str(subject) / ".scaffolded").exists()
        return {"subject": subject, "state": state, "scaffolded": marker}
    @router.get("/api/workspace/active")
    def ws_active(request: Request):
        """The subject's ordered ACTIVE SET — the workspaces the next dispatch mounts (the private baseline
        first, then any activated extras). Each: ``slug, repo, ref, role, path, write, primary``."""
        subject = subject_of(request)
        try:
            mounts = active_workspaces(wsr.root, subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        # Lane A: append the SHARED workspaces the subject is a member of. Enumeration reconciles BOTH
        # membership stores (index ∪ policy/members.json — the same union as the shared listing) so a dead
        # or incomplete index cannot silently drop a locally-held grant from the mount set;
        # shared_active_mounts still re-checks the role authoritatively per workspace. A resolution failure
        # costs the "shared" section of the set, never the subject's own private mounts — and
        # ``index_degraded`` says out loud when the mirror could not be read.
        rows, index_degraded = membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        try:
            mounts = mounts + shared_active_mounts(wsr.root, subject, rows)
        except Exception:  # noqa: BLE001 — a shared-mount resolution hiccup must not break the active-set read
            logger.warning("shared-mount resolution failed for subject=%s — returning private mounts only", subject)
        return {
            "subject": subject,
            "active": [
                {"slug": m.slug, "repo": m.repo, "ref": m.ref, "role": m.role,
                 "path": m.path, "write": m.write, "primary": m.primary, "name": m.name}
                for m in mounts
            ],
            "index_degraded": index_degraded,
        }
    @router.post("/api/workspace/activate")
    def ws_activate(request: Request, body: WorkspaceActivateBody = Body(default=WorkspaceActivateBody())):
        """ADD a workspace to the active set WITHOUT parking the others (the additive counterpart of swap).
        Clones/restores the target if needed. Idempotent — an already-active workspace is a no-op."""
        subject = subject_of(request)
        repo = _repo(body.repo)      # 422 before any git process exists
        key = deploy_keys_mod.workspace_key(subject=subject)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                result = activate_workspace(wsr.root, subject, repo, body.ref or "main",
                                            slug=body.slug or None, token=cred.token,
                                            clone=_clone_fn(cred))
        except repo_ref.RepoRefError as exc:
            raise HTTPException(status_code=422, detail=exc.sentence)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            raise _credential_refusal(f"git clone failed: {redact_secrets(exc)}", subject, None, repo or "")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed,
                "cloned": result.cloned, "nested": result.nested}
    @router.post("/api/workspace/new", status_code=201)
    def ws_new(request: Request, body: WorkspaceNewBody = Body(default=WorkspaceNewBody())):
        """CREATE a brand-new BLANK workspace (seeded from the template) at a fresh unique slug and ADD it
        to the active set (additive — the "new workspace" action). Nothing is parked/rebuilt/backed up: the
        private baseline and every other active workspace stay exactly as they were."""
        subject = subject_of(request)
        try:
            result = create_workspace(wsr.root, subject, name=body.name or None)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed,
                "added": True}
    @router.post("/api/workspace/deactivate")
    def ws_deactivate(request: Request, body: WorkspaceDeactivateBody = Body(...)):
        """REMOVE a workspace from the active set (park it — never destroyed). The private baseline can be
        switched off too (sets ``baseline_hidden``; its home tree is untouched, re-activate to switch it back
        on). Idempotent — an already-off / not-active slug is a no-op."""
        subject = subject_of(request)
        try:
            result = deactivate_workspace(wsr.root, subject, body.slug)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed}
    @router.post("/api/workspace/publish")
    def ws_publish(request: Request, body: WorkspacePublishBody = Body(...)):
        """Publish this subject's vexa-born workspace to GitHub — the counterpart of swap/attach.
        Creates the repo under the caller's account (or ``org``) with their per-call PAT, then pushes
        the active workspace's current branch (FULL history) over the token-scrubbed dedicated remote.
        ``remote_url`` skips creation (pre-created/empty repo). Re-publish = plain push (fast-forward
        or a clear error on divergence — never a force push). The token is used server-side for this
        call only and never stored; every error is token-redacted (P15)."""
        subject = subject_of(request)
        token = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)
        if not token:
            raise HTTPException(status_code=400, detail="a GitHub token is required — pass one or save a reusable token")
        try:
            result = publish_workspace(
                wsr.root, subject,
                token=token, repo_name=body.repo_name, private=body.private,
                org=body.org or None, remote_url=body.remote_url or None,
                # slug → any workspace the caller can manage (own parked slot or shared membership,
                # resolved + permission-checked by _manage_dir); omitted keeps the legacy seed target.
                ws_dir=_manage_dir(subject, body.slug) if body.slug else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid subject")
        except RepoExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))   # already token-redacted (P15)
        except PublishError as exc:
            raise HTTPException(status_code=502, detail=str(exc))   # already token-redacted (P15)
        return {
            "repo_url": result.repo_url,
            "pushed_ref": result.pushed_ref,
            "head_sha": result.head_sha,
            "created": result.created,
        }
    @router.post("/api/workspace/rename")
    def ws_rename(request: Request, body: WorkspaceRenameBody = Body(...)):
        """Rename a workspace slot — a DISPLAY label only. The slug and the parked tree are unchanged, so
        swap-back and repo re-attach keep matching. Pass an empty ``name`` to clear the label."""
        subject = subject_of(request)
        try:
            return rename_workspace(wsr.root, subject, body.slug, body.name)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
    @router.get("/api/workspace/git-token")
    def ws_git_token_get(request: Request):
        """Whether the caller has a SAVED reusable GitHub token, and a masked (last-4) preview of it. The
        clear value is NEVER returned — server-side only (git_credentials)."""
        subject = subject_of(request)
        return {"set": git_creds.read_github_token(wsr.root, subject) is not None,
                "masked": git_creds.masked_github_token(wsr.root, subject)}
    @router.post("/api/workspace/git-token")
    def ws_git_token_set(request: Request, body: GitTokenBody = Body(default=GitTokenBody())):
        """Save (or CLEAR, with an empty token) the caller's reusable GitHub token — stored once, server-
        side, and applied as the fallback credential for every git op across all their repos. Returns the
        masked state, never the clear value."""
        subject = subject_of(request)
        try:
            stored = git_creds.set_github_token(wsr.root, subject, body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"set": stored, "masked": git_creds.masked_github_token(wsr.root, subject)}
    @router.get("/api/workspace/git-remote-status")
    def ws_git_remote_status(request: Request, slug: Optional[str] = None):
        """The GitHub-sync state of a workspace (default = the caller's primary; ``slug`` = one of their
        own or shared workspaces). Read-only + no network: reports the home remote (origin / vexa-publish),
        its URL, the branch, and ahead/behind counts vs the last-fetched tracking ref. No token needed.

        ``_global`` RESOLVES THROUGH THE READ GATE, not through ``_manage_dir`` (Vexa-ai/vexa#1628).
        The company layer is nobody's slot and nobody's membership, so ``_manage_dir`` answered 404 —
        and the workspace README's panel, having asked a true question and been told the workspace
        does not exist, rendered `not readable` with `Could not read the GitHub state.` in red, to
        the administrator, about a tier that simply has no remote. *No repo attached* and *the read
        failed* are different facts and the route was making them one.

        The fallback is the READ target and only for this one slug — the same call
        ``/api/workspace/git`` already uses to report the company layer's branch and commits, so this
        route is not learning to see anything the one beside it could not. It is deliberately NOT a
        general fallback: ``_read_target`` falls through to another person's DESK on a read (the
        2026-09-02 ruling), and a desk's remote URL is not something this route has ever answered —
        widening it here would be a seam change nobody asked for, and
        `test_shared_workspace_attach.py` pins that 404. Every WRITE (push · pull · detach) stays on
        ``_manage_dir``, untouched, so this adds no way to change anything."""
        subject = subject_of(request)
        ws = (_read_target(request, slug) if slug == system_mounts.GLOBAL_SLUG
              else _manage_dir(subject, slug))
        s = remote_status(ws)
        return {
            "has_home": s.has_home, "remote": s.remote, "url": s.url, "branch": s.branch,
            "tracked": s.tracked, "ahead": s.ahead, "behind": s.behind,
        }
    @router.post("/api/workspace/push")
    def ws_push(request: Request, body: WorkspacePushBody = Body(...)):
        """Push a workspace's current branch to its GitHub home (origin for attached clones, vexa-publish
        for published vexa-born), fast-forward only — NEVER a force push. The token authenticates the push
        and is never stored; a diverged remote fails loud (pull first). Every error is token-redacted (P15)."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                if cred.kind == "none":
                    raise HTTPException(status_code=400, detail=(
                        "this workspace has no credential — add its deploy key to the repository "
                        "(POST /api/workspace/{slug}/deploy-key) or save a reusable GitHub token"))
                r = push_origin(ws, token=cred.token, ssh_env=cred.ssh_env)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(redact_secrets(exc), subject, body.slug, home.url or "")  # P15
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha}
    @router.post("/api/workspace/pull")
    def ws_pull(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """Fetch + FAST-FORWARD a workspace from its GitHub home. A divergence (local commits the remote
        lacks) is refused — no merge/rebase/force — so it is resolved deliberately. The token (optional for
        public repos) is used for the fetch only and never stored (P15)."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        # A pull REWRITES the tree, so on a shared workspace it is a write: viewers are refused here even
        # though they may read the same workspace through _manage_dir.
        _require_shared_write(subject, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                r = pull_origin(ws, token=cred.token, ssh_env=cred.ssh_env)  # None ⇒ public-repo fetch
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(redact_secrets(exc), subject, body.slug, home.url or "")  # P15
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha,
                "updated": r.updated, "behind_before": r.behind_before}
    @router.post("/api/workspace/git-remote-detach")
    def ws_git_remote_detach(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """DETACH a workspace from its GitHub home — the inverse of *Attach existing repo…*, and the
        fourth of the four sync controls the workspace README shows (Vexa-ai/vexa#1623).

        It removes the remote and NOTHING else: no file changes, no commit, no deletion. That is what
        makes it safe to offer beside push and pull — a person who says *stop syncing this to GitHub*
        has not said *give me back the tree that was here before it*, and the two acts are only
        confusable if you implement the first as the second.

        The gate is the PULL gate, deliberately, and not a laxer one: a pull rewrites the tree and a
        detach changes where the tree can go, so both are writes to the same workspace plumbing and
        both refuse a viewer. ``_manage_dir`` resolves only the caller's own slots and workspaces
        they belong to; ``_require_shared_write`` then refuses a shared workspace they merely read.

        The RECEIPT is the returned pair plus the log line — the two facts a person needs afterwards
        are *which remote went* and *what its URL was*, and neither survives in git once it is gone."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        _require_shared_write(subject, body.slug)
        try:
            gone = detach_home(ws)
        except RemoteSyncError as exc:
            raise HTTPException(status_code=502, detail=redact_secrets(exc))   # P15
        if gone is None:
            return {"detached": False, "remote": None, "url": None,
                    "detail": "this workspace has no GitHub home"}
        remote, url = gone
        logger.warning("workspace detached from its home: subject=%s slug=%s remote=%s",
                       subject, body.slug or "(primary)", remote)
        return {"detached": True, "remote": remote, "url": url}
    @router.get("/api/workspace/purpose")
    def ws_purpose_get(request: Request, slug: Optional[str] = None):
        """Read a workspace's PURPOSE one-liner (default = the caller's primary; ``slug`` = one of their
        own or shared workspaces). ``""`` when unset."""
        subject = subject_of(request)
        ws = _manage_dir(subject, slug)
        return {"purpose": read_purpose(ws)}
    @router.post("/api/workspace/purpose")
    def ws_purpose_set(request: Request, body: WorkspacePurposeBody = Body(default=WorkspacePurposeBody())):
        """Set (or clear) a workspace's PURPOSE — stored in the workspace + committed so it travels when
        shared and feeds the mount preamble. Returns the normalized purpose actually stored."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        return {"purpose": write_purpose(ws, body.purpose)}
    @router.post("/api/workspace/shared/{workspace_id}/attach")
    def ws_shared_attach(workspace_id: str, request: Request, body: SharedAttachBody = Body(default=SharedAttachBody())):
        """Attach an EXISTING git repo as a shared workspace's tree. Contributor or owner only — a
        viewer may read the group's workspace, never replace it.

        Park-and-clone, exactly as the desk swap: the current tree is kept under the workspace's own
        store and can be swapped back to by slug with no re-clone, so this is reversible. ``policy/``
        (the member list) is carried into the new tree, so an attach can never lock the group out."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
        except MembershipError as exc:
            raise _member_error(exc)
        repo = _repo(body.repo)      # 422 before any git process exists
        key = deploy_keys_mod.workspace_key(workspace_id=workspace_id)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                clone = _clone_fn(cred)
                result = attach_shared_workspace(wsr.root, workspace_id, repo, body.ref or "main",
                                                 slug=body.slug or None, token=cred.token, clone=clone)
        except repo_ref.RepoRefError as exc:
            raise HTTPException(status_code=422, detail=exc.sentence)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace id")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:   # message already token-redacted (P15)
            raise _credential_refusal(f"git clone failed: {redact_secrets(exc)}", subject, workspace_id, repo or "")
        return {
            "workspace_id": workspace_id, "active": result.active_slug, "repo": result.repo,
            "ref": result.ref, "attached": result.swapped, "cloned": result.cloned,
            "parked": result.parked_slug, "nested": result.nested,
            "state": ("cloned" if result.cloned else "restored" if result.swapped else "already attached"),
        }
    @router.get("/api/workspace/shared/{workspace_id}/attached")
    def ws_shared_attached(workspace_id: str, request: Request):
        """A shared workspace's attachment view — the active slug and the parked trees available to swap
        back to, plus its GitHub home. Any member may read it (it says WHAT is mounted, never a credential)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "viewer")
        except MembershipError as exc:
            raise _member_error(exc)
        try:
            state = shared_attached_state(wsr.root, workspace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace id")
        ws = membership_mod._ws_dir(wsr.root, workspace_id)
        st = remote_status(ws)
        key = deploy_keys_mod.workspace_key(workspace_id=workspace_id)
        state["home"] = {"remote": st.remote, "url": st.url, "branch": st.branch,
                         "ahead": st.ahead, "behind": st.behind}
        # A CAPABILITY, not a secret: whether a credential exists and of what kind — never its value.
        state["credential"] = wcreds.home_capability(wsr.root, key=key, remote=st.remote, url=st.url,
                                                    subject=subject)
        return state
    @router.get("/api/workspace/{slug}/deploy-key")
    def ws_deploy_key_get(slug: str, request: Request):
        """This workspace's PUBLIC deploy key (null when none has been generated). The private half has
        no read path at all — it is sealed in the credential store and materialized only for one git op."""
        subject = subject_of(request)
        _manage_dir(subject, slug)          # authorization: own slot, or a workspace they belong to
        key = _workspace_key(subject, slug)
        pub = deploy_keys_mod.public_key(wsr.root, key)
        return {"slug": slug, "public_key": pub, "fingerprint": deploy_keys_mod.fingerprint(pub),
                "add_as": "a deploy key with WRITE access"}
    @router.post("/api/workspace/{slug}/deploy-key")
    def ws_deploy_key_ensure(slug: str, request: Request, body: dict = Body(default={})):
        """Generate this workspace's deploy key (idempotent — a second call returns the SAME key, so a
        key the person already added to their repo is never invalidated) and say where to add it.

        This is the credential model: they add our PUBLIC key to their repository; nothing of theirs
        ever travels to us, and the private half is sealed at rest and never leaves this server."""
        subject = subject_of(request)
        _manage_dir(subject, slug)
        repo_url = str((body or {}).get("repo") or "")
        try:
            prompt = wcreds.deploy_key_prompt(wsr.root, key=_workspace_key(subject, slug), repo_url=repo_url)
        except deploy_keys_mod.DeployKeyError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"slug": slug, **prompt, "message": wcreds.prompt_sentence(prompt)}
    @router.post("/api/workspace/shared/{workspace_id}/active")
    def ws_shared_active(workspace_id: str, request: Request, body: SharedActiveBody = Body(...)):
        """Switch a SHARED workspace ON/OFF in the caller's active set (mount vs hide). Membership is
        unchanged — this is a per-user mount preference so a member can 'switch it off' without leaving."""
        subject = subject_of(request)
        try:
            set_shared_active(wsr.root, subject, workspace_id, body.active)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"workspace_id": workspace_id, "active": body.active}
    @router.post("/api/workspace/{slug}/archive")
    def ws_archive(slug: str, request: Request, body: ArchiveBody = Body(default=ArchiveBody())):
        """Archive (collapse, keep the data) or un-archive one of the caller's own workspaces."""
        subject = subject_of(request)
        try:
            set_archived(wsr.root, subject, slug, body.archived)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {"slug": slug, "archived": body.archived}
    @router.delete("/api/workspace/{slug}")
    def ws_delete(slug: str, request: Request):
        """DELETE one of the caller's own workspaces — removes the data irreversibly. Baseline is refused."""
        subject = subject_of(request)
        try:
            delete_workspace(wsr.root, subject, slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {"slug": slug, "deleted": True}
    @router.post("/api/workspace/reset")
    def ws_reset(request: Request, body: dict = Body(...)):
        """RESET a structural folder to the seed — the delete-equivalent for tiers whose SLOT must
        survive: the caller's `personal` baseline, or `_global` (admin allowlist only). Both are
        "just folders" (founder ruling 2026-08-22) — wipe the content, re-copy the seed, commit.
        `_system` is deliberately NOT resettable: it is sessions/continuity, not knowledge."""
        import shutil as _sh
        import subprocess as _sp
        subject = subject_of(request)
        target = str(body.get("target") or "")
        if target == "_global":
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only an org admin may reset _global")
            if not settings.global_system_workspace_path:
                raise HTTPException(status_code=404, detail="no _global configured")
            # write through the WORKSPACES-DIR mount (rw in dev) — the host-path mirror mount is ro
            candidates = [Path(settings.workspaces_dir) / "_global", Path(settings.global_system_workspace_path)]
            path = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), candidates[0])
        elif target == "personal":
            path = Path(wsr.workspace_dir(subject))
        else:
            raise HTTPException(status_code=400, detail="reset targets: personal | _global (shared workspaces are deleted; _system is never touched)")
        if not path.is_dir():
            raise HTTPException(status_code=404, detail="workspace not found")
        seed = resolve_seed_dir(seeds_root=os.environ.get("VEXA_WORKSPACE_SEEDS_DIR"))
        for child in path.iterdir():
            if child.name == ".git":
                continue
            _sh.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        _sh.copytree(seed, path, dirs_exist_ok=True)
        if (path / ".git").is_dir():
            _sp.run(["git", "-C", str(path), "add", "-A"], check=False, capture_output=True)
            _sp.run(["git", "-C", str(path), "-c", "user.name=vexa-platform", "-c", "user.email=platform@vexa.local",
                     "commit", "-m", f"reseed {target}"], check=False, capture_output=True)
        return {"target": target, "reset": True}
    @router.post("/api/workspace/{workspace_id}/unshare")
    def ws_unshare(workspace_id: str, request: Request):
        """UN-SHARE a workspace (owner only) — move it back into the caller's PRIVATE store and drop every
        member's index entry, so it stops being shared (mirror of share-enable). Returns the new private slug."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            members = membership_mod.read_members(wsr.root, workspace_id)
            new_slug = ensure_workspace_private(wsr.root, subject, workspace_id)
        except MembershipError as exc:
            raise _member_error(exc)
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        for m in members:  # best-effort: the shared workspace is gone, so drop the derived index entries
            try:
                mindex.remove(m.get("subject"), workspace_id)
            except Exception:  # noqa: BLE001
                pass
        # The tree moved into the caller's private store and stopped being a group. Its id did NOT
        # change — un-sharing is an administrative act, not a new workspace — so every link into it
        # keeps resolving, and for everyone else it now answers `not-yours`, which is the truth.
        _ws_sync(new_slug, kind="desk", owner=subject,
                 ws_dir=workspace_slot_dir(wsr.root, subject, new_slug))
        return {"slug": new_slug}
    @router.post("/api/workspace/{slug}/share-enable")
    def ws_share_enable(slug: str, request: Request):
        """Make one of the caller's OWN workspaces shareable (promote a private workspace to a top-level
        shared one if needed) and ensure the caller is its owner. Returns the shareable workspace_id — the
        caller then mints invites against it. This is what lets ANY workspace be shared AFTER creation, with
        no share-vs-not decision at create time."""
        subject = subject_of(request)
        try:
            workspace_id, promoted = ensure_workspace_shareable(wsr.root, subject, slug)
            if promoted:
                membership_mod.ensure_owner(wsr.root, workspace_id, subject, index=mindex,
                                            email=request.headers.get("x-user-email"), commit_fn=_pc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        except MembershipError as exc:
            raise _member_error(exc)
        return {"workspace_id": workspace_id, "promoted": promoted}
    @router.post("/api/workspace/shared/new", status_code=201)
    def ws_shared_new(request: Request, body: SharedNewBody = Body(default=SharedNewBody())):
        """CREATE a new shared workspace and make the caller its OWNER — the bootstrap for the share flow.
        A fresh top-level workspace (git-inited + seeded) is created at <root>/<workspace_id>; the caller is
        granted owner in BOTH stores (policy/members.json + the index). The caller can then mint invites."""
        subject = subject_of(request)
        try:
            wid = create_shared_workspace_dir(wsr.root, body.name)
            membership_mod.ensure_owner(wsr.root, wid, subject, index=mindex,
                                        email=request.headers.get("x-user-email"), commit_fn=_pc)
            # The id is minted HERE, at creation, for the same reason it exists at all: this is the
            # only moment the workspace's human NAME is known. `create_shared_workspace_dir`
            # slugifies it into a directory and drops it, so without this line "ASWF DNA Project"
            # never existed anywhere and the group could only ever be called
            # `aswf-dna-project-b7b2ee`.
            _ws_sync(wid, kind="group", name=(body.name or "").strip() or None, owner=subject)
        except MembershipError as exc:
            raise _member_error(exc)
        except Exception as exc:  # noqa: BLE001 — surface a clean 500 (dir/seed failure) rather than a stack
            logger.exception("shared-workspace create failed for subject=%s", subject)
            raise HTTPException(status_code=500, detail="could not create shared workspace")
        return {"workspace_id": wid, "role": "owner", "name": body.name}
    @router.post("/api/workspace/invites", status_code=201)
    def ws_invite_create(request: Request, body: InviteCreateBody = Body(...)):
        """Mint a scoped invite token for a shared workspace. Auth: owner OR contributor of the target.
        The workspace must be shareable (reserved/own-private refused). The token is returned ONCE; only
        its hash is persisted in policy/invites.json."""
        subject = subject_of(request)
        # AN ADDRESS BINDS THE INVITE (Vexa-ai/vexa#1635). `allowed_emails` names who this is for, so
        # it decides the mode — it is not a hint that a separate flag has to agree with. Asking for
        # both at once ("these addresses, and also anyone") is a contradiction and is refused rather
        # than resolved in one direction the caller cannot see.
        emails = [e for e in (body.allowed_emails or []) if str(e).strip()]
        mode = body.mode
        if emails and mode == "open":
            raise HTTPException(status_code=400,
                                detail="an invite that names addresses is bound to them — drop "
                                       "allowed_emails for an open link, or drop mode=open")
        if mode is None:
            mode = "restricted" if emails else "open"
        try:
            membership_mod.require_role(wsr.root, body.workspace_id, subject, "contributor")
            minted = membership_mod.mint_invite(
                wsr.root, body.workspace_id, role=body.role, created_by=subject,
                expires_in_sec=body.expires_in_sec, max_uses=body.max_uses,
                mode=mode, allowed_emails=emails or None, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        # THE LINK IS COMPOSED HERE, on the deployment's declared public app URL — `VEXA_UI_URL`, the
        # same one variable every scaffold link is built on, because two spellings of the host is how
        # a link ends up naming somewhere the person cannot reach. The rig used to compose it from
        # the MCP host it publishes ITSELF under, and the founder opened `rig.dev.vexa.ai/join?i=…`
        # and got *"not found"*: a client knows where it is, only the deployment knows where the
        # person's terminal is. Unset ⇒ `invite_url` is null and the caller is told which key names
        # it, rather than being handed a url with no origin.
        ui = settings.ui_url if settings is not None else ""
        url = membership_mod.invite_link(ui, minted.token)
        return {
            "id": minted.id, "token": minted.token, "role": minted.role,
            "workspace_id": body.workspace_id, "expires_at": minted.expires_at,
            "max_uses": minted.max_uses, "mode": mode,
            "accept_path": "/api/workspace/invites/accept",
            "join_path": membership_mod.JOIN_PATH,
            "invite_url": url or None,
            "invite_url_refused": None if url else
                "VEXA_UI_URL is not set on agent-api — this deployment has not declared where its "
                "terminal is, so there is no link to give anyone",
        }
    @router.get("/api/workspace/invites/preview")
    def ws_invite_preview(request: Request, token: str):
        """READ-ONLY preview of an invite — the target workspace + terms — WITHOUT granting anything.
        Powers the pre-join CONSENT screen: the invitee sees what the workspace is (its purpose), the role
        they'd get, and who shared it BEFORE they log in / join. Capability-gated by the token (whoever
        holds the link may preview it); no membership is checked or created, no use is consumed. 404 for a
        token that matches nothing (never enumerates workspaces)."""
        info = membership_mod.preview_invite(wsr.root, token)
        if info is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        wsid = info["workspace_id"]
        # Human context for the card: the workspace's purpose + who shared it (their email when we've
        # stored it — see the members roster; else the opaque subject as a last resort).
        purpose = read_purpose(membership_mod._ws_dir(wsr.root, wsid))
        shared_by = info.get("created_by")
        for m in membership_mod.read_members(wsr.root, wsid):
            if m.get("subject") == info.get("created_by") and m.get("email"):
                shared_by = m["email"]
                break
        # THE WORKSPACE'S NAME, not its directory. The join card's whole job is one sentence a person
        # recognises — *"Dmitry invited you to OeNB as a contributor"* — and `oenb-a1b2c3` is not a
        # name anybody was told. Read-only: `by_slug` alone, never the `_ws_sync` fallback the id
        # routes use, because this route is reachable without a session and must not write.
        rec = workspace_registry.by_slug(wsid) or {}
        return {
            "workspace_id": wsid, "id": rec.get("id"), "name": rec.get("name") or wsid,
            "purpose": purpose,
            "role": info["role"], "mode": info["mode"], "expires_at": info["expires_at"],
            # Only for a bound invite: an open one has nothing to prefill and nothing to disclose.
            "restricted_to": (info.get("allowed_emails") or []) if info["mode"] == "restricted" else [],
            "shared_by": shared_by, "valid": info["valid"], "reason": info["reason"],
        }
    @router.post("/api/workspace/invites/accept")
    def ws_invite_accept(request: Request, body: InviteAcceptBody = Body(...)):
        """Redeem an invite token (any logged-in user) → membership in BOTH stores, use-count bumped.
        Idempotent per user (accepting twice = one membership, no extra use consumed). The token carries
        NO workspace id — we resolve it by scanning the shareable workspaces' invites for its hash.
        Post-auth redeem (AMENDMENT 5): the caller is an already-authenticated user (X-User-Id); a
        RESTRICTED invite additionally requires their VERIFIED email (X-User-Email, gateway-injected)
        to be in the invite's allowed_emails."""
        subject = subject_of(request)
        # SECURITY BOUNDARY: X-User-Email is trusted as the caller's VERIFIED email ONLY because the
        # gateway strips any client-sent x-user-email and re-injects the value it resolved from the
        # api-key. That invariant holds solely when the gateway is agent-api's SOLE ingress. Today the
        # terminal / host-local clients reach agent-api directly (no gateway hop), so on the direct edge
        # this header is spoofable — restricted-mode invites are NOT a security boundary until agent-api
        # is gateway-fronted (Stage 4). VEXA_REQUIRE_GATEWAY_IDENTITY (checked in subject_of) lets a
        # hardened deploy reject non-gateway callers. See the TOPOLOGY BOUNDARY note in create_app.
        subject_email = request.headers.get("x-user-email")
        h = membership_mod.hash_token(body.token)
        # Resolve which shared workspace this token belongs to by hash (never trust a client-declared id).
        target_ws = None
        root = wsr.root
        for child in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
            slug = child.name
            if slug.startswith(".") or slug in membership_mod.RESERVED_SLUGS:
                continue
            for inv in membership_mod._read_json_list(child, membership_mod.INVITES_FILE):
                if inv.get("hash") == h:
                    target_ws = slug
                    break
            if target_ws:
                break
        if target_ws is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        try:
            result = membership_mod.accept_invite(
                wsr.root, target_ws, token=body.token, subject=subject, subject_email=subject_email,
                index=mindex, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        return result
    @router.delete("/api/workspace/invites/{invite_id}")
    def ws_invite_revoke(invite_id: str, request: Request, workspace_id: str):
        """Revoke an invite (owner/contributor of the workspace)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            membership_mod.revoke_invite(wsr.root, workspace_id, invite_id, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "invite_id": invite_id}
    @router.get("/api/workspace/invites")
    def ws_invites_list(request: Request, workspace_id: str):
        """List a workspace's invites (owner/contributor). Hashes are never surfaced."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            return {"invites": membership_mod.list_invites(wsr.root, workspace_id)}
        except MembershipError as exc:
            raise _member_error(exc)
    @router.get("/api/workspace/members")
    def ws_members_list(request: Request, workspace_id: str):
        """List a workspace's members (owner/contributor). Opportunistically records the CALLER's own
        verified email onto their member row (self-healing for members granted before emails were stored)
        so the roster shows human labels, not opaque subject ids."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            try:  # best-effort label refresh — never fail the list on a backfill hiccup
                membership_mod.backfill_member_email(
                    wsr.root, workspace_id, subject,
                    request.headers.get("x-user-email"), commit_fn=_pc)
            except Exception:  # noqa: BLE001
                logger.debug("member email backfill skipped for %s in %s", subject, workspace_id, exc_info=True)
            # WHO EACH MEMBER IS, by name (Vexa-ai/vexa#1634). The roster has carried `email`
            # since memberships were stored, and an address is how the system finds a person rather
            # than what they are called — so the front page's first sentence ("you, Jane Smith and 2
            # more") needs a name beside it. Additive and nullable: `read_members` is unchanged, the
            # roster renders exactly as it did for a member nobody has written down, and no caller
            # has to know this field exists. `person_name` never answers with an address.
            rows = []
            for m in membership_mod.read_members(wsr.root, workspace_id):
                named = front_page_mod.person_name(wsr.root, str(m.get("subject") or ""),
                                                   email=m.get("email"))
                rows.append({**m, "name": named} if named else dict(m))
            return {"members": rows}
        except MembershipError as exc:
            raise _member_error(exc)
    @router.delete("/api/workspace/members/{member_subject}")
    def ws_member_remove(member_subject: str, request: Request, workspace_id: str):
        """Remove a member (owner only)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            membership_mod.remove_member(wsr.root, workspace_id, member_subject, index=mindex, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "subject": member_subject}
    @router.post("/api/workspace/members/{member_subject}/role")
    def ws_member_role(member_subject: str, request: Request, workspace_id: str,
                       body: RoleSetBody = Body(...)):
        """Flip a member's role (owner only) — read <-> read/write permissions."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            rec = membership_mod.set_role(
                wsr.root, workspace_id, member_subject, body.role,
                changed_by=subject, index=mindex, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        return rec
    # ── THE TWO VERBS (Vexa-ai/vexa#1632) ────────────────────────────────────────────────────────
    #
    # The front page has no membership form any more: its three controls queue an act on the chat,
    # the agent asks for the address and the role in one question, and then it calls one of these.
    # Both are addressed by EMAIL, which is the whole reason they are new routes rather than a body
    # change on the three above — those take a `member_subject`, the opaque platform id, which is
    # exactly right for a panel holding a roster it just read and useless to an agent whose person
    # said a name out loud.
    #
    # THE THINKING IS IN `control_plane/membership_acts.py`, not here. These are the door: identity,
    # the gate's two inputs, the injected collaborators, and the refusal translation. Everything that
    # can be wrong about an act — who may run it, what an address resolves to, whether the link is
    # mailed or handed over — is decided there, where a test drives it with a directory and three
    # callables instead of a running app.

    def _act_commit(request: Request, subject: str):
        """The `policy/` writer for an act, with THE PERSON WHO ASKED as the commit's author.

        `_pc` (the platform writer) is what every membership route above uses and what this one
        cannot: the issue asks for the act to be "recorded as a commit in the workspace with the
        inviter as author", and until now every membership change in every workspace's history read
        `vexa-platform` — so *who added this person* was answerable only by reading a JSON diff. The
        committer stays the platform, because the platform is what physically holds the write."""
        return membership_mod.policy_commit_as(
            subject, request.headers.get("x-user-email") or "")

    def _act_refusal(exc: "membership_acts.ActRefused"):
        # ONE SENTENCE, THE ACT'S OWN. `_member_error` does the same job for `MembershipError` and
        # `ActRefused` carries the identical `.status`, so this is that translation and not a second
        # policy: the rig prints `detail` to the agent, which says it to the person.
        return HTTPException(status_code=exc.status, detail=str(exc))

    @router.post("/api/workspace/invite")
    def ws_invite_person(request: Request, body: WorkspaceInviteBody = Body(...)):
        """Invite ONE address to a workspace as one of `owner · contributor · reader` — the verb
        behind `workspace_invite`.

        Owner-only, and the check is the same `require_role(..., "owner")` the role and remove routes
        run. `_system` is refused for everybody; `_global` is admin-only and then refused, because the
        company layer's editors are a named set in `POLICIES.md` and a membership record there would
        authorise nothing. The invite is the one `POST /api/workspace/invites` mints — same store,
        same hash-only persistence — minted `restricted` to the named address, so a forwarded link
        grants nobody anything.

        Where the link GOES is the question this route exists to answer: an address this instance
        already knows gets it handed back for the agent to give them in the chat they are in, and
        every other address is published to the mail carrier. The answer says which happened."""
        subject = subject_of(request)
        try:
            membership_acts.assert_may_manage(
                wsr.root, body.slug, subject,
                is_admin=bool(global_layer.is_admin(settings, str(subject))))
            rec = workspace_registry.by_slug(body.slug) or {}
            return membership_acts.invite(
                wsr.root, body.slug, email=body.email, role=body.role, inviter=subject,
                inviter_email=request.headers.get("x-user-email") or "",
                workspace_name=str(rec.get("name") or ""),
                index=mindex, ui_url=(settings.ui_url if settings is not None else ""),
                commit_fn=_act_commit(request, subject),
                resolve_subject=_email_subject_lookup,
                mail=publish_mod.publish_invite)
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)

    @router.post("/api/workspace/membership")
    def ws_membership_set(request: Request, body: WorkspaceMembershipBody = Body(...)):
        """Change what an address IS in a workspace, or take them off it — the verb behind
        `workspace_membership`. `role` is one of the three, or `remove`.

        Same gate as the invite above, and one verb rather than two because it is one question with
        four answers: an agent that had to choose a verb before asking would have to guess the answer
        first. The last-owner refusal reaches the person as itself (409) — it is about the workspace,
        not about them, and a generic failure would leave somebody trying it again."""
        subject = subject_of(request)
        try:
            membership_acts.assert_may_manage(
                wsr.root, body.slug, subject,
                is_admin=bool(global_layer.is_admin(settings, str(subject))))
            return membership_acts.set_membership(
                wsr.root, body.slug, email=body.email, role=body.role, actor=subject,
                index=mindex, commit_fn=_act_commit(request, subject),
                resolve_subject=_email_subject_lookup)
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)

    @router.post("/api/workspace/{workspace_id}/leave")
    def ws_member_leave(workspace_id: str, request: Request):
        """LEAVE a shared workspace — the caller removes THEMSELVES (any role; no owner gate). The
        last-owner guard still applies: a sole creator must unshare or hand off ownership rather than
        orphan the workspace, so their leave is refused (409) with that message."""
        subject = subject_of(request)
        if membership_mod.is_member(wsr.root, workspace_id, subject) is None:
            raise HTTPException(status_code=404, detail="not a member of this workspace")
        try:
            membership_mod.remove_member(wsr.root, workspace_id, subject, index=mindex, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "left": workspace_id}
    @router.get("/api/workspace/shared")
    def ws_shared_list(request: Request):
        """The "workspaces shared with me" listing, reconciled across BOTH membership stores.

        ``users.data.memberships[]`` (the index) is the fast, cross-host read; ``policy/members.json``
        is the authoritative one (Q6). Reading ONLY the mirror made every grant on this host invisible
        whenever the internal edge to admin-api was unreachable — the route answered 200 with an empty
        list, so a 403 on that hop rendered in the UI as "you have no shared workspaces" rather than as
        an error. It is now a UNION, never a subtraction: an index row with no local dir is a workspace
        that lives on another host and must still be listed, so the git store only ever ADDS rows the
        index is missing. ``index_degraded`` says out loud when the mirror could not be read."""
        subject = subject_of(request)
        degraded = False
        try:
            rows = list(mindex.list(subject) or [])
        except Exception as exc:  # noqa: BLE001 — the authoritative store still answers; never 500 this
            logger.warning("membership index list failed for subject=%s: %s — serving policy/members.json",
                           subject, exc)
            rows, degraded = [], True
        seen = {r.get("workspace_id") for r in rows}
        for row in membership_mod.list_memberships(wsr.root, subject):
            if row["workspace_id"] not in seen:
                rows.append(row)
        return {"memberships": rows, "index_degraded": degraded}

    return router
