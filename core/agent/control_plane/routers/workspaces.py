"""routers/workspaces.py — Everything a workspace is: read and write its files, its git state, its identity, the
mount set, attach and swap, and the credentials that make a remote reachable. Sharing, membership
and invites are `routers/sharing.py`.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane.workspace_reader import home_of

from shared import sensitive as sensitive_mod
from control_plane import claims as claims_mod
from control_plane import page_grammar as page_grammar_mod
from control_plane import deploy_keys as deploy_keys_mod
from control_plane.routers import folder_import as folder_import_routes
from control_plane import front_page as front_page_mod
from control_plane import git_credentials as git_creds
from control_plane import global_layer
from control_plane import kg_templates
from control_plane import meeting_note as meeting_note_mod
from control_plane import page_merge
from control_plane import publish as publish_mod
from control_plane import repo_ref
from control_plane import scaffolds as scaffolds_mod
from control_plane import meeting_listen as meeting_listen_mod
from control_plane import signal_scope
from control_plane import system_mounts
from control_plane import workspace_import as imports
from control_plane import workspace_credentials as wcreds
from control_plane import workspace_ids as ids_mod
from control_plane import workspace_membership as membership_mod
from control_plane.api_shared import MAX_UPLOAD_BYTES, _upload_filename, logger
from control_plane.bodies import (
    ArchiveBody, AssetFetchBody, ClaimVerdictsBody, ClaimsProposeBody, EntityUpsertBody,
    GitTokenBody, SharedActiveBody, SharedAttachBody, WorkspaceActivateBody,
    WorkspaceDeactivateBody, WorkspaceMoveBody, WorkspaceNewBody, WorkspacePublishBody,
    WorkspaceImportBody, WorkspacePullBody, WorkspacePurposeBody, WorkspacePushBody,
    WorkspaceRemoveBody, WorkspaceRenameBody, WorkspaceStatusBody, WorkspaceRestoreBody, WorkspaceSwapBody,
    WorkspaceWriteBody, MeetingListenBody)
from control_plane import ceiling as ceiling_mod
from control_plane import identity_token, private_taint
from control_plane import connection_policy
from control_plane.ceiling import is_unwatched, require_in_ceiling, write_slug
from control_plane.workspace_attach import (
    CloneError, activate_workspace, active_workspaces, attach_shared_workspace, bind_repository_credential,
    attached_workspaces, create_workspace, deactivate_workspace, delete_shared_workspace,
    delete_workspace, PERSONAL_REFUSAL, rename_workspace, set_archived, set_shared_active,
    shared_active_mounts, shared_attached_state, swap_workspace, workspace_slot_dir)
from control_plane.workspace_git_sync import (
    RemoteSyncError, SyncRefused, detach_home, fetch_status, pull_origin, push_origin, remote_status)
from control_plane.workspace_membership import POLICY_UNREADABLE, MembershipError
from control_plane.workspace_publish import (
    PublishError, RepoExistsError, TokenRejectedError, UnsupportedHostError, publish_workspace,
    published_remote_url, resolve_provider)
from shared import git_provider
from control_plane.workspace_purpose import read_purpose, write_purpose
from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from pathlib import Path
from workspaces.shared import entities as entities_mod
from workspaces.shared import links as links_mod
from workspaces.shared import workspace_paths as wpaths
from shared import asset_source as assets_mod
from shared import friction as friction_mod
from shared import page_images
from shared.git_redaction import redact as redact_secrets
from shared.gitexec import run_git
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from typing import Annotated, Optional
from pydantic import BaseModel, Field
import hashlib
from collections import OrderedDict
import time
import json
import stat as stat_mod
import os
import re


#: WHO WROTE A PAGE (H10). A page-route commit made for a person by their agent (a delegated caller)
#: closes its message with git trailers that say so: ``On-behalf-of: <person>`` and
#: ``Written-by: agent`` (plus ``Agent-unit: <unit>`` when the identity names one). The person's own
#: write carries none. The audit reads them with ``git log --format=%(trailers)``; nothing a caller
#: sends reaches the trailer block, which is written here from the verified identity alone.
ON_BEHALF_OF = "On-behalf-of"
WRITTEN_BY = "Written-by"
AGENT_UNIT = "Agent-unit"
_TRAILER_VALUE = re.compile(r"[^A-Za-z0-9@._+:-]")


def writer_trailers(headers) -> list:
    """The trailer lines a commit made for this caller carries: none for a person's own write."""
    if not identity_token.is_delegated(headers):
        return []
    subject = _TRAILER_VALUE.sub("", str(headers.get("x-user-id") or ""))[:128] or "unknown"
    out = [f"{ON_BEHALF_OF}: {subject}", f"{WRITTEN_BY}: agent"]
    unit = _TRAILER_VALUE.sub("", identity_token.session_of(headers))[:160]
    if unit:
        out.append(f"{AGENT_UNIT}: {unit}")
    return out


def attributed(message: str, headers) -> str:
    """``message`` with the writer's trailers as its last paragraph (unchanged for a person)."""
    lines = writer_trailers(headers)
    return f"{message}\n\n" + "\n".join(lines) if lines else message


#: HOW FAR BACK THE FRONT PAGE LOOKS for a change a person can read (Vexa-ai/vexa#1634). Deep enough
#: to walk past a run of plumbing commits — an identity write, a roster write, a policy commit — and
#: shallow enough that the answer is still "recently". A workspace whose last twenty commits are all
#: plumbing has genuinely had no page changed lately, and the line says so by naming no page.
LAST_CHANGE_SCAN = 20


class EntitySummaryBody(BaseModel):
    """`POST /api/entities/summary`: the page whose hover story is asked for."""
    path: str = Field(..., min_length=1, max_length=500)
    slug: Optional[str] = Field(default=None, max_length=120)
#: How far back `/api/people/admin` reads `_global` for a commit a PERSON made. Deeper than the
#: last-change scan because a run of platform commits (a policy seed, a re-run of setup) can sit on
#: top of the acceptance that names the administrator, and answering `null` because plumbing was
#: busy would put the word "the admin" back on the line this exists to fix.
ADMIN_SCAN = 60


def page_head(base, rel: str) -> Optional[str]:
    """The top of one page in a workspace, RAW — the bytes a title is read out of.

    Not ``wsr.read_at``: that prepends the template and unfilled banners, which is right for a
    page an agent is about to read and wrong for this, because a banner in front of the file
    pushes its own front matter out of the leading ``---`` position and takes the ``title:``
    with it. Guarded by the same ``resolve_inside`` every path from a caller goes through."""
    try:
        pbase, prel = wpaths.locate_inside(base, rel)
    except (wpaths.PathRefused, OSError, ValueError):
        return None
    return wpaths.read_head_inside(pbase, prel, front_page_mod._HEAD_BYTES) if prel else None


def last_change(wsr, target: Path, *, slug: str, path: Optional[str], viewer,
                global_dir: Path) -> tuple[Optional[str], Optional[dict]]:
    """THE NEWEST COMMIT THAT CHANGED A PAGE in an AUTHORIZED workspace directory, described
    (``front_page.describe_commit``) — ``(path, change)``, ``change`` None when nothing was committed.

    The one place the last-change sentence is computed: `GET /api/workspaces/{slug}/git/last-change`
    and the desktop's workspace and file tiles (`routers/desktop.py`) both call it. ``target`` must
    already be authorized by ``_read_target`` — this decides nothing about who may read what.
    ``ValueError`` for a path or subject git refuses."""
    history = wsr.git_history_at(target, path=path, limit=LAST_CHANGE_SCAN, viewer=viewer)
    commits = history.get("commits") or []
    if not commits:
        # NOTHING HAS BEEN COMMITTED HERE, which is an ordinary state of a new workspace and not
        # a failure. The panel says so in words; a 404 would make it render an error.
        return history.get("path"), None
    newest = next((c for c in commits
                   if any(front_page_mod.is_page(p, slug) for p in (c.get("files") or []))),
                  commits[0])
    return history.get("path"), front_page_mod.describe_commit(
        newest, slug=slug,
        read_page=lambda rel: page_head(target, rel),
        # BOTH HALVES OF THE GIT AUTHOR (Vexa-ai/vexa#1642). `%an` is the person's
        # sign-in address and `%ae` is `<subject>@vexa.local`, so the desk is opened
        # through the second and the name is read off the first. `display_name` — not
        # `person_name` — because the sentence must never say *someone*: where nobody
        # has written this person down, their own address read as a name is the floor.
        name_of=lambda author, email: front_page_mod.display_name(
            wsr.root, address=author, principal=email, global_dir=global_dir))


def build(**d) -> APIRouter:
    """The workspaces routes, bound to one app's dependencies."""
    router = APIRouter()
    _clone_fn = d['_clone_fn']
    _credential_refusal = d['_credential_refusal']
    _entity_mounts = d['_entity_mounts']
    _internal_caller = d['_internal_caller']
    _manage_dir = d['_manage_dir']
    _member_error = d['_member_error']
    _read_target = d['_read_target']
    _repo = d['_repo']
    _require_shared_write = d['_require_shared_write']
    _workspace_key = d['_workspace_key']
    _credential_note = d['_credential_note']
    _ws_is_member = d['_ws_is_member']
    _ws_sync = d['_ws_sync']
    _ws_lookup = d['_ws_lookup']
    mindex = d['mindex']
    settings = d['settings']
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    # TRANSCRIPT SIGNALS: the one page a signal's turn may write (`signal_scope`), the meeting a
    # brief belongs to, and the chat a meeting wakes. `.get` so a harness that builds this router
    # alone, without them, still serves every other route.
    _signal_grants = d.get('_signal_grants') or signal_scope.reader(None)
    _meeting_owner_lookup = d.get('_meeting_owner_lookup')
    _global_root = d.get('_global_root')
    sess = d.get('sess')

    def _link_mounts(mounts: list, target) -> list:
        """The mounts a page written into ``target`` may link into by id: only those every reader
        of ``target`` can open (`workspace_ids.readers_covered`). The rest leave `[[Name]]` local,
        and the reader's resolver decides at read time (friction 2026-10-14)."""
        try:
            here = workspace_registry.by_slug(Path(str(target)).name)
        except Exception:  # noqa: BLE001
            here = None
        members = lambda slug: [m.get("subject") for m in membership_mod.read_members(wsr.root, slug)  # noqa: E731
                                if m.get("subject")]
        out = []
        for m in mounts or []:
            # a person's own desk is mounted under an alias (its active-set slug), so fall back to
            # the directory it lives in, which is what the registry keys it by
            dest = (workspace_registry.by_slug(str(m.get("slug") or ""))
                    or workspace_registry.by_slug(Path(str(m.get("path") or "")).name))
            if ids_mod.readers_covered(here, dest, root=wsr.root, is_member=_ws_is_member, members=members):
                out.append(m)
        return out
    wsr = d['wsr']

    def _writable_global() -> Optional[Path]:
        """The `_global` the worker mount serves (`system_mounts.global_root`), when this process may
        write it — else None. One answer for the editor, the page writer and reset, so an admin's
        edit lands in the bytes every agent reads."""
        target = system_mounts.global_root(settings, wsr.root)
        return target if target.is_dir() and os.access(target, os.W_OK) else None

    def _global_dir() -> Path:
        """Where `_global` is, for the readers in this router (the company directory's person pages)."""
        return system_mounts.global_root(settings, wsr.root)

    def _policy_change(request: Request, subject, target: Path, rel: str, after: Optional[str]) -> str:
        """A write that changes a connection key in `_global/POLICIES.md` (how an agent may use a
        person's mailbox and calendar): refused to a worker without a person in the loop; otherwise
        logged once and answered with the commit-message notice (`connection_policy.change_notice`),
        "" when no connection key changes. ``after`` is the file's new text, None when it goes away."""
        if str(rel).strip().lstrip("./") != connection_policy.POLICIES_FILE:
            return ""
        try:
            if Path(target).resolve() != _global_dir().resolve():
                return ""
        except OSError:
            return ""
        keys = connection_policy.changed_keys(
            wpaths.read_text_inside(target, connection_policy.POLICIES_FILE, max_bytes=1_000_000), after)
        if not keys:
            return ""
        if is_unwatched(request):
            raise ceiling_mod.refused(request, 403, ceiling_mod.refusal(
                "connection_policy_needs_person",
                f"{', '.join(keys)} decide how credentials are used; only a person in the loop changes "
                "them. Nothing was written. Record the change you wanted and stop."),
                reason="connection_policy_needs_person")
        delegated = ceiling_mod.is_delegated(request)
        logger.warning(json.dumps({"event": "connection_policy_changed", "subject": str(subject),
                                   "keys": keys, "delegated": delegated}))
        request.state.connection_policy_changed = keys
        return connection_policy.change_notice(keys, str(subject), delegated=delegated)

    @router.get("/api/workspace/tree")
    def ws_tree(request: Request, hidden: bool = False, slug: Optional[str] = None):
        try:
            return {"files": wsr.tree_at(_read_target(request, slug), hidden=hidden)}
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
    @router.post("/api/workspace/upload")
    async def ws_upload(request: Request, files: list[UploadFile] = File(...),
                        session: str = Query(default="", max_length=128)):
        if not files:
            raise HTTPException(status_code=400, detail="no files uploaded")
        subject = subject_of(request)
        # WHERE THIS CHAT'S FILES GO (Vexa-ai/vexa#1783): its target workspace, `_system` for an
        # incognito chat (no workspace; it goes with the chat), the desk only when the chat writes
        # to Personal. No chat named: the desk, as before.
        from control_plane.routers.workspace_states import attachment_home  # noqa: PLC0415
        chat = session if session and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session) else ""
        home = attachment_home(d.get("sess"), wsr.root, str(subject), chat) if chat else ""
        try:
            if home == system_mounts.SYSTEM_SLUG:
                ws = system_mounts.ensure_system_workspace(wsr.root, str(subject))
            elif home:
                require_in_ceiling(request, home)
                ws = _read_target(request, home, write=True)
            else:
                ws = home_of(wsr, subject)    # the person's HOME, never a switched-off Personal
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
            try:
                wpaths.relative_parts(f"{folder}/{stored_name}")
            except wpaths.PathRefused:
                raise HTTPException(status_code=400, detail="invalid filename") from None
            pending.append((content, stored_name, f"{folder}/{stored_name}",
                            (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]))
        for content, _n, path, _o in pending:
            _hold_private(request, subject, home or None, path, ws, content=content, surface="asset")
        uploaded: list[dict] = []
        for content, stored_name, path, original in pending:
            # NOFOLLOW: a link planted at `uploads/`/`assets/` refuses, one at the name is replaced
            try:
                wpaths.write_bytes_inside(ws, path, content)
            except wpaths.PathRefused:
                raise HTTPException(status_code=400, detail="invalid filename") from None
            # `original` is the name the person knows, for the chip; `name` stays the stored one.
            if chat and d.get("sess") is not None:
                d["sess"].record_attachment(str(subject), chat, path, home)
            uploaded.append({"name": stored_name, "path": path, "size": len(content), "ws": home or str(subject),
                             "type": assets_mod.media_type_for(path),
                             "original": re.sub(r"[\x00-\x1f\x7f]", "", original).strip()[:200]
                                         or stored_name})
        return {"files": uploaded}
    def _write_dir(request: Request, subject, rel: str, slug: Optional[str], *,
                   templates: bool = False) -> Path:
        """WHICH workspace dir a write to ``rel`` lands in, or the refusal. The mount rules, in one
        place: own baseline/_system always; a shared workspace needs contributor+; `_global` only
        the admin account; `kg/templates/` never — except the COMPANY's templates in `_global`, on
        the two doors that pass ``templates=True`` (the page write, which validates the shape, and
        the page remove, which reverts a kind to the baked shape), and only for the admin account.

        Extracted from ``ws_file_write`` unchanged when the ASSET writes arrived, because the
        alternative was a second copy of an authorization rule — and a permission check that exists
        twice is a permission check that will disagree with itself. One caller was already enough
        to make it worth a name; three is not a choice."""
        try:
            wpaths.relative_parts(rel)   # absolute · `..` · `.git`/`.vexa`/`policy` — one rule, every route
        except wpaths.PathRefused as exc:
            # `policy/` IS THE PLATFORM'S (D5, security review C3): the roster, visibility and
            # connections. A refusal there is about authority, not about the path's shape — 403.
            if exc.kind == "reserved" and wpaths.is_platform_policy_path(rel):
                raise HTTPException(status_code=403, detail=(
                    "policy/ is written only by the platform — change members with "
                    "workspace_membership or the members panel")) from None
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # `kg/templates/` is the SHAPE of an entity, not one. It is hidden from every tree, so a tab
        # can only be open on it deliberately — and a save from that tab would silently rewrite the
        # shape every future entity is copied from.
        #
        # THE COMPANY'S TEMPLATES ARE THE ONE EXCEPTION (founder ruling 2026-10-11: the admin account
        # controls the templates new notes are created from). `_global/kg/templates/<kind>.md` is
        # the shape `entity_upsert` creates a new card from; it is written only through the doors
        # that ask for it, and the `_global` branch below still refuses every account but the
        # admin account.
        if rel.startswith("kg/templates/") and not (templates and slug == system_mounts.GLOBAL_SLUG):
            raise HTTPException(status_code=403, detail=(
                "kg/templates/ holds entity shapes, not records — the company's templates are "
                "_global/kg/templates/, edited by the admin account"
                if slug == system_mounts.GLOBAL_SLUG else
                "kg/templates/ holds entity shapes, not records"))
        if slug == system_mounts.GLOBAL_SLUG:
            require_in_ceiling(request, slug)
            if not global_layer.is_admin_account(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only the admin account may edit _global")
            target = _writable_global()
            if target is None:
                raise HTTPException(status_code=404, detail="the organisation tier is not writable here")
            return target
        if slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError:
                pass  # not shared — fall through; _read_target 403s anything outside the active set
        return _read_target(request, slug, write=True)

    # A DROPPED FOLDER (founder 2026-10-13): its four doors live in `routers/folder_import.py`, mounted
    # here so an import INTO an existing workspace is authorized by `_write_dir` above, not a copy of it.
    folder_import_routes.mount(router, subject_of=subject_of, wsr=wsr, settings=settings, write_dir=_write_dir)

    def _refuse_sensitive(values: list[str]) -> None:
        # GIT HOLDS PLACEHOLDERS ONLY (PRD 0.13.4 D17, deny test 23): a page rendered for a viewer
        # (`page_render.to_markdown` stamps it) is never committed back as the page.
        if any(page_grammar_mod.contains_resolved(str(v)) for v in values if v):
            logger.warning(json.dumps({"event": "resolved_render_write_refused"}))
            raise HTTPException(status_code=422, detail={
                "refused": "resolved_placeholders",
                "said": ("This text is a page as one person saw it, with its placeholders already filled in. "
                         "Pages keep the placeholders ({{…}} and [[kind:id]]); edit the page's source instead.")})
        kinds = sensitive_mod.find_all(str(v) for v in values if v)
        if kinds:
            logger.warning(json.dumps({"event": "sensitive_write_refused", "kinds": kinds}))
            raise HTTPException(status_code=422, detail=sensitive_mod.refusal(kinds))

    def _screen_images(request: Request, values: list[str], *, path: str = "",
                       tool: str = "") -> list[str]:
        """VERIFY EVERY EXTERNAL IMAGE ADDRESS BEFORE IT REACHES A PAGE (Vexa-ai/vexa#1624).

        The Example Bank README carried `![Example Bank logo](https://upload.wikimedia.org/…/Example_Bank_Logo.svg)`, an
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

    def _commit(target: Path, paths: list[str], message: str, request: Optional[Request] = None) -> "str | None":
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
        if not (target / ".git").is_dir():
            return None

        def _git(*args: str):
            return run_git(target, *args)

        staged = [p for p in paths if p and _git("add", "--", p).returncode == 0]
        if staged:
            if request is not None:
                message = attributed(message, request.headers)
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
        _hold_private(request, subject, slug, rel, target, content=content, surface="asset")
        if len(content) > assets_mod.MAX_ASSET_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"{rel} exceeds {assets_mod.MAX_ASSET_BYTES // (1024 * 1024)}MB")
        try:
            # resolved ONCE, then written nofollow through what was resolved (`locate_inside`)
            fbase, frel = wpaths.locate_inside(target, rel)
            ibase, irel = wpaths.locate_inside(target, assets_mod.SOURCES_INDEX)
            wpaths.write_bytes_inside(fbase, frel, content)
            existing = wpaths.read_text_inside(ibase, irel) or ""
            wpaths.write_text_inside(ibase, irel, assets_mod.record_source(existing, rel, source))
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _commit(target, [rel, assets_mod.SOURCES_INDEX], f"asset {rel} ({source or 'uploaded'})", request)
        return {"path": rel, "bytes": len(content), "source": source,
                "content_type": assets_mod.media_type_for(rel)}

    @router.get("/api/workspace/asset")
    def ws_asset(request: Request, path: str, slug: Optional[str] = None,
                 if_none_match: Optional[str] = Header(default=None, alias="If-None-Match")):
        """SERVE one workspace file AS ITSELF — the route `![logo](assets/bank-logo.svg)` renders
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
            abase, arel = wpaths.locate_inside(base, path)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # read ONCE, nofollow, through what was resolved — the bytes served are the bytes checked
        data = wpaths.read_bytes_inside(abase, arel) if arel else None
        stat = wpaths.stat_inside(abase, arel) if data is not None else None
        if data is None or stat is None:
            raise HTTPException(status_code=404, detail="not found")
        f = abase / arel
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
        return Response(content=data, media_type=assets_mod.media_type_for(path), headers=headers)

    @router.post("/api/workspace/asset")
    def ws_asset_fetch(request: Request, body: AssetFetchBody = Body(...)):
        """FETCH a remote image INTO the workspace — the act behind the external-image placeholder,
        and the verb behind `fetch_asset`. Then reference the stored file on the page relatively,
        `![alt](assets/<name>)`; never put the remote URL on a page.

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
        url = body.url.strip()
        slug = write_slug(request, body.slug)
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
        rel = assets_mod.asset_path_for(url, ctype, body.path)
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
    def _hold_private(request: Request, subject, slug, rel: str, target: Path, *,
                      content=None, surface: str = "page", before: str = "",
                      text: Optional[str] = None) -> None:
        """A WRITE FROM A SESSION THAT READ THE PERSON'S PRIVATE DATA into a workspace other people
        read — a page, an upload or asset, a filename, the workspace's name or purpose — waits for
        the person (`private_taint`, D6): 409 with a typed fault and a confirmation code; what it
        would have written is kept encrypted for the person's share card, never on a mount. When
        the session's reads were fingerprinted, only a write whose added text (``content`` or
        ``text`` over ``before``) carries something from them is held (frictions 1+2)."""
        if not ceiling_mod.is_delegated(request):
            return
        ws = str(slug or Path(target).name)
        try:
            ws_name = str((workspace_registry.by_slug(ws) or {}).get("name") or "")
        except Exception:  # noqa: BLE001 — a name is a courtesy
            ws_name = ""
        if isinstance(content, (bytes, bytearray)):
            import base64 as _b64  # noqa: PLC0415
            content = {"base64": _b64.b64encode(bytes(content)).decode()}
        unit = identity_token.session_of(request.headers)
        if str(unit or "").strip() and not private_taint.sources(unit):
            return
        try:
            readers = private_taint.other_readers(wsr.root, Path(target), str(subject))
        except MembershipError as exc:
            raise HTTPException(status_code=503, detail={
                "refused": POLICY_UNREADABLE,
                "message": "Who reads this workspace could not be checked, so nothing was written."}) from exc
        held = private_taint.check_egress(
            subject=str(subject), unit=unit, surface=surface,
            target=f"{ws}/{rel}", readers=readers,
            workspace=ws, name=ws_name, path=rel, content=content, delegated=True,
            before=before, text=text)
        if held is not None:
            raise HTTPException(status_code=409, detail=held)

    #: A page's generated story, kept for this process: keyed by WHO asked, the resolved workspace
    #: directory, the path and the page's own content, so it is only ever answered for the same page
    #: as the same reader saw it — never across desks (every desk's slug is "") or readers. Bounded.
    _summary_cache: "OrderedDict[tuple, str]" = OrderedDict()
    _SUMMARY_CACHE_MAX = 512
    _summary_last: dict = {}

    @router.post("/api/entities/summary")
    def entity_summary(request: Request, body: EntitySummaryBody = Body(...)):
        """THE SHORT STORY ON AN ENTITY'S HOVER CARD (founder, live: "this should tell the full story,
        short one"): the page's frontmatter `summary` — who they are, how they relate to the reader,
        the latest open thread, ≤280 characters. The agent keeps it current when it updates the page;
        a page that has none gets one here ONCE, by the cheap model within the spend cap, written into
        the page when the reader may write it (else kept for this process), never per hover."""
        subject = subject_of(request)
        rel = body.path.strip()
        try:
            place = _read_target(request, body.slug)
            content = wsr.read_at(place, rel)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        from control_plane import entity_summary as es  # noqa: PLC0415
        have = es.read_summary(content)
        if have:
            return {"summary": have, "generated": False}
        key = (str(subject), str(place), rel, hashlib.sha256(content.encode("utf-8", "replace")).hexdigest())
        if key in _summary_cache:
            _summary_cache.move_to_end(key)
            return {"summary": _summary_cache[key], "generated": True}
        now = time.time()
        if now - _summary_last.get(subject, 0.0) < es.MIN_GAP_S:
            return {"summary": "", "generated": False}
        _summary_last[subject] = now
        story = es.generate(content, complete=_summary_model(subject))
        if not story:
            return {"summary": "", "generated": False}
        _summary_cache[key] = story
        while len(_summary_cache) > _SUMMARY_CACHE_MAX:
            _summary_cache.popitem(last=False)
        try:
            ws_file_write(request, WorkspaceWriteBody(path=rel, slug=body.slug, content=es.with_summary(content, story)))
        except HTTPException:
            pass                                  # a reader who may not write keeps it for this process
        except Exception:  # noqa: BLE001
            logger.warning("entity summary not stored for %s", rel)
        return {"summary": story, "generated": True}

    def _summary_model(subject: str):
        from control_plane import budgets, home_model  # noqa: PLC0415
        from shared import usage as usage_mod  # noqa: PLC0415
        catalog = getattr(d.get("dispatcher"), "catalog", None)
        if catalog is None or getattr(catalog, "empty", True):
            return None

        def complete(system: str, user: str):
            pol = budgets.read(d["_global_root"]() if d.get("_global_root") else None)
            ucli = budgets.usage_client(d.get("redis_url"))
            if budgets.verdict(pol, usage_mod.rolling(ucli, subject), background=True).get("stop"):
                return None
            out = home_model.complete_json(catalog, system, user, named=pol.get("model_home", "cheapest"),
                                           allowed=budgets.allowed_list(pol))
            if out and out.get("_tokens"):
                from control_plane import llm_budget  # noqa: PLC0415
                llm_budget.record_local(subject, "summary", out.get("_usage") or {"prompt_tokens": int(out["_tokens"])},
                                        fallback_client=ucli)
            return out
        return complete

    # ── WHAT THE ROOM'S AGENT CHANGES DURING A CALL (R1 1.5) ──────────────────────────────────────
    # A page write by a room's workspace agent (its unit `agent-ws-<w>-room-<session>`, from the
    # signed delegation) while that room's call runs is recorded against the call: the page before
    # (the undo), a digest of the page after, and who the turn answered. Off with `rooms`.

    def _room_call_of(request: Request):
        from control_plane import room_echo, room_principal, rooms  # noqa: PLC0415
        store = d.get("session_store")
        unit = (request.headers.get(identity_token.DELEGATION_HEADERS["unit"]) or "").strip()
        if (store is None or not room_principal.is_room_unit(unit)
                or not rooms.enabled(_global_root, (request.headers.get("x-user-id") or "").strip())):
            return None
        sid = room_echo.session_of(unit)
        call = store.active_call(sid) if sid else None
        return (store, sid, unit, call) if call else None

    def _call_change_base(request: Request, target, rel: str):
        if _room_call_of(request) is None:
            return False                       # not a call's write: nothing to remember
        try:
            return wpaths.read_text_inside(target, rel)
        except (OSError, ValueError):
            return None

    def _record_call_change(request: Request, slug, target, rel: str, before, after: str) -> None:
        if before is False:
            return
        found = _room_call_of(request)
        if found is None:
            return
        store, sid, unit, call = found
        from control_plane import rooms as _rooms_sp  # noqa: PLC0415
        try:
            store.record_call_change(
                session_id=sid, room=call["room"], meeting_id=call.get("meeting_id"), unit=unit,
                on_behalf_of=_rooms_sp.speaker_of(_rooms_sp.redis_for(d.get("redis_url")), sid),
                workspace=str(slug or Path(target).name), path=rel, base=before,
                after_digest=hashlib.sha256((after or "").encode("utf-8")).hexdigest())
        except Exception as exc:  # noqa: BLE001 — the write landed; its record is the extra
            logger.warning("call change not recorded for %s (%s)", rel, type(exc).__name__)

    @router.put("/api/workspace/file")
    def ws_file_write(request: Request, body: WorkspaceWriteBody = Body(...)):
        """WRITE one page, creating it or replacing it whole — the terminal's in-place page editor and
        the verb behind `workspace_write`. Authorization mirrors the MOUNT rules, not the read rules:
        own personal workspace and `_system` always; a shared workspace needs contributor+; `_global` only the
        admin allowlist. Commits, so the history stays honest.

        Never overwrite a page with a note saying it moved or went away: `workspace_move` and
        `workspace_delete` do those, and keep the history."""
        import shutil as _sh  # noqa: F401 — parity with ws_reset's import style
        subject = subject_of(request)
        rel = body.path.strip()
        slug = write_slug(request, body.slug)
        # ONE NOTE PER MEETING (#1783): a write of a meeting's note that names no workspace lands on
        # the desk that holds it, not in the chat's target, where it would be a copy nobody sees.
        if slug is not None and not (body.slug or "").strip() \
                and meeting_note_mod.desk_holds_note(wsr.root, subject, rel):
            slug = None
        # A MEETING SIGNAL'S TURN WRITES ITS MEETING'S PAGE AND NOTHING ELSE (`signal_scope`).
        signal_scope.require_page(request, _signal_grants, slug=slug, path=rel, subject=str(subject))
        content = body.content
        # NO IDENTIFIERS IN A PAGE (Vexa-ai/vexa#1783): a government id, a card or account number or
        # a credential is refused here, by kind and count, never echoed (`sensitive.py`).
        _refuse_sensitive([content])
        # #1624: an image address nobody checked never reaches the page (see `_screen_images`).
        content = _screen_images(request, [content], path=rel, tool="workspace_write")[0]
        # A ROUTINE IS A FLOW (ADR-0046): a page shaped like the retired routine file is refused with
        # the tool that makes one, never written to sit there doing nothing.
        from control_plane import routine_flows as _rf  # noqa: PLC0415
        if _rf.looks_like_routine_file(rel, content):
            raise HTTPException(status_code=422, detail=dict(_rf.ROUTINE_FILE_REFUSAL))
        target = _write_dir(request, subject, rel, slug, templates=True)
        # A TEMPLATE IS CHECKED BEFORE IT LANDS: a shape `entity_upsert` cannot create a card from
        # is refused here with what to put back, rather than discovered on the next new note.
        if kg_templates.is_template_path(rel):
            problems = kg_templates.validate(rel, content)
            if problems:
                raise HTTPException(status_code=422, detail={
                    "refused": "not a usable template", "path": rel, "problems": problems})
        try:
            _before_text = wpaths.read_text_inside(target, rel) or ""
        except (wpaths.PathRefused, OSError, ValueError):
            _before_text = ""
        _hold_private(request, subject, slug, rel, target, content=content, before=_before_text)
        notice = _policy_change(request, subject, target, rel, content)
        # TWO WRITERS ON ONE PAGE (#1783): read-merge-write-commit under one lock, and a write that
        # started from an older version is merged with what landed since (`page_merge`).
        with page_merge.write_lock(target):
            _before = _call_change_base(request, target, rel)
            try:
                content, merged = page_merge.reconcile(target, rel, content)
            except page_merge.PageConflict as exc:
                raise HTTPException(status_code=409, detail={
                    "refused": "page changed while you were editing it", "path": rel,
                    "current": exc.current,
                    "said": (f"{rel} was changed by someone else while you were editing it, on the same "
                             "lines. Nothing was written: read it again and apply your change to what is "
                             "there now.")}) from None
            try:
                # resolved ONCE (…and again WITH the root, for the symlink half), written nofollow
                fbase, frel = wpaths.locate_inside(target, rel)
                f = wpaths.write_text_inside(fbase, frel, content)
            except wpaths.PathRefused as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            if (target / ".git").is_dir():
                run_git(target, "add", "--", rel)
                run_git(target, "-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                        "commit", "-m", attributed(f"edit {rel} (terminal page editor)"
                                                   + (f"\n\n{notice}" if notice else ""), request.headers))
        _record_call_change(request, slug, target, rel, _before, content)
        # THE FILE'S REFERENCE, qualified (founder ruling 2026-10-18): the agent names what it wrote
        # with this, never a bare path — `README.md` is in every workspace.
        out = {"path": rel, "written": True, "ref": links_mod.page_ref(slug or str(subject), rel),
               **({"connection_policy_changed": request.state.connection_policy_changed} if notice else {})}
        if merged:
            out["merged"] = True
            out["said"] = (f"{rel} had changed since it was read (someone else wrote to it); this change was "
                           "merged with theirs, and nothing was lost.")
        return out

    # ── THE MEETING'S LISTENING BRIEF (transcript signals, 2026-10-15) ───────────────────────────
    # A small model listens to a live meeting for what the meeting's agent told it to; the brief is
    # the `listen:` block on the meeting's page, and this is its one writer (`meeting_listen`).

    def _signal_policy(subject: Optional[str] = None) -> dict:
        from control_plane import home as _home  # noqa: PLC0415
        try:
            root = _global_root() if callable(_global_root) else None
        except Exception:  # noqa: BLE001 — an unreadable company layer is the defaults
            root = None
        return meeting_listen_mod.policy(_home.read_global(root, "POLICIES.md"), subject=subject)

    def _meeting_row(subject: str, meeting: str) -> "dict | None":
        if _meeting_owner_lookup is None or not str(meeting or "").isdigit():
            return None
        row = _meeting_owner_lookup(str(subject), str(meeting))
        return row if isinstance(row, dict) else None

    @router.post("/api/meeting/listen")
    def meeting_listen(request: Request, body: MeetingListenBody = Body(...)):
        """SET OR REVISE WHAT VEXA LISTENS FOR IN A MEETING — the verb behind `meeting_listen`.

        Writes the `listen:` block in the meeting page's front matter (the people in the meeting see
        it) and bumps its `version`. Fields left out keep their current value. `addressed_to_vexa` is
        always kept and always wakes; `wake_at` and `max_wait_s` are held inside the company's bounds.
        A turn started by a meeting signal may set only its own meeting's brief."""
        subject = str(subject_of(request))
        signal_scope.require_meeting(request, _signal_grants, body.meeting)
        row = _meeting_row(subject, body.meeting)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "refused": "meeting_not_found", "meeting": body.meeting,
                "said": "No meeting with that id that you can read."})
        place = meeting_listen_mod.locate(wsr.root, subject, row)
        if place is None:
            raise HTTPException(status_code=404, detail={
                "refused": "no_meeting_page", "meeting": body.meeting,
                "said": "This meeting has no page yet, so there is nowhere to put the brief. It is "
                        "created when the bot is sent; ask the person to send it first."})
        rel, slug = place["path"], (place["workspace"] or None)
        target = _write_dir(request, subject, rel, slug)
        pol = _signal_policy(subject)
        with page_merge.write_lock(target):
            text = wpaths.read_text_inside(target, rel) or ""
            current, version = meeting_listen_mod.read_listen(text)
            try:
                brief = meeting_listen_mod.normalize(
                    body.brief.model_dump(exclude_none=True),
                    base=current or meeting_listen_mod.default_brief(pol))
            except meeting_listen_mod.BriefError as exc:
                raise HTTPException(status_code=422, detail={
                    "refused": "brief_not_valid", "said": str(exc)}) from None
            version += 1
            try:
                fbase, frel = wpaths.locate_inside(target, rel)
                wpaths.write_text_inside(fbase, frel, meeting_listen_mod.set_listen(text, brief, version))
            except wpaths.PathRefused as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            if (target / ".git").is_dir():
                run_git(target, "add", "--", rel)
                run_git(target, "-c", "user.name=vexa-agent", "-c", "user.email=agent@vexa.local",
                        "commit", "-m", attributed(f"listening brief v{version} for meeting {body.meeting}",
                                                   request.headers))
        listening = pol["transcript_signals"] == "on" and bool(pol["model_signals"])
        return {"meeting": body.meeting, "path": rel, "workspace": place["workspace"],
                "version": version, "brief": brief, "listening": listening,
                "said": (f"Listening brief v{version} set." if listening else
                         f"Listening brief v{version} saved; transcript signals are off for this "
                         "company, so nothing listens yet.")}

    @router.get("/internal/meetings/{row}/listen")
    def meeting_listen_internal(row: str, request: Request,
                                subject: Annotated[str, Query(min_length=1, max_length=200)]):
        """THE BRIEF, FOR `signals` — internal tier only. The meeting's `listen:` brief (the
        company's default brief when its page has none), its version, where the page is, the chat a
        signal wakes (`meet-<row>`, or the chat the meeting was started from) and the signal policy.
        ``subject`` is the meeting's owner; the row is read as them, so a meeting they cannot read
        is a 404."""
        if not _internal_caller(request):
            raise HTTPException(status_code=403, detail={
                "refused": "internal_tier_required",
                "said": "a meeting's listening brief is read by the signals service over the internal tier"})
        if not str(row).isdigit() or len(str(row)) > 20:
            raise HTTPException(status_code=400, detail="row must be a meeting row id")
        mrow = _meeting_row(subject, row)
        if mrow is None:
            raise HTTPException(status_code=404, detail={"refused": "meeting_not_found", "meeting": row})
        pol = _signal_policy(subject)
        place = meeting_listen_mod.locate(wsr.root, subject, mrow)
        brief, version = (meeting_listen_mod.read_listen(meeting_listen_mod.page_text(place))
                          if place is not None else (None, 0))
        try:
            chat = sess.chat_for(subject, f"meet-{row}") if sess is not None else f"meet-{row}"
        except Exception:  # noqa: BLE001 — no binding read: the meeting's own chat
            chat = f"meet-{row}"
        if chat == f"meet-{row}":
            # A CALL'S WAKE LANDS IN THE CHAT THAT HOLDS THE CALL (app.dev 2026-10-16, meeting 147):
            # a room or a private chat, wherever it was started — never a hidden session of its own.
            _store = d.get("session_store")
            try:
                held = _store.call_for_meeting(row) if _store is not None else None
                if held and _store.can_read(held["session_id"], subject):
                    chat = held["session_id"]
            except Exception:  # noqa: BLE001 — no call record: the meeting's own chat
                pass
        return {"brief": brief or meeting_listen_mod.default_brief(pol), "version": version,
                "default": brief is None,
                "workspace": place["workspace"] if place is not None else "",
                "page_path": place["path"] if place is not None else "",
                "chat_session": chat, "subject": subject,
                "policy": {k: pol[k] for k in ("transcript_signals", "model_signals",
                                                "signals_wakes_per_meeting",
                                                "signals_cost_per_meeting_usd")}}

    # ── REMOVING AND MOVING A PAGE (Vexa-ai/vexa#1621) ───────────────────────────────────────────
    #
    # Founder, session 176, 13:36Z: *"remove from personal"* — and there was no verb for it. The
    # agent moving the Example Bank dossier off the desk had `workspace_write`, which creates or overwrites,
    # and two read-only routes; so it collapsed each of the seven pages to a one-line pointer and had
    # to report that "removed" meant "collapsed". The files stayed on the desk (friction
    # `fr_a373e9448d2909a6`).
    #
    # A DELETE IS HISTORY, NEVER A LOSS. Both verbs commit in the workspace, so the bytes are one
    # `git show` away forever — which is what makes removing a page a safe thing for an agent to do
    # at all, and why neither of these needs a confirmation step the model would have to invent.

    def _movable_dir(request: Request, subject, rel: str, slug: Optional[str], *,
                     templates: bool = False) -> Path:
        """WHICH workspace dir a REMOVAL (or the source/target half of a move) lands in, or the
        refusal. `_write_dir`'s rules — path guard, `kg/templates/`, `_global` admin-only, shared
        needs contributor+, anything outside the active set 403s — plus ONE more.

        `_system` IS NOT A REMOVAL TARGET, ever. It is read-write in the mount stack because settings
        and `identity.md` live there and the platform writes them (chats do not: they are kept in the
        session store, ADR-0048);
        that is exactly why an agent verb must not be able to delete out of it. The write route
        deliberately lets `_system` through (a turn recording who it is helping writes there); the
        remove route deliberately does not, at either end of a move. Same reason `_global` is
        admin-only rather than open: the tiers that are not a person's own pages are not a place a
        turn tidies up."""
        if str(slug or "") == system_mounts.SYSTEM_SLUG:
            raise HTTPException(status_code=403, detail=(
                "`_system` holds identity and settings — the platform writes that tier (chats are "
                "kept in the session store, in no workspace); pages are removed from a personal "
                "workspace or a shared one, never from it"))
        return _write_dir(request, subject, rel, slug, templates=templates)

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

    def _page_subject(ws: str, rel: str, verb: str) -> str:
        """`<workspace>: <path> — <verb>`, at most 72 characters, and the VERB always survives.

        Clipping the whole string at 72 cut the verb off a long path, and History's Restore finds a
        removal by it — so a long path is shortened in the middle instead (`kg/…/name.md`)."""
        whole = f"{ws}: {rel} — {verb}"
        if len(whole) <= 72:
            return whole
        room = max(8, 72 - len(f"{ws}:  — {verb}") - 1)
        return f"{ws}: …{rel[-room:]} — {verb}"[:72]

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
        slug = write_slug(request, body.slug)
        # Removing a COMPANY template is allowed (the kind reverts to the baked shape); moving one
        # is not — a move would land a shape nobody validated, or take one out without a revert.
        target = _movable_dir(request, subject, rel, slug, templates=True)
        try:
            # CHECK ONCE, ACT ON WHAT WAS CHECKED: the removal goes through the located root and
            # link-free path by descriptor, so a link swapped in after the check removes nothing
            # outside this workspace (``workspace_paths.unlink_inside``)
            froot, frel = wpaths.locate_inside(target, rel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        st = wpaths.stat_inside(froot, frel) if frel else None
        if not frel or (st is not None and stat_mod.S_ISDIR(st.st_mode)):
            raise HTTPException(status_code=400, detail="that is a folder — this removes one page")
        if st is None or not stat_mod.S_ISREG(st.st_mode):
            raise HTTPException(status_code=404, detail="not found")
        notice = _policy_change(request, subject, target, rel, None)
        if not wpaths.unlink_inside(froot, frel):
            raise HTTPException(status_code=404, detail="not found")
        sha = _commit(target, [rel, *_kg_index_after(target, rel)],
                      _page_subject(target.name, rel, "removed") + (f"\n\n{notice}" if notice else ""), request)
        return {"path": rel, "deleted": True, "workspace": slug or "", "commit": sha}

    @router.post("/api/workspace/restore")
    def ws_file_restore(request: Request, body: WorkspaceRestoreBody = Body(...)):
        """PUT BACK one page a removal took out — the page view's **Undo** and History's **Restore**.

        Founder, 2026-10-11, on a junk entity page the agent had written: *"we probably need a
        delete for a note"*. The delete is `ws_file_remove` above, unchanged; this is what makes
        offering it to a person safe. A removal is a commit, so the bytes are its parent's — read
        out of git and written back exactly, then committed, so the restore is history too.

        THE SAME DOOR AS THE REMOVAL (`_movable_dir`): whoever could not have removed the page
        cannot put it back, `_system` and `kg/templates/` included. And it never overwrites: a page
        already at the path is a 409, because a restore that clobbers a newer page is a second loss
        dressed as an undo. ``commit`` must be a commit that removed ``path``; omitted, it is the
        newest such commit."""
        subject = subject_of(request)
        rel = str(body.path or "").strip()
        slug = write_slug(request, body.slug)
        target = _movable_dir(request, subject, rel, slug)
        try:
            froot, frel = wpaths.locate_inside(target, rel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not frel:
            raise HTTPException(status_code=400, detail="name the page to put back")
        if not (target / ".git").is_dir():
            raise HTTPException(status_code=404, detail="this workspace keeps no history to restore from")
        if wpaths.stat_inside(froot, frel) is not None:
            raise HTTPException(status_code=409, detail=(
                "a page is already at that path — move or remove it first; a restore never overwrites"))
        sha = str(body.commit or "").strip()
        if sha:
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
                raise HTTPException(status_code=400, detail="`commit` is not a commit id")
            took = run_git(target, "show", "--no-renames", "--diff-filter=D", "--name-only",
                           "--format=", sha, "--", rel)
            if took.returncode != 0 or rel not in took.stdout.splitlines():
                raise HTTPException(status_code=404, detail="that commit did not remove this page")
        else:
            sha = run_git(target, "log", "-1", "--no-renames", "--diff-filter=D", "--format=%H",
                          "--", rel).stdout.strip()
            if not sha:
                raise HTTPException(status_code=404, detail="no removal of this page in the history")
        before = run_git(target, "show", f"{sha}^:{rel}", text=False)
        if before.returncode != 0:
            raise HTTPException(status_code=404, detail="the history has no copy of this page")
        try:
            wpaths.write_bytes_inside(froot, frel, before.stdout)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        head = _commit(target, [rel, *_kg_index_after(target, rel)],
                       _page_subject(target.name, rel, "restored"), request)
        return {"path": rel, "restored": True, "workspace": slug or "", "commit": head,
                "from_commit": sha}

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
        src_rel = str(body.path or "").strip()
        dst_rel = str(body.to or "").strip()
        src_slug = write_slug(request, body.slug)
        # NO `to_slug` MEANS THE SAME WORKSPACE — the ordinary rename. Defaulting it to the caller's
        # desk instead would turn every rename inside a shared workspace into a silent extraction of
        # a page out of it, which is the failure `_writeback_workspace_note` already documents for
        # `entity_upsert`'s slug default. `personal` names the desk, as on every page verb.
        asked_to = (body.to_slug or "").strip()
        dst_slug = write_slug(request, asked_to) if asked_to else src_slug
        src = _movable_dir(request, subject, src_rel, src_slug)
        dst = _movable_dir(request, subject, dst_rel, dst_slug)
        try:
            sbase, srel = wpaths.locate_inside(src, src_rel)
            dbase, drel = wpaths.locate_inside(dst, dst_rel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if srel and wpaths.is_dir_inside(sbase, srel):
            raise HTTPException(status_code=400, detail="that is a folder — this moves one page")
        # read ONCE, nofollow, through what was resolved; written the same way
        content = wpaths.read_bytes_inside(sbase, srel) if srel else None
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        if (sbase / srel) == (dbase / drel):
            raise HTTPException(status_code=400, detail="the page is already there")
        same_workspace = src.resolve() == dst.resolve()
        # A NAME IS CONTENT TOO (D6): the new filename lands where the destination's readers see it,
        # and the page itself lands there when it crosses workspaces.
        _hold_private(request, subject, dst_slug, dst_rel, dst, surface="filename",
                      content=None if same_workspace else content)
        text = content.decode("utf-8", "replace")
        notice = " · ".join(n for n in (
            _policy_change(request, subject, src, src_rel, _pointer_stub(dst_rel) if same_workspace and
                           src_rel.lower().endswith(".md") else None),
            _policy_change(request, subject, dst, dst_rel, text)) if n)
        try:
            wpaths.write_bytes_inside(dbase, drel, content)
            # A STUB IS A PAGE, so only a page gets one. A markdown pointer written over
            # `assets/logo.png` is a broken picture wearing a helpful sentence.
            stub = same_workspace and src_rel.lower().endswith(".md")
            if stub:
                wpaths.write_text_inside(sbase, srel, _pointer_stub(dst_rel))
            else:
                wpaths.unlink_inside(sbase, srel)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        subject_line = f"{dst.name}: {dst_rel} — moved from {src_rel}"[:72] + (f"\n\n{notice}" if notice else "")
        if same_workspace:
            sha = _commit(src, [src_rel, dst_rel, *_kg_index_after(src, src_rel, dst_rel)],
                          subject_line, request)
            return {"from": src_rel, "to": dst_rel, "workspace": src_slug or "",
                    "to_workspace": dst_slug or "", "moved": True,
                    "pointer": src_rel if stub else None, "commit": sha, "source_commit": sha}
        # TWO REPOSITORIES, TWO COMMITS — the target first, so the moment between them is one where
        # the page exists twice rather than not at all.
        to_sha = _commit(dst, [dst_rel, *_kg_index_after(dst, dst_rel)], subject_line, request)
        from_sha = _commit(src, [src_rel, *_kg_index_after(src, src_rel)],
                           f"{src.name}: {src_rel} — moved out"[:72] + (f"\n\n{notice}" if notice else ""),
                           request)
        return {"from": src_rel, "to": dst_rel, "workspace": src_slug or "",
                "to_workspace": dst_slug or "", "moved": True, "pointer": None,
                "commit": to_sha, "source_commit": from_sha}

    @router.post("/api/workspace/entity")
    def ws_entity_upsert(request: Request, body: EntityUpsertBody = Body(...)):
        """RECORD what was just learned about a person, company, meeting, project or decision — the
        verb behind `entity_upsert` (PRD decision 24). Use it the moment a turn learns anything
        durable; a name without a page gets one now.

        One call creates `kg/entities/<kind>/<slug>.md` if it is missing and updates it in place if
        it is there — never check first, never merge by hand. The page is a CARD (decision 24.6): a
        summary, the kind's sections (file facts into them with `fields`, or with `section`),
        `## Connected` chips both ways, `## Sources`, `## Open questions`, and `## Timeline` last for
        anything dated. A refusal names what to fix: 400 is an argument in a shape this does not
        read (send the same facts again in the shape it names), 422 is a rule (fix the fact, do not
        retry). The answer lists `links_missing` — names with no page yet, which are the next calls.

        Authorization is `ws_file_write`'s, because it is the same act — a write into a workspace —
        and two spellings of one authorization rule is how the second one ends up weaker. It
        refreshes `kg/INDEX.md` and commits both by pathspec with the F31 subject shape.
        """
        subject = subject_of(request)
        kind = body.kind.strip().lower()
        name = body.name.strip()
        source = body.source.strip()
        slug = write_slug(request, body.slug)
        facts = [str(f) for f in body.facts if str(f).strip()]
        summary = body.summary.strip()
        questions = [str(q) for q in body.open_questions]
        # #1624 — the card's free text goes through the same door a plain page does, in ONE call so
        # a logo named in both the summary and a fact costs the host one question, not two.
        _refuse_sensitive([name, summary, *facts, *questions, source,
                           *[str(v) for v in (body.fields or {}).values()]])
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
            if not global_layer.is_admin_account(settings, str(subject)):
                raise HTTPException(status_code=403,
                                    detail="only the admin account may write company-tier pages into _global")
            global_target = _writable_global()
            if global_target is None:
                raise HTTPException(status_code=404, detail="the organisation tier is not writable here")
        elif slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError:
                pass  # not shared — _read_target 403s anything outside the active set
        target = global_target or _read_target(request, slug, write=True)
        _hold_private(request, subject, slug, f"kg/entities/{kind}/{name}", target,
                      content=body.model_dump() if hasattr(body, "model_dump") else None,
                      text="\n".join([name, summary, *facts, *questions, source,
                                       *[str(v) for v in (body.fields or {}).values()]]))
        # ONE WRITER AT A TIME PER WORKSPACE: two parallel upserts of pages that link each other
        # merged into duplicate `## Connected` chips (a real workspace, 2026-10-14).
        with page_merge.write_lock(target):
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
                    mounts=_link_mounts(_entity_mounts(subject, request), target), dates=body.dates,
                    summary=summary,
                    fields=body.fields,
                    section=body.section.strip(),
                    connections=body.connections or (),
                    open_questions=questions,
                    # THE COMPANY'S SHAPE for this kind, when the admin account wrote one
                    # (`_global/kg/templates/<kind>.md`); a new card takes its sections, and one that is
                    # not a usable shape is ignored in favour of the baked one (`kg_templates`).
                    template=kg_templates.company_template(_global_dir(), kind))
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
            # A PAGE WRITTEN OUTSIDE ITS KIND'S FOLDER is filed into it, and the index lists it.
            moved = entities_mod.file_strays(target)
            index_rel = entities_mod.write_index(target, slug or str(subject))
            sha = None
            if result.get("changed") or moved:
                tracked = [old for old, _new in moved
                           if run_git(target, "ls-files", "--error-unmatch", "--", old, check=False).returncode == 0]
                sha = entities_mod.commit_entity(
                    target, [result["path"], index_rel, *result.get("back_links", ()),
                             *[new for _old, new in moved], *tracked],
                    subject_path=result["path"], created=bool(result.get("created")),
                    author=(str(subject), f"{subject}@vexa.local"),
                    trailers=writer_trailers(request.headers))
            ref = links_mod.page_ref(slug or str(subject), result["path"]) if result.get("path") else None
            return {**result, "index": index_rel, "commit": sha, **({"ref": ref} if ref else {})}
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
        tier, and reading one's own settings is not a leak (chats are in the session store, read with
        `chat_read`). But `_global/POLICIES.md` says
        of it, in as many words, *"`_system` is read by no agent for anybody else — chats, sessions
        and settings are the one genuinely private tier"*, and this route exists to put a workspace's
        history on a page. Settings are not a workspace's history; there is no `_system` README and
        no panel that would show this. Refusing it here costs nothing anybody asked for.

        ``personal`` IS THE DESK. The terminal's desk tab carries no slug at all (its file reads pass
        none, and ``_read_target`` answers the caller's primary for an absent one) — but a path
        segment cannot be absent, so the desk needs a name here. ``personal`` is the one the rest of
        the system already uses for exactly this (``workspace_attach.PERSONAL_ALIAS``, the MCP, the
        panel's own breadcrumb), and it resolves through the same absent-slug branch rather than
        through a second lookup."""
        if slug == system_mounts.SYSTEM_SLUG:
            raise HTTPException(status_code=403, detail=(
                "_system is identity and settings, not a workspace with a history; chats are kept "
                "in the session store"))
        target_slug = None if slug in ("", "personal") else slug
        try:
            target = _read_target(request, target_slug)  # authorizes exactly as the file read does
            return {"slug": slug,
                    **wsr.git_history_at(target, path=path, limit=limit,
                                         viewer=subject_of(request))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid path or subject")

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
        could not open. `_system` is refused here for the reason it is refused there: identity and
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
                "_system is identity and settings, not a workspace with a history; chats are kept "
                "in the session store"))
        target_slug = None if slug in ("", "personal") else slug
        try:
            target = _read_target(request, target_slug)   # authorizes exactly as the file read does
            hpath, change = last_change(wsr, target, slug=slug, path=path, viewer=subject_of(request),
                                        global_dir=_global_dir())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid path or subject")
        return {"slug": slug, "path": hpath, "change": change}

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
        # THE ADDRESS IS PART OF THE QUESTION (Vexa-ai/vexa#1642). The gateway strips any
        # client-sent `x-user-email` and re-injects the one it resolved from the session, so this is
        # the reader's own verified address — the key every step of the chain is actually written
        # against, and the floor under it when no page names them.
        address = (request.headers.get("x-user-email") or "").strip() or None
        name = front_page_mod.display_name(wsr.root, subject, email=address, global_dir=_global_dir())
        return {"subject": subject, "name": name,
                "first_name": front_page_mod.first_name(name)}

    @router.get("/api/people/admin")
    def people_admin(request: Request):
        """WHO WRITES THE COMPANY LAYER, by name (Vexa-ai/vexa#1642).

        Founder, 2026-09-07, on his own instance: the line read *everyone at Vexa reads it, **the
        admin** writes it* on the one deployment where the administrator is certainly known. *The
        admin* is a role, and the first line a person meets should name the person.

        WHERE THE ANSWER COMES FROM, and why it needs nothing new. `_global/STRUCTURE.md` states it:
        *"every acceptance is a commit authored by the administrator who made it"*
        (`global_layer.commit_global` sets `user.name`/`user.email` to that person). So the company
        layer's own history IS the record of who writes it, and the newest commit there that a
        person made names them.

        GATED ON `global_admin_only`. With it on, the layer has exactly one writer by policy, so
        "the newest author" and "the admin" are the same person and the claim is safe. With it off
        they are not — and the sentence does not name a writer in that case anyway, so the honest
        answer is `null` rather than the most recent editor promoted to administrator.

        NEVER AN ADDRESS ON THE WIRE. The name and its first word, and nothing else: the strip may
        not print an address (#1634 rule 3) and a route that hands one over invites it to."""
        empty = {"name": None, "first_name": None}
        try:
            target = _read_target(request, system_mounts.GLOBAL_SLUG)
        except (ValueError, HTTPException):
            return empty
        policies = page_head(target, "POLICIES.md") or ""
        if str(front_page_mod.front_matter(policies).get("global_admin_only", "")).strip().lower() \
                not in ("on", "true", "yes"):
            return empty
        try:
            history = wsr.git_history_at(target, limit=ADMIN_SCAN, viewer=subject_of(request))
        except ValueError:
            return empty
        author, email = front_page_mod.admin_principal(history.get("commits") or [])
        if not author and not email:
            return empty
        name = front_page_mod.display_name(wsr.root, address=author, principal=email, global_dir=_global_dir())
        return {"name": name, "first_name": front_page_mod.first_name(name)}

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
                                detail="resetting a personal workspace to a witnessed sha is an "
                                       "internal-tier capability")
        sha = str((body or {}).get("sha") or "").strip()
        if not sha:
            raise HTTPException(status_code=400, detail="`sha` is required — the witness to reset to")
        try:
            target = home_of(wsr, subject_of(request))    # the repo the witness was read from (#1783)
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
        # us one: a desk called "Personal 126" is better than `126` and worse than the person's own
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

    @router.get("/api/claims")
    def company_context(request: Request):
        """The validated company context — only the claims a person CONFIRMED or CORRECTED. This is
        what an agent may rely on about their company; proposed claims are counted, never shown,
        because nobody has stood behind them yet. Read it before writing anything that describes
        the company."""
        try:
            return claims_mod.context(wsr.workspace_dir(subject_of(request)))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject") from None

    @router.post("/api/claims")
    def write_claims(request: Request, body: ClaimsProposeBody = Body(...)):
        """Record what an agent believes about this person's company as PROPOSED — and tell flows,
        once per claim. Put everything learned in ONE call, show the person the lines it hands
        back, and record their answer with `validate`. A proposed claim is never company context
        until a person has answered: an agent cannot promote its own guess.

        THIS ROUTE EXISTS SO THE FACT HAS A PRODUCER. The book was written through
        `PUT /api/workspace/file`, a generic route that holds bytes and knows nothing about what
        they mean — so the one moment worth telling anybody about, a claim being proposed, was
        indistinguishable from any other file write, and `claim.proposed` had no publisher. A
        generic route cannot publish a specific fact without inspecting paths and guessing at
        contents, which is how a file route quietly becomes a state machine nobody declared.

        ONE EVENT PER CLAIM, and only for claims this call actually added: `await_claim` looks a
        `claim_id` up in the book and blocks on that claim's own words, so one event for a batch
        would be one card for three questions with no way to answer two of them.

        It lives in the workspaces router because the book is a file in a personal workspace and everything this
        needs — the subject, the reader, the workspace path — is already closed over here; the state
        machine itself is `control_plane.claims`, which is the concern.

        A ROOM'S OWN AGENT (a workspace principal, D2) has no book and no desk of its own. What it
        proposes (a room's signal turn may only propose, D27) waits in the book of the person who
        answers for the room — its payer, `x-user-bills-to`, which identity signs: the workspace
        owner, or in a room of two the person who started the turn. Nothing is ever written under the
        principal's own id; a principal with no payer named proposes nothing."""
        if identity_token.is_workspace_principal(request.headers):
            subject = (request.headers.get(identity_token.DELEGATION_HEADERS["bills_to"]) or "").strip()
            if not subject:
                raise HTTPException(status_code=403, detail=identity_token.SHARED_REFUSAL)
        else:
            subject = subject_of(request)
        batch = [b if isinstance(b, str) else b.model_dump() for b in body.claims]
        if not batch:
            raise HTTPException(status_code=400, detail="claims must be a non-empty list")
        try:
            result = claims_mod.propose(home_of(wsr, subject), batch)
        except wpaths.PathRefused:
            # the book's folder or file is a link, not a plain path on this desk — never followed
            raise HTTPException(status_code=409, detail="the claims book in this workspace is not a plain "
                                "file") from None
        for cid in result["ids"]:
            publish_mod.publish(publish_mod.EVENT_CLAIM_PROPOSED,
                                publish_mod.claim_source_id(subject, cid),
                                publish_mod.claim_refs(subject, cid))
        return result

    @router.post("/api/claims/verdicts")
    def record_claim_verdicts(request: Request, body: ClaimVerdictsBody = Body(...)):
        """Record a PERSON's word on proposed claims — `confirmed`, `corrected` (the note is the
        correction; the original stays beside it) or `rejected`. Put their whole answer in ONE call,
        and only after asking them: this is their word, never an agent's guess about it.

        The first claim they stand behind makes their personal workspace ready, so answering finishes the setup.
        An unknown id or verdict comes back in `errors` and the rest of the answer still lands."""
        subject = subject_of(request)
        if not body.verdicts:
            raise HTTPException(status_code=400, detail="verdicts must be a non-empty list")
        try:
            return claims_mod.record_verdicts(home_of(wsr, subject),
                                              [v.model_dump() for v in body.verdicts])
        except wpaths.PathRefused:
            raise HTTPException(status_code=409, detail="the claims book in this workspace is not a plain "
                                "file") from None
    @router.get("/api/workspaces/by-slug/{slug}")
    def ws_id_by_slug(slug: str, request: Request):
        """The identity of a workspace addressed the OLD way — by slug. What the terminal calls to
        put a NAME where it used to print a directory name (F49: the chat header read `126`)."""
        require_in_ceiling(request, slug)
        subject = subject_of(request)
        # Private attached roots resolve within their owner's slots, never as
        # instance-wide desks in the global registry.
        own = attached_workspaces(wsr.root, subject)
        slot = own.get("slots", {}).get(slug)
        if slot is not None and slug not in (own.get("active"), "seed") and workspace_slot_dir(wsr.root, subject, slug).is_dir():
            return {"id": slug, "slug": slug, "name": slot.get("name") or slug,
                    "kind": "private", "access": "readable", "writable": True}
        rec = workspace_registry.by_slug(slug) or _ws_lookup(slug)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        if access == ids_mod.ACCESS_NOT_YOURS and not _sees_names(subject):
            return ids_mod.not_yours_view(slug=slug)
        return ids_mod.view(rec, access, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member))
    def _sees_names(subject) -> bool:
        """May this reader see the NAME of a workspace it is not a member of? The admin account and
        instance admins (they rename groups and support the instance); nobody else. Asked only for a
        `not-yours` answer, so an ordinary read costs no identity round trip."""
        return bool(global_layer.is_admin_account(settings, str(subject))
                    or global_layer.is_admin(settings, str(subject)))
    def _id_in_ceiling(request: Request, workspace_id: str) -> None:
        """The ceiling names workspaces by slug; the id routes name one by id, so ask by both."""
        known = workspace_registry.get(workspace_id) or {}
        require_in_ceiling(request, str(known.get("slug") or workspace_id))

    @router.get("/api/workspaces/{workspace_id}")
    def ws_id_resolve(workspace_id: str, request: Request):
        """`{id, name, kind, access}` for one workspace id, from THIS reader's point of view.

        Never 404s and never 403s: `not-yours` and `gone` are ANSWERS (decision 26.3), and a status
        code would make the client render an error where the design says render a greyed chip."""
        _id_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        rec = workspace_registry.get(workspace_id)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        if access == ids_mod.ACCESS_NOT_YOURS and not _sees_names(subject):
            return ids_mod.not_yours_view(workspace_id=workspace_id)
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
        _id_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        # A WORKSPACE'S NAME IS READ BY EVERY MEMBER (D6): held like a page write.
        _rec0 = workspace_registry.get(workspace_id) or {}
        _slug0 = str(_rec0.get("slug") or workspace_id)
        try:
            _dir0 = membership_mod._ws_dir(Path(wsr.root), _slug0)
        except Exception:  # noqa: BLE001 — an unresolvable workspace has no roster to read
            _dir0 = None
        if _dir0 is not None:
            _hold_private(request, subject, _slug0, ".vexa/name", _dir0, surface="workspace_name",
                          content=str(body.get("name") or ""))
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
        with a link to its git home instead of the publish action."""
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
        # `person` in routes.v1.json: loads a repository with the person's saved git credentials
        require_in_ceiling(request, body.slug)
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
        first, then any activated extras). Each: ``slug, repo, ref, role, path, write, primary``.

        THE ADMIN ACCOUNT's set is the company layer and nothing else — the same answer its dispatch
        mounts (`dispatch.build_mount_set`) — and says so with `admin_account: true`, the one fact the
        terminal needs to show it only Company. Every other subject's answer carries no such key."""
        subject = subject_of(request)
        if global_layer.is_admin_account(settings, str(subject)):
            return {
                "subject": subject,
                "active": [{"slug": system_mounts.GLOBAL_SLUG, "repo": "", "ref": "", "role": "global",
                            "path": str(_global_dir()), "write": True, "primary": False,
                            "name": None}],
                "index_degraded": False,
                "admin_account": True,
            }
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
                 "path": m.path, "write": m.write, "primary": m.primary, "name": m.name,
                 # the person's own desk (Personal) — not the same fact as `primary` (#1783)
                 "desk": m.desk}
                for m in mounts
            ],
            "index_degraded": index_degraded,
        }
    @router.post("/api/workspace/import", status_code=202)
    def ws_import(request: Request, body: WorkspaceImportBody):
        """Import a repository as its own workspace; preserve Personal and the repository tree.

        Use this for public AND private repository URLs, never WebFetch as an access test.
        Returns an operation_id immediately. Poll workspace_import_status until completed;
        queued/running is not success. Stored credentials are resolved inside the agent service.
        credential_workspace optionally reuses the deploy key of a workspace you own.
        """
        # `person` in routes.v1.json: loads a repository with the person's saved git credentials
        require_in_ceiling(request, body.credential_workspace)
        subject = subject_of(request)
        repo = _repo(body.repo)
        try:
            ref = repo_ref.valid_ref(body.ref)
        except repo_ref.RepoRefError as exc:
            raise HTTPException(422, str(exc)) from exc
        credential_workspace = (body.credential_workspace or "").strip()
        if credential_workspace:
            try:
                membership_mod.require_role(wsr.root, credential_workspace, subject, "owner")
            except MembershipError as exc:
                raise _member_error(exc)
        key = deploy_keys_mod.workspace_key(subject=subject, workspace_id=credential_workspace)
        has_key = deploy_keys_mod.exists(wsr.root, key)
        # SSH first only when this import names the workspace whose deploy key it reuses. A key the
        # person merely HAS is the fallback for an https clone refused for want of a credential.
        use_key = bool(credential_workspace) and has_key
        repo = imports.repository_url(repo, use_deploy_key=use_key)
        fallback = imports.ssh_form(repo) if has_key and not use_key else None
        slug = imports.workspace_slug(repo, ref)

        def clone_into_workspace(url: str):
            with wcreds.for_workspace(wsr.root, key=key, repo_url=url, subject=subject, explicit_token=body.token) as cred:
                return activate_workspace(wsr.root, subject, url, ref, slug=slug, token=cred.token,
                                          clone=_clone_fn(cred))

        def operation():
            used = repo
            try:
                result = clone_into_workspace(repo)
            except CloneError as exc:
                detail = redact_secrets(exc)
                if not (fallback and wcreds.is_auth_failure(str(detail))):
                    raise _credential_refusal(f"git clone failed: {detail}", subject, credential_workspace or None, repo)
                used = fallback
                try:
                    result = clone_into_workspace(fallback)
                except CloneError as exc2:
                    raise _credential_refusal(f"git clone failed: {redact_secrets(exc2)}", subject,
                                              credential_workspace or None, fallback)
            name = repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            rename_workspace(wsr.root, subject, result.slug, name)
            if credential_workspace:
                bind_repository_credential(wsr.root, subject, result.slug, credential_workspace)
            return {"workspace": result.slug, "slug": result.slug, "cloned": result.cloned,
                    "changed": result.changed, "repo": used, "ref": ref,
                    "name": name}
        return imports.start(wsr.root, subject, repo, ref, operation)

    # `/status` keeps this route from matching the same URL as `/api/workspace/{slug}/deploy-key`
    # (slug "import", operation "deploy-key"): see tests/test_route_table.py.
    @router.get("/api/workspace/import/{operation_id}/status")
    def ws_import_status(operation_id: str, request: Request):
        """Read your repository import. Only completed confirms an independently usable workspace.

        Poll this after workspace_import; report failures verbatim. Never start a duplicate import
        or report success while status is queued/running/interrupted.
        """
        return imports.status(wsr.root, subject_of(request), operation_id)

    @router.post("/api/workspace/activate")
    def ws_activate(request: Request, body: WorkspaceActivateBody = Body(default=WorkspaceActivateBody())):
        """ADD a workspace to the active set WITHOUT parking the others (the additive counterpart of swap).
        Clones/restores the target if needed. Idempotent — an already-active workspace is a no-op."""
        require_in_ceiling(request, body.slug)
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
                "cloned": result.cloned}
    @router.post("/api/workspace/new", status_code=201)
    def ws_new(request: Request, body: WorkspaceNewBody = Body(default=WorkspaceNewBody())):
        """CREATE a brand-new BLANK workspace (seeded from the template) and ADD it to the active set.
        Pass the `name` the person said: the answer's `slug` is that name in kebab case
        ("eugenia - dmitry" → `eugenia-dmitry`, suffixed `-2` when taken), and every later verb
        addresses the workspace by it. Made in a chat, it joins that chat and becomes where the chat
        writes — say so in one line. The workspace starts PRIVATE; to share it, call
        `workspace_invite` on that slug — the first invite makes it shared with the caller as owner,
        and its answer gives the shared workspace's `workspace_id` to use from then on. Nothing else is
        parked, rebuilt or backed up."""
        if body.slug:
            require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            result = create_workspace(wsr.root, subject, name=body.name or None, slug=body.slug or None)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=f"the slug {body.slug!r} is taken — choose another")
        except ValueError as exc:
            if body.slug:
                raise HTTPException(status_code=422, detail=str(exc))
            raise HTTPException(status_code=400, detail="invalid subject")
        # REGISTERED AT BIRTH, so the chat header's by-slug check can name it the moment it exists,
        # and ANSWERED AS `created` — the key the harness's `focus` event reads to mount the new
        # workspace in this chat and make it where the chat writes (Vexa-ai/vexa#1783: a workspace
        # made in a chat kept the chat writing to Personal, and the Writes-to menu could not offer it).
        name = (body.name or "").strip() or None
        _ws_sync(result.slug, kind="desk", owner=str(subject), name=name,
                 ws_dir=workspace_slot_dir(wsr.root, subject, result.slug))
        # ITS STANDARD ICON, picked from the name at birth (founder 2026-10-18), changeable any time
        from control_plane import workspace_icon as _wicon  # noqa: PLC0415
        _wicon.assign_glyph(workspace_slot_dir(wsr.root, subject, result.slug), name or "", result.slug,
                            author=str(subject))
        return {"subject": result.subject, "slug": result.slug, "created": result.slug,
                "name": name or result.slug, "changed": result.changed, "added": True,
                "say": (f"Created {name or result.slug} — its slug is `{result.slug}`. Say so, and that "
                        "the person can rename it or pick another slug.")}
    @router.post("/api/workspace/deactivate")
    def ws_deactivate(request: Request, body: WorkspaceDeactivateBody = Body(...)):
        """REMOVE a workspace from the active set (park it — never destroyed). The private baseline can be
        switched off too (sets ``baseline_hidden``; its home tree is untouched, re-activate to switch it back
        on). Idempotent — an already-off / not-active slug is a no-op."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            result = deactivate_workspace(wsr.root, subject, body.slug)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed}
    @router.post("/api/workspace/publish")
    def ws_publish(request: Request, body: WorkspacePublishBody = Body(...)):
        """Publish a vexa-born workspace to one of this deployment's git providers: create the repository
        (on `provider`, by name — `git_providers` lists them; the default when omitted — under `org`: a
        GitHub org, or a GitLab group path, nested allowed) and push the current branch's full history.
        Uses the person's saved token for that provider (Connections → Git); a token is never an agent
        argument. `remote_url` skips creation and pushes to a pre-created empty repository. Re-publish is
        a plain fast-forward push, never a force push. Answers the repository's web link and provider.
        A rejected token is a typed refusal (`token_rejected`, naming the provider). A person-in-the-loop verb.
        From a chat the repository is always created private under the token's own account: `org` and
        `remote_url` are refused there (`publish_from_the_ui`, the person publishes from the UI), and a
        chat other people read cannot publish at all (`shared_conversation`)."""
        # `person` in routes.v1.json: spends the person's git credential and changes where the tree syncs
        subject = subject_of(request)
        # A chat other people read never publishes: the person's token and repository are theirs alone.
        ceiling_mod.require_private(request)
        require_in_ceiling(request, body.slug or subject)
        # A worker acting for the person creates the repository PRIVATE, under the token's own
        # account; naming an owner or a pre-created repository is the person's call, made in the UI.
        private, org, remote_url = body.private, body.org, (body.remote_url or "").strip()
        if ceiling_mod.is_delegated(request):
            if (org or "").strip() or remote_url:
                raise ceiling_mod.refused(request, 403, ceiling_mod.refusal(
                    "publish_from_the_ui",
                    "From a chat a workspace is published only as a private repository under the "
                    "person's own account. To publish under an organisation or group, or into an "
                    "existing repository, tell the person to publish it from the workspace's Publish "
                    "dialog in the UI; do not retry it another way."), reason="publish_from_the_ui")
            private = True
            # PUBLISHING IS AN ACT A CARD SUGGESTS (M1): the workspace's history leaves the platform
            # for a git provider, so the person's watching agent does it only on their press of a
            # card naming the workspace or the repository.
            if not is_unwatched(request):
                from control_plane.routers import asks  # noqa: PLC0415
                title = None
                if body.slug and d.get("workspace_registry") is not None:
                    try:
                        title = (ids_mod.resolve_slug(wsr.root, body.slug,
                                                      registry=d["workspace_registry"]) or {}).get("name")
                    except Exception:  # noqa: BLE001 — an unreadable registry names nothing more
                        title = None
                names = {str(n) for n in (body.repo_name, body.slug, title) if n}
                asks.require_press(d.get("session_store"), request, body.pressed, subject=str(subject),
                                   targets=[names] if names else [], act="Publishing a workspace")
        # slug → any workspace the caller can manage (own parked slot or shared membership, resolved
        # + permission-checked by _manage_dir); omitted keeps the legacy seed target.
        ws_dir = _manage_dir(request, body.slug) if body.slug else None
        if remote_url and not wcreds.is_https(remote_url):
            raise HTTPException(status_code=400, detail="publish pushes only to an https:// repository URL")
        # Where a NEW repository would be created, decided before any credential is read: a configured
        # provider, by name (or by its host, the earlier spelling). Anything else is refused by name
        # (P18), never probed.
        target = None
        if not remote_url:
            try:
                target = resolve_provider(body.provider, body.host)
            except UnsupportedHostError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        token = (body.token or "").strip()
        if not token:
            # A saved token goes only to its own provider: the one this call creates on, or the one a
            # remote_url is on. Any other host needs a token typed for this call.
            saved_for = target if target is not None else wcreds.token_destination.saved_token_provider(remote_url)
            if saved_for is not None:
                token = git_creds.read_token(wsr.root, subject, saved_for) or ""
        if not token:
            where = target.label if target is not None else "that repository's host"
            raise HTTPException(status_code=400, detail=f"an access token is required — pass one, or save a "
                                                        f"token for {where} in Connections → Git")
        try:
            result = publish_workspace(
                wsr.root, subject,
                token=token, repo_name=body.repo_name, private=private,
                org=org or None, remote_url=remote_url or None, ws_dir=ws_dir,
                host=target.netloc() if target is not None else None,
                provider=target.name if target is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid subject")
        except UnsupportedHostError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RepoExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))   # already token-redacted (P15)
        except TokenRejectedError as exc:
            raise HTTPException(status_code=422, detail={"code": "token_rejected", "provider": exc.provider,
                                                         "message": str(exc)})
        except PublishError as exc:
            raise HTTPException(status_code=502, detail=str(exc))   # already token-redacted (P15)
        return {
            "repo_url": result.repo_url,
            "pushed_ref": result.pushed_ref,
            "head_sha": result.head_sha,
            "created": result.created,
            "provider": result.provider,
        }
    @router.post("/api/workspace/rename")
    def ws_rename(request: Request, body: WorkspaceRenameBody = Body(...)):
        """RENAME one of the person's own private workspaces, by its `slug`: the name the person sees.
        The slug stays as it is (verbs keep addressing it), and the name is what the workspace is
        called when it is first shared. An empty `name` clears it."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            return rename_workspace(wsr.root, subject, body.slug, body.name)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
    def _git_provider_or_404(name: Optional[str]):
        p = git_provider.registry().by_name(name) if (name or "").strip() else git_provider.registry().default()
        if p is None:
            raise HTTPException(status_code=404, detail=f"no git provider named '{name}' on this deployment — "
                                                        f"configured: {', '.join(git_provider.registry().names())}")
        return p
    def _git_providers_state(subject: str) -> list[dict]:
        out = []
        for p in git_provider.registry().all():
            tok = git_creds.read_token(wsr.root, subject, p)
            out.append({**p.describe(), "token_saved": tok is not None, "masked": git_creds.masked(tok)})
        return out
    @router.get("/api/workspace/git-providers")
    def ws_git_providers(request: Request):
        """The git providers this deployment reaches — GitHub, a GitHub Enterprise, a self-hosted GitLab —
        each with its name, kind, label, address, whether its owners may be nested groups, whether a
        repository can be created there, and whether the person has saved a token for it (masked, never
        the value). Read this before publishing to pick `provider` and to know which owner shape `org` takes."""
        subject = subject_of(request)
        return {"default": git_provider.registry().default().name, "providers": _git_providers_state(subject)}
    @router.get("/api/workspace/git-token")
    def ws_git_token_get(request: Request, provider: Optional[str] = None):
        """Whether the caller has a SAVED reusable token for a git provider (the default when `provider`
        is omitted), and a masked (last-4) preview of it. The clear value is NEVER returned —
        server-side only (git_credentials). `providers` lists every configured provider's state."""
        subject = subject_of(request)
        p = _git_provider_or_404(provider)
        tok = git_creds.read_token(wsr.root, subject, p)
        return {"set": tok is not None, "masked": git_creds.masked(tok), "provider": p.name,
                "providers": _git_providers_state(subject)}
    @router.post("/api/workspace/git-token")
    def ws_git_token_set(request: Request, body: GitTokenBody = Body(default=GitTokenBody())):
        """Save (or CLEAR, with an empty token) the caller's reusable token for one git provider (the
        default when `provider` is omitted) — stored once in the credential broker and applied as the
        fallback credential for every git op on that provider. Returns the masked state, never the value."""
        subject = subject_of(request)
        p = _git_provider_or_404(body.provider)
        try:
            stored = git_creds.set_token(wsr.root, subject, body.token, p)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"set": stored, "masked": git_creds.masked_token(wsr.root, subject, p), "provider": p.name}
    @router.get("/api/workspace/git-remote-status")
    def ws_git_remote_status(request: Request, slug: Optional[str] = None):
        """The git-sync state of a workspace (default = the caller's primary; ``slug`` = one of their
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
        ws = (_read_target(request, slug) if slug == system_mounts.GLOBAL_SLUG
              else _manage_dir(request, slug))
        s = remote_status(ws)
        note = _credential_note(str(subject_of(request)), slug) if slug and slug != system_mounts.GLOBAL_SLUG else None
        return {
            "has_home": s.has_home, "remote": s.remote, "url": s.url, "branch": s.branch,
            "tracked": s.tracked, "ahead": s.ahead, "behind": s.behind,
            # a GitHub login inherited from a deleted workspace, said before a sync fails (#1783)
            **({"credential": note} if note else {}),
        }
    @router.post("/api/workspace/push")
    def ws_push(request: Request, body: WorkspacePushBody = Body(...)):
        """Push a workspace's current branch to its git home (origin for attached clones, vexa-publish
        for published vexa-born), fast-forward only — NEVER a force push. The token authenticates the push
        and is never stored; a diverged remote fails loud (pull first). Every error is token-redacted (P15)."""
        # `person` in routes.v1.json: spends the person's git credential
        subject = subject_of(request)
        # A chat other people read never pushes with the person's credential.
        ceiling_mod.require_private(request)
        require_in_ceiling(request, body.slug or subject)
        ws = _manage_dir(request, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                if cred.kind == "none":
                    raise HTTPException(status_code=400, detail=(
                        "this workspace has no credential — add its deploy key to the repository "
                        "(POST /api/workspace/{slug}/deploy-key) or save a token for its git provider in Connections → Git"))
                r = push_origin(ws, token=cred.token, ssh_env=cred.ssh_env)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(redact_secrets(exc), subject, body.slug, home.url or "")  # P15
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha}
    @router.post("/api/workspace/status")
    def ws_status(request: Request, body: WorkspaceStatusBody = Body(default=WorkspaceStatusBody())):
        """Look at how a workspace stands against its git repository, without changing the workspace. The platform fetches with the workspace's own credential and moves only its record of the remote, never the files, the index or the local branch, so this is safe to call at any time, including with nobody watching.

        It returns the branch and its upstream, how many commits are ahead (only here: `workspace_push` would send them) and behind (only on the remote: `workspace_pull` would bring them in), the commits on each side with their subjects and file counts, a summary of uncommitted and untracked paths, and when it fetched (and when it last fetched before that). Call it before `workspace_pull` or `workspace_push` and tell the person what it found; do not run git yourself, the workspace's history is the platform's.

        Refusals are typed in `detail.refused`: `no_remote` (attach or publish a repository first), `detached` (no branch checked out), `no_credential` (the repository refused us: add the workspace's deploy key or save a token) and `network` (GitHub could not be reached; `previous_fetch_at` says when it last was)."""
        # NOT `person` in routes.v1.json: it changes nothing a person sees. Any member may look — the
        # same `_manage_dir` resolution the read-only git-remote-status uses; a pull additionally
        # needs contributor because a pull rewrites the tree, and this does not.
        subject = subject_of(request)
        ws = _manage_dir(request, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)

        def _refused(status: int, kind: str, detail: str, **extra):
            return HTTPException(status_code=status, detail={"refused": kind, "detail": detail, **extra})

        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=None) as cred:
                s = fetch_status(ws, token=cred.token, ssh_env=cred.ssh_env,
                                 auth_failure=wcreds.is_auth_failure)
        except ValueError as exc:
            raise _refused(400, "invalid_remote", str(exc))
        except SyncRefused as exc:
            msg = redact_secrets(exc)
            if exc.kind in ("no_remote", "detached"):
                raise _refused(409, exc.kind, msg)
            if exc.kind == "no_credential":
                try:
                    prompt = wcreds.prompt_sentence(wcreds.deploy_key_prompt(
                        wsr.root, key=key, repo_url=home.url or "", subject=str(subject)))
                    msg = f"{msg}\n\n{prompt}"
                except Exception:  # noqa: BLE001 — no ssh-keygen here; the plain sentence stands
                    pass
                raise _refused(424, "no_credential", msg, previous_fetch_at=exc.previous_fetch_at)
            raise _refused(502, "network", msg, previous_fetch_at=exc.previous_fetch_at)
        except RemoteSyncError as exc:
            raise _refused(502, "network", redact_secrets(exc))
        return {
            "remote": s.remote, "url": s.url, "branch": s.branch, "upstream": s.upstream,
            "ahead": s.ahead, "behind": s.behind,
            "local_commits": s.local_commits, "remote_commits": s.remote_commits,
            "uncommitted": s.uncommitted,
            "fetched_at": s.fetched_at, "previous_fetch_at": s.previous_fetch_at,
        }
    @router.post("/api/workspace/pull")
    def ws_pull(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """Fetch + FAST-FORWARD a workspace from its git home. A divergence (local commits the remote
        lacks) is refused — no merge/rebase/force — so it is resolved deliberately. The token (optional for
        public repos) is used for the fetch only and never stored (P15)."""
        # `person` in routes.v1.json: spends the person's git credential
        subject = subject_of(request)
        ws = _manage_dir(request, body.slug)
        # A pull REWRITES the tree, so on a shared workspace it is a write: viewers are refused here even
        # though they may read the same workspace through _manage_dir.
        _require_shared_write(subject, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                r = pull_origin(ws, token=cred.token, ssh_env=cred.ssh_env,  # None ⇒ public-repo fetch
                                merge=body.merge)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(redact_secrets(exc), subject, body.slug, home.url or "")  # P15
        out = {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha,
               "updated": r.updated, "behind_before": r.behind_before, "merged": r.merged}
        if r.conflicts:
            out["conflicts"] = list(r.conflicts)
            out["say"] = ("Not merged — nothing changed. These files were changed on both sides: "
                          + ", ".join(r.conflicts) + ". Ask the person which version to keep.")
        elif r.merged:
            out["say"] = "Merged the repository's changes in; push now to send yours."
        return out
    @router.post("/api/workspace/git-remote-detach")
    def ws_git_remote_detach(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """DETACH a workspace from its git home — the inverse of *Attach existing repo…*, and the
        fourth of the four sync controls the workspace README shows (Vexa-ai/vexa#1623).

        It removes the remote and NOTHING else: no file changes, no commit, no deletion. That is what
        makes it safe to offer beside push and pull — a person who says *stop syncing this to the repository*
        has not said *give me back the tree that was here before it*, and the two acts are only
        confusable if you implement the first as the second.

        The gate is the PULL gate, deliberately, and not a laxer one: a pull rewrites the tree and a
        detach changes where the tree can go, so both are writes to the same workspace plumbing and
        both refuse a viewer. ``_manage_dir`` resolves only the caller's own slots and workspaces
        they belong to; ``_require_shared_write`` then refuses a shared workspace they merely read.

        The RECEIPT is the returned pair plus the log line — the two facts a person needs afterwards
        are *which remote went* and *what its URL was*, and neither survives in git once it is gone."""
        # `person` in routes.v1.json: changes where the tree syncs
        subject = subject_of(request)
        ws = _manage_dir(request, body.slug)
        _require_shared_write(subject, body.slug)
        try:
            gone = detach_home(ws)
        except RemoteSyncError as exc:
            raise HTTPException(status_code=502, detail=redact_secrets(exc))   # P15
        if gone is None:
            return {"detached": False, "remote": None, "url": None,
                    "detail": "this workspace has no git home"}
        remote, url = gone
        logger.warning("workspace detached from its home: subject=%s slug=%s remote=%s",
                       subject, body.slug or "(primary)", remote)
        return {"detached": True, "remote": remote, "url": url}
    @router.get("/api/workspace/purpose")
    def ws_purpose_get(request: Request, slug: Optional[str] = None):
        """Read a workspace's PURPOSE one-liner (default = the caller's primary; ``slug`` = one of their
        own or shared workspaces). ``""`` when unset."""
        ws = _manage_dir(request, slug)
        return {"purpose": read_purpose(ws)}
    @router.post("/api/workspace/purpose")
    def ws_purpose_set(request: Request, body: WorkspacePurposeBody = Body(default=WorkspacePurposeBody())):
        """Set (or clear) a workspace's PURPOSE — stored in the workspace + committed so it travels when
        shared and feeds the mount preamble. Returns the normalized purpose actually stored.

        The purpose enters EVERY member's agent preamble, so on a shared workspace setting it is a
        write that speaks to other people's agents: a viewer may read it and may not set it."""
        ws = _manage_dir(request, body.slug)
        _require_shared_write(subject_of(request), body.slug)
        _hold_private(request, subject_of(request), body.slug, ".vexa/purpose", ws, surface="purpose",
                      content=body.purpose)
        return {"purpose": write_purpose(ws, body.purpose)}
    @router.post("/api/workspace/shared/{workspace_id}/attach")
    def ws_shared_attach(workspace_id: str, request: Request, body: SharedAttachBody = Body(default=SharedAttachBody())):
        """Attach an EXISTING git repo as a shared workspace's tree. Contributor or owner only — a
        viewer may read the group's workspace, never replace it.

        Park-and-clone, exactly as the desk swap: the current tree is kept under the workspace's own
        store and can be swapped back to by slug with no re-clone, so this is reversible. ``policy/``
        (the member list) is carried into the new tree, so an attach can never lock the group out."""
        require_in_ceiling(request, workspace_id)
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
            "parked": result.parked_slug,
            "state": ("cloned" if result.cloned else "restored" if result.swapped else "already attached"),
        }
    @router.get("/api/workspace/shared/{workspace_id}/attached")
    def ws_shared_attached(workspace_id: str, request: Request):
        """A shared workspace's attachment view — the active slug and the parked trees available to swap
        back to, plus its git home. Any member may read it (it says WHAT is mounted, never a credential)."""
        require_in_ceiling(request, workspace_id)
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
        _manage_dir(request, slug)          # authorization: own slot, or a workspace they belong to
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
        _manage_dir(request, slug)
        repo_url = str((body or {}).get("repo") or "")
        try:
            prompt = wcreds.deploy_key_prompt(wsr.root, key=_workspace_key(subject, slug), repo_url=repo_url,
                                              subject=str(subject))
        except deploy_keys_mod.DeployKeyError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"slug": slug, **prompt, "message": wcreds.prompt_sentence(prompt)}
    @router.post("/api/workspace/shared/{workspace_id}/active")
    def ws_shared_active(workspace_id: str, request: Request, body: SharedActiveBody = Body(...)):
        """Switch a SHARED workspace ON/OFF in the caller's active set (mount vs hide). Membership is
        unchanged — this is a per-user mount preference so a member can 'switch it off' without leaving.

        ON needs membership, asked of the authoritative roster (`workspace_membership.is_member`):
        a caller who is not a member is refused, so its active set never names a workspace it cannot
        read. OFF is allowed for anyone — it only hides an id from the caller's own set, which is how
        a client clears a workspace the caller was removed from."""
        require_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        if body.active:
            try:
                membership_mod.require_role(wsr.root, workspace_id, subject, "viewer")
            except MembershipError as exc:
                raise _member_error(exc)
        try:
            set_shared_active(wsr.root, subject, workspace_id, body.active)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"workspace_id": workspace_id, "active": body.active}
    @router.post("/api/workspace/{slug}/archive")
    def ws_archive(slug: str, request: Request, body: ArchiveBody = Body(default=ArchiveBody())):
        """Archive (collapse, keep the data) or un-archive one of the caller's own workspaces."""
        require_in_ceiling(request, slug)
        subject = subject_of(request)
        try:
            set_archived(wsr.root, subject, slug, body.archived)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {"slug": slug, "archived": body.archived}
    @router.delete("/api/workspace/{slug}")
    def ws_delete(slug: str, request: Request,
                  confirm: bool = Query(default=False, description=(
                      "For a SHARED workspace with other members: true only after the person confirmed "
                      "deleting it for the members this route named."))):
        """DELETE a workspace for good — its pages and history are gone. Only when the person has said,
        in this chat, to delete THAT workspace: name it and ask first, never on your own initiative. To
        just get it out of the way, HIDE it instead (`workspace_state_set`, reversible).

        Their own private workspace: deleted. A SHARED one: only its owner may, and it is deleted for
        every member — when others are in it the first call answers 409 naming them; tell the person
        who loses it, and call again with `confirm=true` only after they say yes. Their personal
        workspace can't be deleted (nor hidden, nor shared)."""
        require_in_ceiling(request, slug)
        subject = subject_of(request)
        if slug in ("personal", str(subject)):
            raise HTTPException(status_code=400, detail=PERSONAL_REFUSAL)
        try:
            delete_workspace(wsr.root, subject, slug)
            return {"slug": slug, "deleted": True}
        except ValueError:
            raise HTTPException(status_code=400, detail=PERSONAL_REFUSAL)
        except KeyError:
            pass        # not one of their own: a shared workspace, or nothing
        try:
            role = membership_mod.is_member(wsr.root, slug, subject)
        except MembershipError:
            role = None         # not a workspace id at all: the same answer as one that is gone
        if role is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        if role != "owner":
            raise HTTPException(status_code=403, detail=(
                "only an owner can delete a shared workspace — to stop seeing it, hide it "
                "(workspace_state_set) or leave it"))
        members = membership_mod.read_members(wsr.root, slug)
        others = [m for m in members if str(m.get("subject")) != str(subject)]
        if others and not confirm:
            named = [str(m.get("email") or m.get("name") or m.get("subject")) for m in others]
            raise HTTPException(status_code=409, detail={
                "reason": "members_would_lose_it",
                "members": named,
                "say": (f"Deleting it removes it for {', '.join(named)} too, with all its pages and "
                        "history. Ask the person; call again with confirm=true only after they say yes."),
            })
        try:
            delete_shared_workspace(wsr.root, subject, slug)
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        for m in members:      # the tree is gone: clear the derived index rows, then what points at it
            try:
                mindex.remove(m.get("subject"), slug)
            except Exception:  # noqa: BLE001
                pass
        membership_mod.drop_invites(wsr.root, slug)
        membership_mod.drop_policy_store(wsr.root, slug)   # the roster does not outlive the tree (D5)
        try:
            rec = workspace_registry.by_slug(slug)
            if rec and rec.get("id"):
                workspace_registry.forget(rec["id"])
        except Exception:  # noqa: BLE001 — a stale registry row resolves to `gone`, never to a tree
            logger.warning("registry row for deleted workspace %s not cleared", slug)
        return {"slug": slug, "deleted": True, "shared": True,
                "members_removed": [str(m.get("email") or m.get("subject")) for m in others]}
    @router.post("/api/workspace/reset")
    def ws_reset(request: Request, body: dict = Body(...)):
        """RESET a structural folder to the seed — the delete-equivalent for tiers whose SLOT must
        survive: the caller's `personal` baseline, or `_global` (admin allowlist only). Both are
        "just folders" (founder ruling 2026-08-22) — wipe the content, re-copy the seed, commit.
        `_system` is deliberately NOT resettable: it is identity and settings, not knowledge."""
        subject = subject_of(request)
        target = str(body.get("target") or "")
        require_in_ceiling(request, None if target == "personal" else target)
        if target == "_global":
            if not global_layer.is_admin_account(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only the admin account may reset _global")
            # The `_global` the worker mount serves (`system_mounts.global_root`): unset config is the
            # in-store one (founder ruling 2026-10-08), not "no _global".
            path = system_mounts.global_root(settings, wsr.root)
        elif target == "personal":
            path = Path(wsr.workspace_dir(subject))
        else:
            raise HTTPException(status_code=400, detail="reset targets: personal | _global (shared workspaces are deleted; _system is never touched)")
        if not path.is_dir():
            raise HTTPException(status_code=404, detail="workspace not found")
        seed = resolve_seed_dir(seeds_root=os.environ.get("VEXA_WORKSPACE_SEEDS_DIR"))
        # THE SLOT IS A WORK TREE THE MODEL'S TOOLS CAN WRITE. Clearing it never follows a link: a
        # symlinked child is unlinked as itself (`rmtree` would refuse it, leave it in place, and the
        # seed copy below would then write THROUGH it into wherever it points). The seed goes back in
        # through nofollow writes, so a link planted again in the gap is refused, never followed.
        wpaths.clear_tree_inside(path, keep=(".git",))
        refused = wpaths.copy_tree_inside(seed, path)
        if refused:
            logger.warning("reseed %s: %d seed path(s) refused (a link in the way): %s",
                           target, len(refused), ", ".join(refused[:5]))
        if (path / ".git").is_dir():
            run_git(path, "add", "-A")
            run_git(path, "-c", "user.name=vexa-platform", "-c", "user.email=platform@vexa.local",
                    "commit", "-m", f"reseed {target}")
        return {"target": target, "reset": True}

    # A workspace's image (UI decision 15f) — its own router module, included here so the route
    # table stays one list in api.py.
    from control_plane.routers import workspace_icon as _workspace_icon  # noqa: PLC0415
    router.include_router(_workspace_icon.build(**d))
    return router
