"""routers/sharing.py — Sharing a workspace: make one shareable or private again, create a group,
mint, preview, redeem, list and revoke invites, read the roster and change a member's role, the two
address-based verbs an agent uses (`workspace_invite`, `workspace_membership`), leave, and the
"shared with me" listing.

Moved out of `routers/workspaces.py` byte for byte; `build()` rebinds each dependency to the name the
handlers already used. The access rules themselves are `workspace_membership.py` and
`membership_acts.py`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, Request

from control_plane import front_page as front_page_mod
from control_plane import global_layer
from control_plane import join_links
from control_plane import membership_acts
from control_plane import people_directory
from control_plane import publish as publish_mod
from control_plane import system_mounts
from control_plane import workspace_names as workspace_names_mod
from control_plane import workspace_membership as membership_mod
from control_plane.api_shared import logger
from control_plane.bodies import (
    InviteAcceptBody, InviteCreateBody, InviteLinkCreateBody, InviteLinkRevokeBody,
    JoinLinkCreateBody, JoinLinkRevokeBody, RoleSetBody, SharedNewBody, WorkspaceInviteBody,
    WorkspaceMemberAddBody, WorkspaceMembershipBody)
from control_plane.ceiling import reads_within, require_in_ceiling
from control_plane.workspace_attach import (
    create_shared_workspace_dir, ensure_workspace_private, ensure_workspace_shareable,
    own_private_slot, workspace_slot_dir)
from control_plane.workspace_membership import MembershipError
from control_plane.workspace_purpose import read_purpose


def build(**d) -> APIRouter:
    """The sharing routes, bound to one app's dependencies."""
    router = APIRouter()
    # THE ADDRESS → SUBJECT RESOLVER (Vexa-ai/vexa#1632). Already built for the meeting room, and
    # the same question one door over: does this deployment know this address? Here the answer
    # decides whether an invite link is handed back in the chat or mailed, and a resolver that
    # cannot answer means EXTERNAL — the fail-closed direction, which mails rather than silently
    # handing the agent a link it will tell somebody is already theirs.
    _email_subject_lookup = d['_email_subject_lookup']
    _member_error = d['_member_error']
    _pc = d['_pc']
    _ws_sync = d['_ws_sync']
    mindex = d['mindex']
    settings = d['settings']
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    wsr = d['wsr']
    notifier = d.get('notifier')
    sess = d.get('sess')
    session_store = d.get('session_store')

    def _notify(recipient, *, kind: str, request: Request, actor: str, slug: str, url=None) -> None:
        """One workspace notification (#1783), from the route that did the act. The workspace is
        named by its registry name, as its invite preview names it; nothing else is said."""
        if notifier is None or not recipient:
            return
        try:
            rec = workspace_registry.by_slug(slug) or {}
            notifier.emit([str(recipient)], kind=kind, actor=str(actor),
                          actor_name=people_directory.name_of(wsr.root, str(actor), mindex.list,
                                                              request.headers.get("x-user-email") or ""),
                          object_kind="workspace", object_id=slug,
                          object_name=str(rec.get("name") or slug), url=url)
        except Exception as exc:  # noqa: BLE001 — the act happened; its notice is the extra
            logger.warning("workspace notification %s not sent (%s)", kind, type(exc).__name__)

    def _global_dir() -> Path:
        """Where `_global` is, for the roster's person names."""
        return system_mounts.global_root(settings, wsr.root)

    def _share_own(request: Request, subject: str, slug: str) -> tuple[str, bool]:
        """SHARING STARTS WITH THE FIRST INVITE (Vexa-ai/vexa#1783). A workspace made with
        `workspace_new` is private and has no roster, so an invite to it answered `403 insufficient
        role` — its creator owned it in every sense but the one sharing asks about, and a person who
        said "create a workspace and share it with X" could not be served. When ``slug`` is one of
        the caller's OWN private workspaces (never their personal one), it is made shared here, the
        caller recorded as its owner, exactly as `share-enable` does; anything else is returned
        unchanged for the ordinary owner check. Returns ``(workspace_id, shared_now)``."""
        slot = own_private_slot(wsr.root, subject, slug)
        if slot is None:
            return slug, False
        try:
            wid, promoted = ensure_workspace_shareable(wsr.root, subject, slug)
        except (KeyError, ValueError):
            return slug, False
        if promoted:
            membership_mod.ensure_owner(wsr.root, wid, subject, index=mindex,
                                        email=request.headers.get("x-user-email"), commit_fn=_pc)
            _ws_sync(wid, kind="group", name=(slot.get("name") or "").strip() or None, owner=subject)
            # THE CHATS THAT WORKED IN IT FOLLOW IT (Vexa-ai/vexa#1783): writes go to the shared
            # workspace, and a chat that writes there reads only it — no private workspace.
            if sess is not None:
                try:
                    from control_plane.routers.workspace_states import rehome_shared  # noqa: PLC0415
                    rehome_shared(sess, settings, wsr.root, mindex, workspace_registry, str(subject), slug, wid)
                except Exception:  # noqa: BLE001
                    logger.warning("chats not re-homed after sharing for subject=%s", subject)
        return wid, promoted

    def _private_roster(request: Request, subject: str, slug: str):
        """The roster of one of the caller's own PRIVATE workspaces: them, as owner. True, and what
        an agent needs before it shares one — a 403 here read as "you do not own this"."""
        if own_private_slot(wsr.root, subject, slug) is None:
            return None
        rec = {"subject": str(subject), "role": "owner"}
        if request.headers.get("x-user-email"):
            rec["email"] = request.headers.get("x-user-email")
        return {"members": [rec], "shared": False,
                "note": "private — inviting someone (workspace_invite) shares it, with you as owner"}

    @router.post("/api/workspace/{workspace_id}/unshare")
    def ws_unshare(workspace_id: str, request: Request):
        """UN-SHARE a workspace (owner only) — move it back into the caller's PRIVATE store and drop every
        member's index entry, so it stops being shared (mirror of share-enable). Returns the new private slug."""
        require_in_ceiling(request, workspace_id)
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
        membership_mod.drop_invites(wsr.root, workspace_id)   # …and no pending invite reopens it
        # The tree moved into the caller's private store and stopped being a group. Its id did NOT
        # change — un-sharing is an administrative act, not a new workspace — so every link into it
        # keeps resolving, and for everyone else it now answers `not-yours`, which is the truth: a
        # tree in its owner's private store is theirs alone (`workspace_ids.private_owner`), whatever
        # kind its record carries.
        _ws_sync(new_slug, kind="desk", owner=subject,
                 ws_dir=workspace_slot_dir(wsr.root, subject, new_slug))
        return {"slug": new_slug}
    @router.post("/api/workspace/{slug}/share-enable")
    def ws_share_enable(slug: str, request: Request):
        """Make one of the caller's OWN workspaces shareable (promote a private workspace to a top-level
        shared one if needed) and ensure the caller is its owner. Returns the shareable workspace_id — the
        caller then mints invites against it. This is what lets ANY workspace be shared AFTER creation, with
        no share-vs-not decision at create time."""
        require_in_ceiling(request, slug)
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
        its hash is persisted, in the invite store at <root>/.invites/<workspace_id>.json — the one
        file `invites/preview` and `invites/accept` read (Vexa-ai/vexa#1645)."""
        require_in_ceiling(request, body.workspace_id)
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
        if not info.get("valid"):
            # Revoked, expired or used up: the reason only, so the page can say which.
            return {"valid": False, "reason": info.get("reason")}
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
        # recognises — *"Dmitry invited you to Example Bank as a contributor"* — and `bank-a1b2c3` is not a
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
    # ACCEPT AND DECLINE ARE ONE ACT EACH, reached from two doors (#1783): the join page and the
    # invite's own notification (`POST /api/notifications/act`, the `notifications_act` tool). Both
    # call these, so the gate, the redeem and the `invite_accepted` / `invite_declined` notice to the
    # inviter have one writer. `ceiling=False` is the notification's door: joining is the person's
    # own act on their own invite, asked for in a watched turn, and reads nothing in this dispatch.
    def accept_token(request: Request, token: str, *, ceiling: bool = True) -> dict:
        subject = subject_of(request)
        # SECURITY BOUNDARY: X-User-Email is the caller's VERIFIED email because agent-api's identity
        # door (gateway-identity.v1, see the TOPOLOGY BOUNDARY note in create_app) rebuilds every x-user-*
        # header from the gateway's signature — the address identity resolved — or believes it from
        # the internal tier only. Restricted-mode invites rest on that.
        subject_email = request.headers.get("x-user-email")
        # Resolve which shared workspace this token belongs to by hash (never trust a client-declared
        # id) — through `find_invite`, the SAME resolver `invites/preview` uses.
        found = membership_mod.find_invite(wsr.root, token)
        if found is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        target_ws = found[0]
        # The token names its workspace; a delegated dispatch joins only one inside its ceiling.
        if ceiling:
            require_in_ceiling(request, target_ws)
        try:
            result = membership_mod.accept_invite(
                wsr.root, target_ws, token=token, subject=subject, subject_email=subject_email,
                index=mindex, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        if not result.get("already_member"):
            # A LINK (an open invite) tells its maker who came in through it (#1783):
            # `workspace_joined_via_link` in the app, and the `workspace.joined_via_link` fact for
            # the mail kind the company switches on.
            via_link = found[1].get("mode") == "open"
            _notify(found[1].get("created_by"), kind="workspace_joined_via_link" if via_link else "invite_accepted",
                    request=request, actor=subject, slug=target_ws)
            if via_link:
                _joined_fact(subject, subject_email, target_ws, found[1])
        # SHARED WORK, BOUND TO THIS WORKSPACE (Vexa-ai/vexa#1783): the person's next new chat starts
        # in the "shared work" profile with {{workspace}} = this workspace — a profile, not a rule.
        try:
            from control_plane import workspace_attach as _attach  # noqa: PLC0415
            _attach.queue_profile(wsr.root, subject, _attach.SHARED_WORK, {_attach.WORKSPACE_VAR: target_ws})
        except Exception:  # noqa: BLE001 — the join happened; the profile is the extra
            logger.warning("shared work not queued for subject=%s", subject)
        rec = workspace_registry.by_slug(target_ws) or {}
        return {**result, "name": rec.get("name") or target_ws}

    def _joined_fact(subject: str, email, slug: str, rec: dict) -> None:
        """Hand `workspace.joined_via_link` to flows (never raises; the join already happened)."""
        try:
            ws = workspace_registry.by_slug(slug) or {}
            publish_mod.publish_joined_via_link({
                "uid": str(rec.get("created_by") or ""), "joiner": str(subject),
                "joiner_name": people_directory.name_of(wsr.root, str(subject), mindex.list, email or ""),
                "joiner_email": str(email or ""), "workspace": slug,
                "workspace_name": str(ws.get("name") or slug), "link_id": str(rec.get("id") or ""),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace.joined_via_link not published (%s)", type(exc).__name__)

    def decline_token(request: Request, token: str) -> dict:
        subject = subject_of(request)
        email = str(request.headers.get("x-user-email") or "").strip().lower()
        found = membership_mod.find_invite(wsr.root, token)
        if found is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        target_ws, rec = found
        allowed = {str(e).strip().lower() for e in (rec.get("allowed_emails") or [])}
        if rec.get("mode") != "restricted" or not email or email not in allowed:
            raise HTTPException(status_code=404, detail="invalid invite")
        try:
            if not rec.get("revoked"):
                membership_mod.revoke_invite(wsr.root, target_ws, str(rec.get("id")), commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        _notify(rec.get("created_by"), kind="invite_declined", request=request, actor=subject, slug=target_ws)
        rec2 = workspace_registry.by_slug(target_ws) or {}
        return {"declined": True, "workspace_id": target_ws, "name": rec2.get("name") or target_ws}

    invite_doors = d.get("invite_doors")
    if isinstance(invite_doors, dict):
        invite_doors.update(accept=accept_token, decline=decline_token)

    @router.post("/api/workspace/invites/accept")
    def ws_invite_accept(request: Request, body: InviteAcceptBody = Body(...)):
        """Redeem an invite token (any logged-in user) → membership in BOTH stores, use-count bumped.
        Idempotent per user (accepting twice = one membership, no extra use consumed). The token carries
        NO workspace id — we resolve it by scanning the shareable workspaces' invites for its hash.
        Post-auth redeem (AMENDMENT 5): the caller is an already-authenticated user (X-User-Id); a
        RESTRICTED invite additionally requires their VERIFIED email (X-User-Email, gateway-injected)
        to be in the invite's allowed_emails."""
        return accept_token(request, body.token)

    @router.post("/api/workspace/invites/decline")
    def ws_invite_decline(request: Request, body: InviteAcceptBody = Body(...)):
        """Decline an invite made for you (#1783): the invite is revoked, so the link stops working,
        and whoever made it is told. Only an invite RESTRICTED to the caller's verified address can
        be declined by them — an open link belongs to nobody in particular, so it is simply not
        used. Any other token is answered like one that matches nothing."""
        return decline_token(request, body.token)
    @router.delete("/api/workspace/invites/{invite_id}")
    def ws_invite_revoke(invite_id: str, request: Request, workspace_id: str):
        """Revoke an invite (owner/contributor of the workspace)."""
        require_in_ceiling(request, workspace_id)
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
        require_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            return {"invites": membership_mod.list_invites(wsr.root, workspace_id)}
        except MembershipError as exc:
            raise _member_error(exc)
    @router.get("/api/workspace/members")
    def ws_members_list(request: Request, workspace_id: str = Query(
            ..., description="the shared workspace's slug")):
        """List a workspace's members and what each one is — owner, contributor or viewer (shown to
        a person as reader). Readable by a contributor or owner. For one of the caller's own PRIVATE
        workspaces the roster is the caller alone, as owner, with `shared: false`. Opportunistically records the
        CALLER's own verified email onto their member row (self-healing for members granted before
        emails were stored) so the roster shows human labels, not opaque subject ids."""
        require_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        private = _private_roster(request, subject, workspace_id)
        if private is not None:
            return private
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
                                                   email=m.get("email"), global_dir=_global_dir())
                rows.append({**m, "name": named} if named else dict(m))
            return {"members": rows}
        except MembershipError as exc:
            raise _member_error(exc)
    @router.delete("/api/workspace/members/{member_subject}")
    def ws_member_remove(member_subject: str, request: Request, workspace_id: str):
        """Remove a member (owner only)."""
        require_in_ceiling(request, workspace_id)
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
        require_in_ceiling(request, workspace_id)
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
        behind `workspace_invite`. `slug` may be one of the caller's own PRIVATE workspaces (the slug
        `workspace_new` answered): the invite then makes it shared, with the caller as owner, and the
        answer's `workspace_id` (with `shared_now: true`) is the shared workspace's slug from then on —
        use it for `workspace_members` and further invites. The personal workspace is never shared.

        Owner-only, and the check is the same `require_role(..., "owner")` the role and remove routes
        run. `_system` is refused for everybody; `_global` is admin-only and then refused, because the
        company layer's editors are a named set in `POLICIES.md` and a membership record there would
        authorise nothing. The invite is the one `POST /api/workspace/invites` mints — same store,
        same hash-only persistence — minted `restricted` to the named address, so a forwarded link
        grants nobody anything.

        Where the link GOES is the question this route exists to answer: an address this instance
        already knows gets it handed back for the agent to give them in the chat they are in, and
        every other address is published to the mail carrier. The answer says which happened."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            slug, shared_now = _share_own(request, subject, body.slug)
            membership_acts.assert_may_manage(
                wsr.root, slug, subject,
                is_admin=bool(global_layer.is_admin_account(settings, str(subject))))
            rec = workspace_registry.by_slug(slug) or {}
            answer = membership_acts.invite(
                wsr.root, slug, email=body.email, role=body.role, inviter=subject,
                inviter_email=request.headers.get("x-user-email") or "",
                workspace_name=str(rec.get("name") or ""),
                index=mindex, ui_url=(settings.ui_url if settings is not None else ""),
                commit_fn=_act_commit(request, subject),
                resolve_subject=_email_subject_lookup,
                mail=publish_mod.publish_invite, mail_delivery=publish_mod.mail_delivery)
            answer = {**answer, "workspace_id": slug, "shared_now": shared_now}
            if shared_now:
                answer["said"] = (f"{body.slug} is now a shared workspace, addressed as {slug}, with "
                                  f"you as owner. " + str(answer.get("said") or "")).strip()
            done = answer
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)
        if done.get("invited") and done.get("internal"):
            # AN ADDRESS WITH AN ACCOUNT HERE IS TOLD IN THE APP (#1783), with the link to open. The
            # link is restricted to that verified address, so the row grants nobody else anything.
            try:
                invitee = _email_subject_lookup(str(done.get("email") or "")) if _email_subject_lookup else None
            except Exception:  # noqa: BLE001
                invitee = None
            _notify(invitee, kind="workspace_invited", request=request, actor=subject,
                    slug=str(done.get("workspace_id") or body.slug),
                    url=done.get("link"))
        return done

    @router.post("/api/workspace/membership")
    def ws_membership_set(request: Request, body: WorkspaceMembershipBody = Body(...)):
        """Change what an address IS in a workspace, or take them off it — the verb behind
        `workspace_membership`. `role` is one of the three, or `remove`.

        Same gate as the invite above, and one verb rather than two because it is one question with
        four answers: an agent that had to choose a verb before asking would have to guess the answer
        first. The last-owner refusal reaches the person as itself (409) — it is about the workspace,
        not about them, and a generic failure would leave somebody trying it again."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            membership_acts.assert_may_manage(
                wsr.root, body.slug, subject,
                is_admin=bool(global_layer.is_admin_account(settings, str(subject))))
            return membership_acts.set_membership(
                wsr.root, body.slug, email=body.email, role=body.role, actor=subject,
                index=mindex, commit_fn=_act_commit(request, subject),
                resolve_subject=_email_subject_lookup)
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)

    def _workspace_named(subject: str, said: str) -> str:
        """The slug of the caller's own workspace that ``said`` names — its slug, or its name in any
        case. Refused when none or more than one answers: a name is never guessed."""
        want = str(said or "").strip()
        if want in membership_acts.SYSTEM_SLUGS or want in membership_acts.GLOBAL_SLUGS:
            return want
        rows, _ = membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        slugs = [str(r.get("workspace_id") or "") for r in rows if r.get("workspace_id")]
        if want in slugs:
            return want
        low = want.lower()
        hits = []
        for slug in slugs:
            rec = workspace_registry.by_slug(slug) or {}
            if str(rec.get("name") or "").strip().lower() == low or slug.lower() == low:
                hits.append(slug)
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise HTTPException(status_code=404, detail=f"none of your workspaces is called {want!r}")
        raise HTTPException(status_code=409, detail=f"{len(hits)} of your workspaces are called "
                                                    f"{want!r} — name it by its slug: " + ", ".join(hits))

    @router.post("/api/workspace/members/add")
    def ws_member_add(request: Request, body: WorkspaceMemberAddBody = Body(...)):
        """Give someone who already has an account read or write access to a workspace — the verb
        behind `workspace_member_add`. Only the workspace's owner adds (the same gate as invite and
        membership); `_system` is nobody's to share and `_global` is the administrator's file.

        ``person`` is the account id an @-mention in the chat carries — one of the people the caller
        already shares a workspace with — or an email with an account here. Nobody new is invited
        by this verb: a person with no account is refused with a pointer to `workspace_invite`."""
        subject = subject_of(request)
        slug = _workspace_named(subject, body.workspace)
        require_in_ceiling(request, slug)
        try:
            membership_acts.assert_may_manage(
                wsr.root, slug, subject,
                is_admin=bool(global_layer.is_admin(settings, str(subject))))
            done = membership_acts.add_member(
                wsr.root, slug, person=body.person, role=body.role, actor=subject, index=mindex,
                commit_fn=_act_commit(request, subject), resolve_subject=_email_subject_lookup,
                known_people=lambda: people_directory.mentionable(wsr.root, subject, mindex.list))
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)
        if done.get("added"):
            _notify(done.get("subject"), kind="workspace_added", request=request, actor=subject, slug=slug)
        return done

    # ── LINKS (#1783): a workspace link N people can join by; chats get the same mechanism ───────
    #
    # Founder, 2026-10-14: the host asks their agent for a link, puts it in the calendar event, and
    # whoever opens it joins. The terms (uses, expiry, revocation, hash-only storage) are
    # `join_links`; who may mint is the same gate as inviting a person (owner, or the workspace
    # made shared on the spot when it is the caller's own private one).

    _LINK_ROLES = {"member": "contributor", "write": "contributor", "contributor": "contributor",
                   "reader": "viewer", "read": "viewer", "viewer": "viewer"}

    def _link_error(exc: join_links.LinkError):
        return HTTPException(status_code=exc.status, detail=str(exc))

    def _days(d):
        return None if d is None else int(d) * 86400

    @router.post("/api/workspace/invite-link", status_code=201)
    def ws_invite_link_create(request: Request, body: InviteLinkCreateBody = Body(...)):
        """Create a link that lets up to `max_uses` people join a workspace (default 10, for 7 days),
        when the person asks for one — e.g. to put in a calendar invite. Whoever opens it signs in if
        they must and joins as `role` (contributor = read and write, or reader); a person already in
        it spends no use. `slug` may be one of the person's own private workspaces: it becomes shared,
        with them as owner (Personal is never shared). The answer's `url` is shown ONCE — give it to
        the person; it cannot be read back later. List links with workspace_invite_links and stop one
        with workspace_invite_link_revoke. Each time someone joins, the person is notified."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        role = _LINK_ROLES.get(str(body.role or "").strip().lower())
        if role is None:
            raise HTTPException(status_code=400, detail="a link gives contributor (read and write) or reader")
        try:
            slug, shared_now = _share_own(request, subject, body.slug)
            membership_acts.assert_may_manage(
                wsr.root, slug, subject,
                is_admin=bool(global_layer.is_admin_account(settings, str(subject))))
            minted = join_links.mint(wsr.root, "workspace", slug, role=role, created_by=subject,
                                     max_uses=body.max_uses, expires_in_sec=_days(body.expires_in_days))
        except membership_acts.ActRefused as exc:
            raise _act_refusal(exc)
        except MembershipError as exc:
            raise _member_error(exc)
        except join_links.LinkError as exc:
            raise _link_error(exc)
        url = join_links.link_url(settings.ui_url if settings is not None else "", minted.token)
        rec = workspace_registry.by_slug(slug) or {}
        name = str(rec.get("name") or slug)
        return {
            "id": minted.id, "workspace_id": slug, "name": name, "role": membership_mod.role_word(minted.role),
            "max_uses": minted.max_uses, "expires_at": minted.expires_at, "shared_now": shared_now,
            "url": url or None,
            "url_refused": None if url else
                "VEXA_UI_URL is not set on agent-api — this deployment has not declared where its "
                "terminal is, so there is no link to give anyone",
            "said": (f"Link to {name}: up to {minted.max_uses} people can join as "
                     f"{membership_mod.role_word(minted.role)} until it expires or you revoke it."),
        }

    @router.get("/api/workspace/invite-links")
    def ws_invite_links(request: Request, slug: str = Query(
            ..., description="the workspace, by its slug or its name")):
        """A workspace's join links: id, role, uses so far of max_uses, expiry, and state (live,
        used_up, expired, revoked). The URLs themselves are never stored, so they are not listed."""
        require_in_ceiling(request, slug)
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, slug, subject, "contributor")
            return {"workspace_id": slug, "links": join_links.listing(wsr.root, "workspace", slug)}
        except MembershipError as exc:
            raise _member_error(exc)

    @router.post("/api/workspace/invite-link/revoke")
    def ws_invite_link_revoke(request: Request, body: InviteLinkRevokeBody = Body(...)):
        """Stop one of a workspace's links working, when the person asks. People who already joined
        stay members (remove them with workspace_membership)."""
        require_in_ceiling(request, body.slug)
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, body.slug, subject, "contributor")
            join_links.revoke(wsr.root, "workspace", body.slug, body.link_id)
        except MembershipError as exc:
            raise _member_error(exc)
        except join_links.LinkError as exc:
            raise _link_error(exc)
        return {"revoked": True, "id": body.link_id, "workspace_id": body.slug}

    # The generic door: a CHAT link is minted, listed and revoked here; any link is previewed and
    # redeemed here. What a chat is, and who may link it, the chat domain says (`register_chat_target`).
    @router.post("/api/join-links", status_code=201)
    def join_link_create(request: Request, body: JoinLinkCreateBody = Body(...)):
        """Create a link that lets up to `max_uses` people join a chat."""
        subject = subject_of(request)
        if body.target_kind != "chat":
            raise HTTPException(status_code=400, detail="workspace links: POST /api/workspace/invite-link")
        target = join_links.chat_target()
        if target is None:
            raise HTTPException(status_code=501, detail="chat links are not available on this deployment yet")
        if not target.may_link(subject, body.target_id):
            raise HTTPException(status_code=404, detail="no such chat of yours")
        try:
            minted = join_links.mint(wsr.root, "chat", body.target_id, role=body.role, created_by=subject,
                                     max_uses=body.max_uses, expires_in_sec=_days(body.expires_in_days))
        except join_links.LinkError as exc:
            raise _link_error(exc)
        url = join_links.link_url(settings.ui_url if settings is not None else "", minted.token)
        return {"id": minted.id, "target_kind": "chat", "target_id": body.target_id, "role": minted.role,
                "max_uses": minted.max_uses, "expires_at": minted.expires_at, "url": url or None}

    @router.get("/api/join-links")
    def join_links_list(request: Request, target_kind: str, target_id: str):
        """A chat's links (no URLs; they are shown once)."""
        subject = subject_of(request)
        target = join_links.chat_target()
        if target_kind != "chat" or target is None or not target.may_link(subject, target_id):
            raise HTTPException(status_code=404, detail="no such chat of yours")
        return {"links": join_links.listing(wsr.root, "chat", target_id)}

    @router.post("/api/join-links/revoke")
    def join_link_revoke(request: Request, body: JoinLinkRevokeBody = Body(...)):
        subject = subject_of(request)
        target = join_links.chat_target()
        if body.target_kind != "chat" or target is None or not target.may_link(subject, body.target_id):
            raise HTTPException(status_code=404, detail="no such chat of yours")
        try:
            join_links.revoke(wsr.root, "chat", body.target_id, body.link_id)
        except join_links.LinkError as exc:
            raise _link_error(exc)
        return {"revoked": True, "id": body.link_id}

    @router.get("/api/join-links/preview")
    def join_link_preview(request: Request, token: str):
        """What a link opens, before anything is granted: `target_kind` (workspace · chat), its
        name, the role, and whether it still works (`reason`: revoked · expired · used_up). Gated by
        the token alone; 404 for a token that matches nothing. Also how a sensor resolves a link it
        found in a calendar event to its workspace."""
        found = join_links.find(wsr.root, token)
        if found is None:
            raise HTTPException(status_code=404, detail="invalid link")
        kind, target_id, rec = found
        reason = join_links.why_not(rec)
        if reason is not None:
            return {"valid": False, "reason": reason, "target_kind": kind}
        if kind == "workspace":
            ws = workspace_registry.by_slug(target_id) or {}
            name = str(ws.get("name") or target_id)
            extra = {"workspace_id": target_id, "id": ws.get("id")}
        else:
            target = join_links.chat_target()
            try:
                name = str(target.title(target_id)) if target else ""
            except Exception:  # noqa: BLE001
                name = ""
            extra = {"chat_id": target_id}
        return {"valid": True, "reason": None, "target_kind": kind, "name": name or "untitled",
                "role": rec.get("role"), "mode": rec.get("mode", "open"), "expires_at": rec.get("expires_at"),
                **extra}

    @router.post("/api/join-links/redeem")
    def join_link_redeem(request: Request, body: InviteAcceptBody = Body(...)):
        """Join whatever a link opens, as the signed-in person. Idempotent: a person already in spends
        no use. Refused calmly when the link is revoked, expired or used up (410)."""
        subject = subject_of(request)
        email = request.headers.get("x-user-email")
        found = join_links.find(wsr.root, body.token)
        if found is None:
            raise HTTPException(status_code=404, detail="invalid link")
        kind, target_id, rec = found
        if kind == "workspace":
            return {"target_kind": "workspace", **accept_token(request, body.token)}
        try:
            out = join_links.redeem_chat(wsr.root, target_id, body.token, subject=subject, email=email or "")
        except join_links.LinkError as exc:
            raise _link_error(exc)
        _bind_to_chats_workspace(subject, target_id)
        return {"target_kind": "chat", **out}

    def _bind_to_chats_workspace(subject: str, chat_id: str) -> None:
        """A chat opened from its link writes, for the newcomer, where it writes for its owner:
        "shared work" bound to that workspace (Vexa-ai/vexa#1783). Nothing when it writes to the
        owner's own desk — that is not a place the newcomer has."""
        try:
            row = session_store.get(chat_id) if session_store is not None else None
            if row is None or sess is None:
                return
            from control_plane.routers.workspace_states import bind_shared_work_for  # noqa: PLC0415
            wid = sess.target(str(row.get("owner") or ""), str(row.get("name") or ""))
            bind_shared_work_for(sess, settings, wsr.root, mindex, workspace_registry, subject, chat_id, wid)
        except Exception:  # noqa: BLE001
            logger.warning("chat-link binding skipped for subject=%s", subject)

    @router.post("/api/workspace/{workspace_id}/leave")
    def ws_member_leave(workspace_id: str, request: Request):
        """LEAVE a shared workspace — the caller removes THEMSELVES (any role; no owner gate). The
        last-owner guard still applies: a sole creator must unshare or hand off ownership rather than
        orphan the workspace, so their leave is refused (409) with that message."""
        require_in_ceiling(request, workspace_id)
        subject = subject_of(request)
        if membership_mod.is_member(wsr.root, workspace_id, subject) is None:
            raise HTTPException(status_code=404, detail="not a member of this workspace")
        try:
            membership_mod.remove_member(wsr.root, workspace_id, subject, index=mindex, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "left": workspace_id}
    @router.get("/api/workspace/shared")
    def ws_shared_list(request: Request,
                       hidden: bool = Query(default=False,
                                            description="Include workspaces the person hid.")):
        """EVERY workspace the person has, under `all`: `slug`, the `name` the Files pane shows,
        `kind` (`personal` · `private` · `shared`), `role`, and for a private one `empty` and any
        `same_name_as` — call a workspace by that name when talking to the person (their own one is
        "Personal", never its slug). Every workspace verb accepts the name as well as the slug; two
        workspaces answering one name are refused with both, so ask which. When two of their own
        share a name and one is `empty`, offer to delete the empty one (`workspace_remove`, after
        they say yes). `memberships` is the "workspaces shared with me" listing, reconciled across
        BOTH membership stores. Each row carries its `state` (`active` · `inactive` · `hidden`);
        workspaces the person HID are left out unless `hidden=true` — do not mention them unless
        asked (Vexa-ai/vexa#1783).

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
        # A delegated dispatch lists only the workspaces inside its ceiling (`ceiling.reads_within`);
        # a person with no delegation sees every membership, as before.
        rows = [r for r in rows if reads_within(request, str(r.get("workspace_id") or ""))]
        # EVERY WORKSPACE, BY THE NAME THE PERSON SEES (Vexa-ai/vexa#1783): their personal one, their
        # own private ones and the shared ones, as the Files pane lists them — so an agent asked about
        # "Eugenia - Dmitry" finds it, and calls the personal workspace "Personal", not its slug.
        everything = [r for r in workspace_names_mod.listing(
            wsr.root, str(subject), registry=workspace_registry, index_list=mindex.list,
            with_emptiness=True) if r["kind"] != "shared" or reads_within(request, r["slug"])]
        if not hidden:
            # HIDDEN IS NOT LISTED (Vexa-ai/vexa#1783): not in Files, not in a picker, not in the
            # agent's listing. The rows stay in `workspace_names.listing` so the name still resolves.
            gone = {r["slug"] for r in everything if r.get("state") == "hidden"}
            everything = [r for r in everything if r["slug"] not in gone]
            rows = [r for r in rows if str(r.get("workspace_id") or "") not in gone]
        return {"memberships": rows, "index_degraded": degraded, "all": everything}

    return router
