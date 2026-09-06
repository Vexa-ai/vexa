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
from control_plane import git_credentials as git_creds
from control_plane import global_layer
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
    WorkspaceDeactivateBody, WorkspaceNewBody, WorkspacePublishBody, WorkspacePullBody,
    WorkspacePurposeBody, WorkspacePushBody, WorkspaceRenameBody, WorkspaceSwapBody,
    _upload_filename, logger)
from control_plane.workspace_attach import (
    CloneError, activate_workspace, active_workspaces, attach_shared_workspace,
    attached_workspaces, create_shared_workspace_dir, create_workspace,
    deactivate_workspace, delete_workspace, ensure_workspace_private,
    ensure_workspace_shareable, rename_workspace, set_archived, set_shared_active,
    shared_active_mounts, shared_attached_state, swap_workspace, workspace_slot_dir)
from control_plane.workspace_git_sync import (
    RemoteSyncError, pull_origin, push_origin, remote_status)
from control_plane.workspace_membership import MembershipError
from control_plane.workspace_publish import (
    PublishError, RepoExistsError, publish_workspace, published_remote_url)
from control_plane.workspace_purpose import read_purpose, write_purpose
from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from pathlib import Path
from workspaces.shared import entities as entities_mod
from workspaces.shared import workspace_paths as wpaths
from shared.git_redaction import redact as redact_secrets
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from typing import Optional
import hashlib
import os


def build(**d) -> APIRouter:
    """The workspaces routes, bound to one app's dependencies."""
    router = APIRouter()
    _clone_fn = d['_clone_fn']
    _credential_refusal = d['_credential_refusal']
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
        uploads = ws / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
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
            target = (uploads / stored_name).resolve()
            if uploads.resolve() not in target.parents:
                raise HTTPException(status_code=400, detail="invalid filename")
            pending.append((target, content, stored_name, f"uploads/{stored_name}"))
        uploaded: list[dict[str, str]] = []
        for target, content, stored_name, path in pending:
            target.write_bytes(content)
            uploaded.append({"name": stored_name, "path": path})
        return {"files": uploaded}
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
        else:
            if slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
                try:
                    membership_mod.require_role(wsr.root, slug, subject, "contributor")
                except MembershipError:
                    pass  # not shared — fall through; _read_target 403s anything outside the active set
            target = _read_target(request, slug, write=True)
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
                summary=str(body.get("summary") or "").strip(),
                fields=body.get("fields") if isinstance(body.get("fields"), dict) else None,
                section=str(body.get("section") or "").strip(),
                connections=body.get("connections") or (),
                open_questions=body.get("open_questions") or ())
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
        its URL, the branch, and ahead/behind counts vs the last-fetched tracking ref. No token needed."""
        subject = subject_of(request)
        ws = _manage_dir(subject, slug)
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
        try:
            membership_mod.require_role(wsr.root, body.workspace_id, subject, "contributor")
            minted = membership_mod.mint_invite(
                wsr.root, body.workspace_id, role=body.role, created_by=subject,
                expires_in_sec=body.expires_in_sec, max_uses=body.max_uses,
                mode=body.mode, allowed_emails=body.allowed_emails, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        # The client composes the accept URL; we hand back the token + id + terms once.
        return {
            "id": minted.id, "token": minted.token, "role": minted.role,
            "workspace_id": body.workspace_id, "expires_at": minted.expires_at,
            "max_uses": minted.max_uses, "mode": body.mode,
            "accept_path": "/api/workspace/invites/accept",
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
        return {
            "workspace_id": wsid, "name": wsid, "purpose": purpose,
            "role": info["role"], "mode": info["mode"], "expires_at": info["expires_at"],
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
            return {"members": membership_mod.read_members(wsr.root, workspace_id)}
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
