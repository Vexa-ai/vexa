"""routers/scaffolds.py — One record per arrival (PRD §5.5) — mint, read, redeem the share, and the two reads a
panel does around it: resolve a page's links, record that a page was opened.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import link_resolver as link_resolver_mod
from control_plane import scaffolds as scaffolds_mod
from control_plane import system_mounts
from control_plane import workspace_ids as ids_mod
from control_plane.api_shared import ScaffoldHandBody, ScaffoldMintBody, logger
from fastapi import APIRouter, Body, HTTPException, Request
from workspaces.shared import workspace_paths as wpaths


def build(**d) -> APIRouter:
    """The scaffolds routes, bound to one app's dependencies."""
    router = APIRouter()
    _compose_and_mint = d['_compose_and_mint']
    _email_subject_lookup = d['_email_subject_lookup']
    _global_root = d['_global_root']
    _internal_caller = d['_internal_caller']
    _meeting_owner_lookup = d['_meeting_owner_lookup']
    _scaffold_is_for = d['_scaffold_is_for']
    _scaffold_recipient_is = d['_scaffold_recipient_is']
    _scaffold_view = d['_scaffold_view']
    _ws_here = d['_ws_here']
    _ws_is_member = d['_ws_is_member']
    _ws_sync = d['_ws_sync']
    scaffolds = d['scaffolds']
    settings = d['settings']
    subject_of = d['subject_of']
    workspace_registry = d['workspace_registry']
    workspace_touches = d['workspace_touches']
    wsr = d['wsr']

    @router.post("/internal/scaffolds", status_code=201)
    def mint_scaffold(body: ScaffoldMintBody, request: Request):
        """Mint one scaffold and return `{id, url}` — the INTERNAL tier only (flows and the rig).

        Gated on `X-Internal-Secret`, the same edge the meeting room uses, for the same reason: a
        scaffold names the workspaces a chat will mount and the opening its agent will answer, so a
        caller who could mint one for somebody else's address could compose that person's first
        turn. A browser client through the gateway holds no such secret and cannot mint at all.

        EVERYTHING THAT CAN BE WRONG IS WRONG HERE, NOT AT THE CLICK. The preset must exist and be
        non-empty, the kind must be in the catalogue, the terminal's origin must be configured. A
        step calls this BEFORE it sends, so a failure here stops the send — which is the share-gate
        doctrine one layer up (`email_attendees` refuses to mail a link it cannot make work). A
        record that mints happily and opens onto nothing is the exact failure this route exists to
        make impossible.

        THE SHARE TOKEN IS MINTED BY THE CALLER, not here, and that is a deliberate boundary. The
        restricted grant is `POST /meetings/{id}/share` on meeting-api as the meeting's OWNER, which
        needs that owner's gateway key; flows already holds the credential that can produce one
        (`flows_steps/meeting.mint_transcript_share`) and agent-api holds no gateway key by design.
        Giving agent-api one so this route could mint the share would put a key that can act as any
        user into a service whose whole job is to read workspaces. The caller mints, and passes the
        token here; this route composes it into the url so the LINK is still built in one place."""
        if not _internal_caller(request):
            raise HTTPException(status_code=403, detail="minting a scaffold is an internal-tier capability")
        who = str(body.who or "").strip()
        if not who or "@" not in who:
            raise HTTPException(status_code=400, detail="`who` must be the recipient's address")
        if body.kind not in scaffolds_mod.KINDS:
            raise HTTPException(status_code=400,
                                detail=f"unknown scaffold kind {body.kind!r} — the catalogue is "
                                       f"{', '.join(scaffolds_mod.KINDS)}")
        ui = (settings.ui_url if settings is not None else "").rstrip("/")
        if not ui:
            raise HTTPException(status_code=503,
                                detail="VEXA_UI_URL is not set on agent-api — a scaffold url would "
                                       "have no origin, and a link nobody can open is not a touch")
        try:
            fm, _body_text = scaffolds_mod.read_preset(_global_root(), str(body.opening or ""))
        except scaffolds_mod.ScaffoldError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        subject = _email_subject_lookup(who.lower()) or ""
        row = None
        mid = str(body.meeting or "").strip()
        minted_by = str((body.provenance or {}).get("minted_by") or "")
        if mid.isdigit():
            for reader_uid in (subject, minted_by):
                if reader_uid:
                    row = _meeting_owner_lookup(reader_uid, mid)
                    if isinstance(row, dict):
                        break
        group = scaffolds_mod.group_workspace_of(row)

        # THE MOUNT SET, STATED. `_global` always (it is the company layer and every worker carries
        # it), the recipient's own desk when they have one, the group desk when the meeting is bound
        # to one. A caller may override the whole list; it may not omit `_global`.
        if body.workspaces is not None:
            mounts = [w for w in body.workspaces if str(w).strip()]
        else:
            mounts = [subject] if subject else []
            if group and group not in mounts:
                mounts.append(group)
        mounts = [system_mounts.GLOBAL_SLUG] + [m for m in mounts if m != system_mounts.GLOBAL_SLUG]

        # ONE FIRST VISIT PER PERSON, NOT ONE PER SIGN-IN.
        #
        # Every other kind is minted by a flow at the moment it creates a touch, so a second mint is
        # a second touch and deserves its own record. `first-visit` is the opposite: it is minted
        # because nobody sent anything, on every sign-in that arrives without a link. Minting a
        # fresh record each time would give the terminal a new `scaffold-<id>` chat id each time,
        # and therefore a new rail row — a fourth sign-in showing four rows the person never made,
        # which is precisely the defect F34/F35 exist to remove. Caught in review by the terminal
        # worker before it shipped.
        #
        # So the record is reused while it is still PENDING. Once redeemed the person has actually
        # been through it, and a later sign-in is a genuinely new arrival that may hold new facts —
        # a workspace shared with them since, a meeting they have been invited to.
        if body.kind == "first-visit":
            existing = next((r for r in scaffolds.for_recipient(who, pending_only=True)
                             if r.get("kind") == "first-visit"), None)
            if existing:
                logger.info("scaffold REUSED id=%s kind=first-visit who=%s (unredeemed)",
                            existing["id"], who)
                return {"id": existing["id"], "url": f"{ui}/?s={existing['id']}"}

        rec = _compose_and_mint(
            who=who, subject=subject, kind=body.kind, opening=str(body.opening), fm=fm,
            mid=mid, mounts=mounts, group=group, refs_in=body.refs,
            tabs=body.tabs, focus=body.focus, provenance=body.provenance,
            share_token=body.share_token,
        )
        # THE LINK IS AN ID. Nothing else: no preset name, no mount list, no prompt text — and since
        # R-A08, no capability either. The share the caller minted is stored ON the record and the
        # recipient redeems it against this id (`POST /api/scaffolds/<id>/share`), because a bearer
        # token in a query string leaks into every access log and proxy trace it passes through —
        # the rule `worker/engine.py` states for the delegation token, applied to the artefact that
        # actually crosses a mail provider.
        url = f"{ui}/?s={rec['id']}"
        logger.info("scaffold MINTED id=%s kind=%s who=%s meeting=%s mounts=%s opening=%s share=%s",
                    rec["id"], rec["kind"], who, rec.get("meeting"), mounts, rec["opening"],
                    bool(body.share_token))
        return {"id": rec["id"], "url": url}
    @router.post("/api/scaffolds/hand", status_code=201)
    def mint_hand_scaffold(body: ScaffoldHandBody, request: Request):
        """A hand link becomes a record, minted FOR THE CALLER, and the client composes nothing.

        `/?ask=<preset>&meeting=<row>` is the one composition path the scaffold work left standing,
        and it was still being composed in the browser: the terminal read the preset and substituted
        `?meeting=` and `?ws=` into the opening text. A URL that reaches the model is the thing
        decision 13 forbids — the URL carries NAMES, never prompt text — and the `?s=` path obeyed
        it while this one did not. So the hand link mints here and redirects to `/?s=<id>`, and the
        substitution happens where every other scaffold's does: server-side, at turn time, out of
        the RECORD (`scaffolds.turn_prompt`), never out of the address bar.

        THREE THINGS THIS ROUTE REFUSES, and each is the whole point of it existing:

          · a preset that is not a NAME in `_global/asks/` — `read_preset` decides, exactly as the
            internal mint does, so there is one definition of what an opening may be;
          · a `who` other than the caller — there is no such field. The recipient is the session's
            own subject and address, so a link cannot mint a first turn for somebody else;
          · a meeting the caller cannot see. `_meeting_owner_lookup` is owner-only and fail-closed,
            so a crafted `?meeting=<someone else's row>` mints NOTHING rather than a record carrying
            another meeting's title and attendees into this person's opening.

        The last one is the subtle half. Even with no text in the URL, an unchecked row id would let
        a crafted link decide WHICH FACTS the agent is handed — which is the same attack one level
        down. A hand link may name only a meeting you own; an attendee reaches theirs through the
        emailed scaffold, which carries its own share."""
        subject = subject_of(request)
        if not subject:
            raise HTTPException(status_code=401, detail="sign in first")
        # The caller's ADDRESS, gateway-injected and trusted — the same header the chat turn already
        # attributes commits by, and the gateway strips any client-supplied spelling of it before it
        # gets here (see the TOPOLOGY BOUNDARY note). A record is bound to an address, so a session
        # that carries none cannot mint: refusing is honest, guessing one would bind the record to a
        # person who may not be the caller.
        who = (request.headers.get("x-user-email") or "").strip()
        if not who or "@" not in who:
            raise HTTPException(status_code=409,
                                detail="this session carries no address, and a scaffold is bound to "
                                       "one — sign in again")
        preset = str(body.preset or "").strip()
        if not scaffolds_mod.NAME_RE.match(preset):
            raise HTTPException(status_code=400, detail="`preset` must be a preset name")
        try:
            fm, _text = scaffolds_mod.read_preset(_global_root(), preset)
        except scaffolds_mod.ScaffoldError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        ui = (settings.ui_url if settings is not None else "").rstrip("/")
        if not ui:
            raise HTTPException(status_code=503, detail="VEXA_UI_URL is not set on agent-api")

        mid = str(body.meeting or "").strip()
        row = None
        if mid:
            row = _meeting_owner_lookup(subject, mid) if mid.isdigit() else None
            if not isinstance(row, dict):
                # fail CLOSED and say so: a link naming a meeting this person cannot open is a
                # broken link, and minting a record without it would answer about the wrong thing.
                raise HTTPException(status_code=404,
                                    detail="no such meeting for this account — a hand link may only "
                                           "name a meeting you can open")
        group = scaffolds_mod.group_workspace_of(row)
        mounts = [system_mounts.GLOBAL_SLUG, subject] + ([group] if group else [])

        rec = _compose_and_mint(
            who=who, subject=subject, kind="hand-link", opening=preset, fm=fm,
            mid=mid, mounts=mounts, group=group, refs_in=None,
            tabs=None, focus=None,
            provenance={"flow": "hand-link", "minted_by": subject},
        )
        logger.info("scaffold MINTED (hand) id=%s who=%s meeting=%s opening=%s",
                    rec["id"], who, rec.get("meeting"), rec["opening"])
        return {"id": rec["id"], "url": f"{ui}/?s={rec['id']}"}
    @router.get("/api/scaffolds/{scaffold_id}")
    def read_scaffold(scaffold_id: str, request: Request):
        """The record, as its recipient. Refuses anyone else; marks REDEEMED on first read.

        A 404 for an unknown id AND for one that is not yours — the id is a capability until redeem
        binds it, so a 403 would tell a prober that a scaffold with that id exists."""
        subject = subject_of(request)
        rec = scaffolds.get(scaffold_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such scaffold")
        if not (_internal_caller(request) or _scaffold_is_for(rec, request, subject)):
            logger.warning("scaffold REFUSED id=%s subject=%s reason=not-the-recipient", scaffold_id, subject)
            raise HTTPException(status_code=404, detail="no such scaffold")
        # REDEEM ONLY FOR THE RECIPIENT (R-A13). An admin's debugging read used to stamp
        # `redeemed_at`/`redeemed_by` with the ADMIN — and `scaffolds.redeem`'s own docstring calls
        # that stamp "the only measurement the alpha ledger's 'seconds to act' column is made of".
        #
        # It is load-bearing twice over, which is why it is fixed here rather than left on the
        # backlog: `redeemed_by == subject` is one of the three identity tests BOTH scaffold
        # predicates run, so a stamp written by an admin's read promoted that admin to "the
        # recipient" on every later call — including the share hand-out one route down, which is
        # exactly the capability R-A08 exists to keep bound to one person.
        if _scaffold_recipient_is(rec, request, subject):
            rec = scaffolds.redeem(scaffold_id, subject) or rec
        try:
            return _scaffold_view(rec, subject)
        except scaffolds_mod.ScaffoldError as e:
            # The preset was there at mint and is not there now — an admin deleted or emptied it.
            # Say so; a silent empty opening is the failure that let the phase greeting win (F5).
            raise HTTPException(status_code=409, detail=str(e)) from e
    @router.post("/api/scaffolds/{scaffold_id}/share")
    def redeem_scaffold_share(scaffold_id: str, request: Request):
        """The transcript share this record carries, handed to its RECIPIENT — the replacement for
        `&tshare=` in the mailed link (R-A08).

        A scaffold for a meeting that is not the reader's own carries a restricted grant, minted by
        the meeting's owner in `core/flows` and passed to the mint. It used to ride the link's query
        string, where a bearer credential enters every access log and proxy trace between us and the
        recipient's inbox — and then whatever they forward. `worker/engine.py:1044-1046` states the
        rule for the delegation token in as many words; this was the weaker spelling on the more
        exposed artefact.

        `{"token": null}` — not a 404 — when the record carries no share: most scaffolds are about
        the reader's own meeting and carry no capability, and answering "not found" for the ordinary
        case would teach the client to treat it as breakage.

        A 404 for a stranger AND for an unknown id, exactly as the read route answers, so this route
        cannot be used to discover that a scaffold id exists. An INSTANCE ADMIN gets the 404 too —
        see `_scaffold_recipient_is` — and so does the internal tier: the mint hands the token IN,
        and a route that handed it back out would be a second way to reach it."""
        subject = subject_of(request)
        rec = scaffolds.get(scaffold_id)
        if rec is None or not _scaffold_recipient_is(rec, request, subject):
            logger.warning("scaffold share REFUSED id=%s subject=%s reason=not-the-recipient",
                           scaffold_id, subject)
            raise HTTPException(status_code=404, detail="no such scaffold")
        rec = scaffolds.hand_share(scaffold_id, subject) or rec
        token = rec.get("share_token") or None
        if token:
            logger.info("scaffold share HANDED id=%s subject=%s", scaffold_id, subject)
        return {"token": token}
    @router.get("/api/scaffolds")
    def list_scaffolds(request: Request, mine: bool = True, pending: bool = True):
        """The caller's own scaffolds — what is WAITING for this person behind a link they may never
        have clicked. Step 6 of the build order (`whats_waiting` returns pending scaffolds so the
        person's own agent opens the same record) reads exactly this: one record, two renderers."""
        subject = subject_of(request)
        email = (request.headers.get("x-user-email") or "").strip().lower()
        # Indexed by ADDRESS, because that is what a scaffold is minted against — the recipient
        # usually has no subject yet. With no gateway-injected address there is nothing to look up,
        # and the honest answer is an empty list, never every scaffold on the instance.
        rows = scaffolds.for_recipient(email, pending_only=pending) if email else []
        out = []
        for r in rows:
            try:
                out.append(_scaffold_view(r, subject))
            except scaffolds_mod.ScaffoldError:
                # One scaffold whose preset an admin deleted must not empty the whole list.
                logger.warning("scaffold %s is unrenderable (its preset is gone) — omitted", r.get("id"))
        return {"scaffolds": out}
    @router.post("/api/desk/touch", status_code=202)
    def desk_touch(request: Request, body: dict = Body(default={})):
        """Record that this person opened one page — the ranking signal behind the desk README.

        202, not 200: it is a report, the caller has nothing to do with the answer, and a panel
        must never wait on bookkeeping to render a document.

        WHAT IT WILL NOT DO is trust the workspace it is told. The body names a workspace id; the
        access rule is applied to it exactly as it is for a link, so a touch cannot be used to ask
        whether a workspace exists, and a page in a workspace this caller cannot read is not
        recorded. The DESK the touch is filed under is always the CALLER'S OWN — never a parameter,
        because "whose desk does this belong to" is not a question a client gets to answer."""
        subject = subject_of(request)
        wid = str(body.get("workspace") or "").strip()
        # NOT `.lstrip("/")`. Silently turning `/etc/passwd` into `etc/passwd` is a normalization
        # that RESCUES the one input the guard below exists to refuse — and this route's answer
        # travels into the desk README as a link.
        path = str(body.get("path") or "").strip()
        try:
            wpaths.relative_parts(path)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not wid:
            raise HTTPException(status_code=400, detail="a touch names a workspace id and a path")
        rec = workspace_registry.get(wid)
        if ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member) != ids_mod.ACCESS_READABLE:
            return {"recorded": False}          # not readable — nothing to say, and nothing leaked
        desk = workspace_registry.by_slug(str(subject)) or _ws_sync(str(subject), kind="desk",
                                                                    owner=str(subject))
        if not desk:
            return {"recorded": False}
        workspace_touches.touch(desk["id"], wid, path)
        # Mirror it where the WORKER can read it: the README is regenerated at the end of a turn,
        # in a container that holds the mounts and no redis.
        ids_mod.mirror_touches(desk.get("dir") or (wsr.root / str(subject)),
                               workspace_touches.recent(desk["id"]))
        return {"recorded": True}
    @router.post("/api/links/resolve")
    def links_resolve(request: Request, body: dict = Body(default={})):
        """A page's refs → what each one points at now, per ref: `{ref, title, url, access}`.

        One round trip for a whole document on purpose. The panel renders a doc at once, and a
        request per link would be a burst of them against the same three directories."""
        refs = body.get("refs")
        if not isinstance(refs, list):
            raise HTTPException(status_code=400, detail="refs must be a list of link references")
        slug = str(body.get("slug") or "").strip() or None
        return {"results": link_resolver_mod.resolve_many(
            [str(r) for r in refs], subject=subject_of(request), root=wsr.root,
            registry=workspace_registry, here=_ws_here(request, slug), is_member=_ws_is_member)}

    return router
