"""routers/workspace_states.py — what each workspace IS to a person, and what one chat reads
(Vexa-ai/vexa#1783, founder 2026-10-14).

*"I can't use the software if the pile of workspaces is growing, I can't get rid of them, and I
can't properly manage agent context."* Three verbs, set from the chat first:

  ``GET  /api/workspace/states``   every workspace with its state — ``active`` · ``inactive`` ·
                                   ``hidden`` — plus this chat's target and reads and the person's
                                   defaults. Hidden ones are left out unless ``hidden=true``.
  ``POST /api/workspace/state``    put workspaces (or ``all``) in a state. ``hidden`` is the
                                   person's, everywhere; ``active``/``inactive`` change THIS chat's
                                   reads when asked from a chat, else the default for new chats.
  ``POST /api/workspace/defaults`` where new chats write, and which workspaces they read.

ONE WRITER EACH. The per-person states live in the attach state (``workspace_attach``), the chat's
reads and target in the session record (``_Sessions.set_reads`` / ``set_target``), the default
target where it always was (``_Sessions.set_default_target``). This router composes them; it keeps
no store of its own.

WHICH CHAT. A worker's call names its chat by the unit its delegation was signed for — it cannot
name another; a person's own call (the header's toggles) passes ``session``.
"""
from __future__ import annotations

import re
from typing import Literal, Optional, Union

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from control_plane import global_layer
from control_plane import identity_token
from control_plane import system_mounts
from control_plane import workspace_attach as attach
from control_plane import workspace_membership as membership_mod
from control_plane import workspace_names as names_mod
from control_plane.api_shared import _is_slug, logger
from control_plane.ceiling import is_unwatched, reads_within, require_in_ceiling
from control_plane.routers.chat_read import _unit_session
from control_plane.workspace_membership import MembershipError
from shared import units

DESK = "personal"


class WorkspaceStateBody(BaseModel):
    """``workspace_state_set`` — put workspaces in a state."""
    model_config = {"extra": "forbid"}
    workspaces: list[str] = Field(
        ..., description='Workspace names or slugs, or ["all"] (the string "all" is accepted too). '
                         '"personal" is the person\'s own desk.')
    excluding: list[str] = Field(default_factory=list,
                                 description='With "all": the workspaces to leave as they are ("all except …").')
    state: Literal["active", "inactive", "hidden"] = Field(
        ..., description="active = mounted and readable; inactive = listed, not mounted; hidden = not "
                         "mounted, not listed, kept with its history (unhide = active).")
    scope: Optional[Literal["chat", "default"]] = Field(
        default=None, description='For active/inactive: "chat" changes only this conversation, '
                                  '"default" changes what new chats read. Omitted: this chat when asked '
                                  'from one, else the default. Hidden is always the person\'s, everywhere.')
    session: str = Field(default="", max_length=128,
                         description="The person's own chat id (the header's toggles); a worker's chat is "
                                     "known from its delegation and this is ignored.")


    @field_validator("workspaces", mode="before")
    @classmethod
    def _all_as_text(cls, v):
        """ "all" as a string, as the description says and as an agent writes it (friction 11:30)."""
        return [v] if isinstance(v, str) else v


class WriteTargetProposalBody(BaseModel):
    """`propose_write_target` — ASK the person to move this chat's writes (UI decision 6b)."""
    model_config = {"extra": "forbid"}
    workspace: str = Field(..., min_length=1, max_length=200,
                           description="The workspace you think this request belongs in, by name (or slug).")
    reason: str = Field(default="", max_length=300,
                        description="One short clause the person reads: why that workspace, e.g. 'the request is about the OeNB pilot'.")
    session: str = Field(default="", max_length=128, description="The person's own chat id; a worker's chat is known.")


#: Said with every proposal: the agent waits for the click, and writes nothing meanwhile.
PROPOSAL_SAY = ("Asked the person whether this chat should write to {name} instead of {current}. Say that "
                "in one line and WAIT for their click — do not write to {current} meanwhile (reading is "
                "fine). Their choice arrives as their next message.")


class WorkspaceDefaultsBody(BaseModel):
    """``workspace_defaults_set`` — what a person's NEW chats start with."""
    model_config = {"extra": "forbid"}
    target: Optional[str] = Field(default=None, max_length=128,
                                  description='Where new chats write: a workspace the person can write, or '
                                              '"personal". Omitted: unchanged.')
    active: Optional[list[str]] = Field(default=None,
                                        description="Exactly the workspaces new chats read; every other "
                                                    "one that is not hidden becomes inactive. Omitted: "
                                                    "unchanged.")


class ChatProfileBody(BaseModel):
    """``chat_profile_set`` — use a profile in this chat."""
    model_config = {"extra": "forbid"}
    name: str = Field(..., max_length=32, description="work, study, hobby, incognito, or one the person made.")
    session: str = Field(default="", max_length=128, description="The person's own chat id; a worker's chat is known.")
    workspace: str = Field(default="", max_length=128, description=(
        "Binds the profile's {{workspace}} variable — \"use shared work for Collab Room\"."))
    new_chat: bool = Field(default=False, description=(
        "true: START A NEW chat in this profile and open it (\"start an incognito chat\"); this chat "
        "is left as it is."))


class ChatProfileDeleteBody(BaseModel):
    """``chat_profile_delete`` — remove a profile the person made; a starter goes back to how it was."""
    model_config = {"extra": "forbid"}
    name: str = Field(..., max_length=32)


class ChatProfileEditBody(BaseModel):
    """``chat_profile_edit`` — change what a profile does."""
    model_config = {"extra": "forbid"}
    name: str = Field(..., max_length=32)
    target: Optional[str] = Field(default=None, max_length=128,
                                  description='Where its chats write ("personal" for their own).')
    reads: Optional[list[str]] = Field(default=None, description="Exactly what its chats read.")
    default: Optional[bool] = Field(default=None, description="true: new chats start in this profile.")


def profile_scope(prof: dict, rows: list[dict], subject: str,
                  bind: Optional[dict] = None) -> tuple[str, Optional[list[str]], bool]:
    """``(target, reads, no_target)`` a profile puts on a chat — variables bound (``bind``), names
    resolved, hidden ones dropped. An unbound variable is a ``ValueError`` naming it."""
    if prof.get("incognito"):
        return "", [], True
    bind = bind or {}
    for var in prof.get("variables") or []:
        if not str(bind.get(var) or "").strip():
            raise ValueError(f"the {prof['name']} profile needs {var} — say which workspace")
    prof = {**prof, "target": bind.get(prof.get("target"), prof.get("target")),
            "reads": None if prof.get("reads") is None else [bind.get(r, r) for r in prof["reads"]]}
    visible = {r["slug"]: r for r in rows if r.get("state") != "hidden"}

    def slug(ref):
        ref = str(ref or "").strip()
        if not ref or ref.casefold() in (DESK, "desk") or ref == str(subject):
            return DESK
        try:
            got = names_mod.resolve(rows, ref, str(subject))
        except names_mod.Ambiguous:
            return None
        return got if got in visible else None
    t = slug(prof.get("target")) if prof.get("target") else DESK
    reads = None
    if prof.get("reads") is not None:
        reads = [x for x in (slug(r) for r in prof["reads"]) if x]
    return ("" if t in (DESK, None) else t), reads, False


def apply_profile(sess, subject: str, session: str, name: str, scope: tuple) -> None:
    """Put a profile's scope on a chat — the one place it is written, from the verb and from a new
    chat's first turn."""
    target, reads, nowhere = scope
    if nowhere:
        sess.set_reads(subject, session, [])
        sess.set_no_target(subject, session)
    else:
        sess.set_target(subject, session, target)
        sess.set_reads(subject, session, reads)
    sess.set_profile(subject, session, name, applied=_scope_key(target, reads, nowhere))


def _scope_key(target, reads, nowhere) -> dict:
    """A chat's scope in one comparable shape — what a profile applied vs what the chat has now."""
    return {"target": "" if nowhere else (target or ""), "reads": None if reads is None else sorted(reads),
            "nowhere": bool(nowhere)}


def company_profiles(settings, root) -> tuple[dict, str]:
    """What the COMPANY seeds (Vexa-ai/vexa#1783): chat profiles from `_global/PROFILES.md` front
    matter (``profiles: {name: {target, reads}}``, `{{workspace}}` allowed) and the default profile for
    new chats from `_global/POLICIES.md` (``default_chat_profile``). Missing or unreadable = nothing."""
    try:
        import yaml  # noqa: PLC0415
        from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415
        g = system_mounts.global_root(settings, root)
        prof_text = _wpaths.read_text_inside(g, "PROFILES.md", max_bytes=256_000) or ""
        pol_text = _wpaths.read_text_inside(g, "POLICIES.md", max_bytes=1_000_000) or ""
    except Exception:  # noqa: BLE001
        return {}, ""
    profiles: dict = {}
    m = re.match(r"^---\n(.*?)\n---", prof_text, re.S)
    if m:
        try:
            data = yaml.safe_load(m.group(1)) or {}
            if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
                profiles = data["profiles"]
        except Exception:  # noqa: BLE001 — a malformed file seeds nothing
            profiles = {}
    d = re.search(r"^default_chat_profile:\s*([A-Za-z0-9 _-]+?)\s*$", pol_text, re.M)
    return profiles, (d.group(1).strip().lower() if d else "")


def first_turn_profile(sess, settings, root, subject: str, rows: list[dict]):
    """The profile a NEW chat starts in, and its scope: a queued one (an invite link just opened),
    else the person's default, else the company's. ``None`` when there is none."""
    company, company_default = company_profiles(settings, root)
    profs, mine = attach.chat_profiles(root, subject, company)
    queued = attach.take_queued_profile(root, subject)
    if queued and queued.get("name") == attach.SHARED_WORK:
        # an opened workspace link: the shared scope, unnamed (there are no prebuilt profiles)
        try:
            return "", profile_scope(_shared_scope(), rows, subject, queued.get("bind"))
        except ValueError:
            pass
    for name, bind in ((queued or {}).get("name"), (queued or {}).get("bind")), (mine, None), (company_default, None):
        prof = next((p for p in profs if p["name"] == name), None) if name else None
        if prof is None:
            continue
        try:
            return prof["name"], profile_scope(prof, rows, subject, bind)
        except ValueError:
            continue
    return None


def _shared_scope() -> dict:
    """The shared chat's scope as a profile-shaped record: writes to and reads only {{workspace}}."""
    sc = attach.STARTER_SCOPES[attach.SHARED_WORK]
    return {"name": "", "target": sc["target"], "reads": list(sc["reads"]), "incognito": False,
            "variables": [attach.WORKSPACE_VAR]}


def attachment_home(sess, root, subject: str, session: str, rows_for=None) -> str:
    """WHERE A FILE ATTACHED IN THIS CHAT IS STORED (Vexa-ai/vexa#1783, founder 2026-10-15): the
    chat's target workspace; ``_system`` (no workspace, kept with the chat) for an incognito chat or
    one that writes nowhere; ``""`` — the person's desk — only when the chat writes to Personal and
    Personal is not hidden. A new chat that has no record yet takes its default target."""
    if not session or sess is None:
        return ""
    try:
        if sess.is_incognito(subject, session) or sess.no_target(subject, session):
            return system_mounts.SYSTEM_SLUG
        t = sess.target(subject, session)
        if not t and not sess.has_record(subject, session) and rows_for is not None:
            t, _ = default_target_for(sess, subject, rows_for())
        if t:
            return t
        state = attach._load_state(attach._store(root, subject))
        if attach._seed_slot_slug(state) in attach._hidden_of(state):
            return system_mounts.SYSTEM_SLUG
    except Exception:  # noqa: BLE001 — unknown: the person's own desk, as before
        logger.warning("attachment home unreadable for subject=%s session=%s", subject, session)
    return ""


def use_in_chat(request, sess, settings, root, mindex, registry, subject: str, workspace_id: str,
                explicit_session: str = "") -> str:
    """THE CHAT THAT MADE A WORKSPACE WORKS IN IT, THIS TURN (Vexa-ai/vexa#1783, founder 2026-10-15:
    the agent made a workspace for the work in a chat, the chat kept writing to Personal, and the
    agent told him to go to another chat). Its target becomes the workspace and the workspace joins
    its reads; a SHARED workspace's chat reads only it (no private workspace). Returns the chat id,
    or "" when the call came from no chat. A worker's chat is the one its delegation names."""
    unit = identity_token.session_of(request.headers)
    session = _unit_session(unit, str(subject)) if unit else (explicit_session or "")
    if not session or sess is None:
        return ""
    try:
        from pathlib import Path as _P  # noqa: PLC0415
        if (_P(root) / workspace_id).is_dir() and membership_mod.is_member(root, workspace_id, subject):
            bind_shared_work_for(sess, settings, root, mindex, registry, subject, session, workspace_id)
            return session
        own = sess.reads(subject, session)
        sess.set_target(subject, session, workspace_id)
        if own is not None and workspace_id not in own:
            sess.set_reads(subject, session, [*own, workspace_id])
        return session
    except Exception:  # noqa: BLE001 — the workspace exists; the chat's scope is the extra
        logger.warning("chat not switched to new workspace for subject=%s", subject)
        return ""


def rehome_shared(sess, settings, root, mindex, registry, subject: str, old: str, new: str) -> list:
    """A private workspace just became shared under a new id: every chat of this person that WROTE
    to it now writes to the shared one and reads only it (the shared rule); a chat that only READ it
    reads the new id instead. Returns the chats moved."""
    moved = []
    try:
        rows = sess.list(subject)
    except Exception:  # noqa: BLE001
        return moved
    for row in rows:
        s = str(row.get("session") or "")
        try:
            if sess.target(subject, s) == old:
                bind_shared_work_for(sess, settings, root, mindex, registry, subject, s, new)
                moved.append(s)
            else:
                own = sess.reads(subject, s)
                if own is not None and old in own:
                    sess.set_reads(subject, s, [new if w == old else w for w in own])
                    moved.append(s)
        except Exception:  # noqa: BLE001
            continue
    return moved


def bind_shared_work_for(sess, settings, root, mindex, registry, subject: str, session: str,
                         workspace_id: str) -> bool:
    """``bind_shared_work`` for a person whose workspace listing the caller does not hold. Fail-soft:
    a binding that cannot be made leaves the chat on its defaults and never fails the share."""
    if not workspace_id or not session:
        return False
    try:
        memberships, _ = membership_mod.reconciled_memberships(root, subject, mindex.list)
        rows = names_mod.listing(root, subject, registry=registry, index_list=lambda _s: memberships)
        return bind_shared_work(sess, settings, root, subject, session, workspace_id, rows)
    except Exception:  # noqa: BLE001
        logger.warning("shared work not bound for subject=%s session=%s", subject, session)
        return False


def bind_shared_work(sess, settings, root, subject: str, session: str, workspace_id: str,
                     rows: list[dict]) -> bool:
    """THE SHARED-CHAT SEAM (Vexa-ai/vexa#1783): put "shared work" bound to ``workspace_id`` on one
    chat — what sharing a chat into a workspace, or opening a chat from its link, does for each
    participant. ``rows`` is the person's ``workspace_names.listing``. False when the person cannot
    use it (the profile is missing, or the workspace is not theirs to read)."""
    if _row_for(rows, workspace_id) is None:
        return False
    apply_profile(sess, subject, session, "",
                  profile_scope(_shared_scope(), rows, subject, {attach.WORKSPACE_VAR: workspace_id}))
    return True


def _retention_days(settings, root) -> int:
    """`incognito_retention_days` in `_global/POLICIES.md`, 7 when unset (the chats router purges)."""
    try:
        from workspaces.shared import workspace_paths as _wpaths  # noqa: PLC0415
        text = _wpaths.read_text_inside(system_mounts.global_root(settings, root), "POLICIES.md",
                                        max_bytes=1_000_000) or ""
        m = re.search(r"^incognito_retention_days:\s*(\d+)\s*$", text, re.M)
        return max(0, int(m.group(1))) if m else 7
    except Exception:  # noqa: BLE001
        return 7


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


def _row_for(rows: list[dict], slug: str) -> Optional[dict]:
    return next((r for r in rows if r["slug"] == slug or r.get("also") == slug), None)


def default_target_for(sess, subject: str, rows: list[dict]) -> tuple[str, str]:
    """WHERE A NEW CHAT WRITES (Vexa-ai/vexa#1783, founder 2026-10-14: *"default set to biz but it's
    writing in personal"*): the person's default target as a slug, or ``""`` for Personal, and a
    sentence when a default exists but cannot be used — gone, hidden, or only readable — so the
    header can say why it is Personal. Decided HERE, server-side, for every new chat, so no client
    has to win a race to apply it. ``rows`` is ``workspace_names.listing`` for the person."""
    try:
        wid = str(sess.default_target(subject) or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable default is Personal
        return "", ""
    if not wid or wid == DESK:
        return "", ""
    row = _row_for(rows, wid)
    if row is None or row["kind"] == "personal":
        return "", ("" if row is not None else
                    "Your default for new chats no longer exists, so new chats write to Personal.")
    if row.get("state") == "hidden":
        return "", f"Your default for new chats, {row['name']}, is hidden, so new chats write to Personal."
    if row["kind"] == "shared" and row.get("role") not in ("owner", "contributor"):
        return "", f"You can only read {row['name']}, so new chats write to Personal."
    return row["slug"], ""


def build(**d) -> APIRouter:
    router = APIRouter()
    _read_target = d["_read_target"]
    mindex = d["mindex"]
    sess = d["sess"]
    settings = d["settings"]
    subject_of = d["subject_of"]
    workspace_registry = d["workspace_registry"]
    wsr = d["wsr"]
    session_store = d.get("session_store")

    def _not_the_owner(subject: str, session: str) -> bool:
        """A SHARED chat's profile is its owner's to switch (UI decision 6)."""
        if session_store is None or not session:
            return False
        try:
            from control_plane import session_sync  # noqa: PLC0415
            row = session_sync.resolve(session_store, subject, session, create=False)
            if row is None:
                return False
            humans = [p for p in session_store.participants(row["id"]) if p.get("kind") == "human"]
            return len(humans) > 1 and str(row.get("owner") or "") != str(subject)
        except Exception:  # noqa: BLE001 — unknown: let the person act on their own chat
            return False

    def _session_of(request: Request, given: str = "") -> str:
        """The chat this call is about: a worker's own (signed), else what the person passed, else ""."""
        subject = str(subject_of(request))
        unit = identity_token.session_of(request.headers)
        if unit:
            return _unit_session(unit, subject)
        s = (given or "").strip()
        return s if s and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", s) else ""

    def _rows(subject: str) -> list[dict]:
        memberships, _ = membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        rows = names_mod.listing(wsr.root, subject, registry=workspace_registry,
                                 index_list=lambda _s: memberships, with_emptiness=True)
        return rows

    def _resolve(rows: list[dict], subject: str, ref: str) -> str:
        """A name or slug → the slug; ``personal`` for the person's own desk. Unknown → 404."""
        ref = str(ref or "").strip()
        if ref in (DESK, "desk", str(subject), attach.SEED_SLOT) or ref.casefold() == "personal":
            return DESK
        try:
            slug = names_mod.resolve(rows, ref, str(subject))
        except names_mod.Ambiguous as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if not any(r["slug"] == slug or r.get("also") == slug for r in rows):
            raise HTTPException(status_code=404, detail=f"no workspace called {ref!r}")
        row = next(r for r in rows if r["slug"] == slug or r.get("also") == slug)
        return row["slug"]

    def _default_reads(rows: list[dict]) -> list[str]:
        return [r["slug"] for r in rows if r.get("state") == "active"]

    def _chat_view(subject: str, session: str, rows: list[dict]) -> dict:
        own = None
        target = ""
        note = ""
        fresh = True
        nowhere = False
        if session:
            try:
                own = sess.reads(subject, session)
                target = sess.target(subject, session)
                fresh = not sess.has_record(subject, session)
                nowhere = sess.no_target(subject, session)
            except Exception:  # noqa: BLE001 — an unreadable record is a chat on the defaults
                logger.warning("chat scope unreadable for subject=%s session=%s", subject, session)
        if fresh and not target:
            # A NEW CHAT shows what its first turn will do: write to the default (chats.py applies
            # the same `default_target_for` when the turn starts).
            target, note = default_target_for(sess, subject, rows)
        visible = {r["slug"] for r in rows if r.get("state") != "hidden"}
        reads = [w for w in (own if own is not None else _default_reads(rows)) if w in visible]
        profile = ""
        changed = False
        if session:
            try:
                profile = sess.profile(subject, session)
                if profile:
                    # AD-HOC SCOPE ("write here", "exclude this") after the profile: "Work (changed)".
                    changed = sess.profile_applied(subject, session) != _scope_key(target, own, nowhere)
            except Exception:  # noqa: BLE001
                profile = ""
        if nowhere:
            # NO TARGET (Vexa-ai/vexa#1783, "no workspaces"): it writes nowhere and reads only what it
            # names — nothing, when it names nothing.
            return {"session": session or None, "target": None, "reads": reads,
                    "own_reads": own is not None, "profile": profile or None, "changed": changed}
        target = target or DESK
        if target not in visible:
            # a hidden or gone target: Personal — unless Personal itself is hidden, then nowhere
            target = DESK if DESK in visible else None
        if target is not None and target not in reads:
            reads = [*reads, target]
        return {"session": session or None, "target": target, "reads": reads,
                "own_reads": own is not None, "profile": profile or None, "changed": changed,
                **({"note": note} if note else {})}

    def _name(rows: list[dict], slug: str) -> str:
        row = next((r for r in rows if r["slug"] == slug), None)
        return (row.get("display") or row["name"]) if row else slug

    def _view(request: Request, subject: str, session: str, include_hidden: bool) -> dict:
        rows = _rows(subject)
        chat = _chat_view(subject, session, rows)
        try:
            default_target = sess.default_target(subject) or DESK
        except Exception:  # noqa: BLE001
            default_target = DESK
        # A delegated dispatch sees only what is inside its ceiling, as `/api/workspace/shared` does.
        rows = [r for r in rows if r["kind"] == "personal" or reads_within(request, r["slug"])]
        hidden = [r for r in rows if r.get("state") == "hidden"]
        shown = [r for r in rows if include_hidden or r.get("state") != "hidden"]
        out = []
        for r in shown:
            out.append({"slug": r["slug"], "name": r["name"], "display": r.get("display") or r["name"],
                        "kind": r["kind"], "role": r.get("role"),
                        "state": r.get("state", "active"), "reads": r["slug"] in chat["reads"],
                        "target": chat["target"] is not None and r["slug"] == chat["target"],
                        **({"empty": r["empty"]} if "empty" in r else {})})
        n = len(chat["reads"])
        # THE PROFILE FIRST (founder 2026-10-14, UI decision 6): "Work · writes to biz · reads 8",
        # "Work (changed) · …" after an ad-hoc change, "No profile · …" when none is chosen.
        # LEAN (founder 2026-10-15): "Writes to biz"; with a profile "Work · writes to biz";
        # "Incognito". No "No profile", no counts — the menu holds the detail.
        prof = chat.get("profile")
        label = (prof.capitalize() + (" (changed)" if chat.get("changed") else "")) if prof else ""
        if prof == attach.INCOGNITO and not chat.get("changed"):
            line = "Incognito"
        else:
            where = ("No workspaces" if chat["target"] is None and not n
                     else "Writes nowhere" if chat["target"] is None
                     else f"Writes to {_name(rows, chat['target'])}")
            line = f"{label} · {where[0].lower() + where[1:]}" if label else where
        dt_slug, dt_note = default_target_for(sess, subject, rows)
        return {"workspaces": out, "hidden": len(hidden), "chat": chat,
                "defaults": {"target": dt_slug or DESK, "target_name": _name(rows, dt_slug) if dt_slug else "Personal",
                             "stored": default_target, "active": _default_reads(rows),
                             **({"note": dt_note} if dt_note else {})},
                "line": line}

    @router.post("/api/workspace/propose-target")
    def ws_propose_target(request: Request, body: WriteTargetProposalBody = Body(...)):
        """ASK THE PERSON TO MOVE THIS CHAT'S WRITES to another workspace (UI decision 6b, founder
        2026-10-15: "the agent should think which workspace it wants to write to and should ask to
        change its writing target proactively"). Call it BEFORE your first write in a turn whenever
        what the request is about — a workspace it names, the meeting's workspace, the open page's
        workspace, the people involved — is not where this chat writes, or when you are not sure
        the current target is right. It changes NOTHING: the person sees "Write to <X> instead of
        <Y>? — <reason>" with Switch and Keep, and only their click moves the target (writes to a
        shared workspace are seen by other people, so this is never the agent's call). Until they
        answer, do not write to the current target; reading is fine. Their answer arrives as their
        next message."""
        subject = str(subject_of(request))
        rows = _rows(subject)
        slug = _resolve(rows, subject, body.workspace)
        if slug != DESK:
            require_in_ceiling(request, slug)
            row = next((r for r in rows if r["slug"] == slug), None)
            if row is not None and row.get("state") == "hidden":
                raise HTTPException(status_code=409, detail=f"{row['name']} is hidden — the person would have to unhide it first")
            if row is not None and row["kind"] == "shared" and row.get("role") not in ("owner", "contributor"):
                raise HTTPException(status_code=403, detail=f"The person can only read {row['name']}, so it cannot be this chat's target")
        session = _session_of(request, body.session)
        chat = _chat_view(subject, session, rows)
        current = chat.get("target") or ""
        name = _name(rows, slug) if slug != DESK else "Personal"
        current_name = (_name(rows, current) if current and current != DESK else "Personal") if chat.get("target") is not None else "nowhere"
        if current == slug:
            return {"proposed": None, "already": True, "say": f"This chat already writes to {name}. Go ahead."}
        return {"ui_action": "propose_target", "proposed": slug, "name": name,
                "current": current or None, "current_name": current_name,
                "reason": body.reason.strip(), "session": session or None,
                "say": PROPOSAL_SAY.format(name=name, current=current_name)}

    @router.get("/api/workspace/states")
    def ws_states(request: Request,
                  hidden: bool = Query(default=False, description="Include hidden workspaces."),
                  session: str = Query(default="", max_length=128,
                                       description="The person's own chat id; a worker's chat is known.")):
        """EVERY WORKSPACE THE PERSON HAS, WITH ITS STATE — `active` (mounted and readable),
        `inactive` (listed, not mounted) or `hidden` (not mounted, not listed; the person can unhide
        it). Hidden ones are left out unless `hidden=true`: do not mention them unless asked. Also
        THIS chat's `target` (where writes go) and `reads` (what is mounted), the person's
        `defaults` for new chats, and `line` — the header's own words, "Writes to X · Reads N
        workspaces". Call workspaces by `name`; the personal one is "Personal"."""
        subject = str(subject_of(request))
        if global_layer.is_admin_account(settings, subject):
            return {"workspaces": [], "hidden": 0, "admin_account": True,
                    "chat": {"session": None, "target": system_mounts.GLOBAL_SLUG,
                             "reads": [system_mounts.GLOBAL_SLUG], "own_reads": False},
                    "defaults": {"target": system_mounts.GLOBAL_SLUG, "active": []},
                    "line": "Writes to Company · Reads 1 workspace"}
        return _view(request, subject, _session_of(request, session), hidden)

    @router.post("/api/workspace/state")
    def ws_state_set(request: Request, body: WorkspaceStateBody = Body(...)):
        """PUT WORKSPACES IN A STATE when the person asks — "hide the old ones", "show finos again",
        "only read Personal and Example Bank here", "hide all". `workspaces` is a list of names (or
        "all"); `state` is `active`, `inactive` or `hidden`. "Unhide all" is `all` + `active` with
        `scope: "default"`; in a chat, `all` + `active` selects every workspace that is not hidden.

        HIDDEN is the person's, everywhere: not mounted, not listed, reversible, history kept; hiding
        a shared workspace hides it for them only. The personal workspace can't be hidden. ACTIVE /
        INACTIVE change THIS chat's reads when asked from a chat (`scope` omitted or "chat"), or what
        new chats read (`scope: "default"`). Mounts change from the next message. Say what changed in
        one line, by name."""
        subject = str(subject_of(request))
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="workspace states are the person's to change")
        if global_layer.is_admin_account(settings, subject):
            raise HTTPException(status_code=403, detail="the admin account has one workspace, Company")
        rows = _rows(subject)
        session = _session_of(request, body.session)
        scope = body.scope or ("chat" if session else "default")
        every = [w.strip().casefold() for w in body.workspaces] == ["all"]
        if every:
            # hide all = everything but Personal; select all = everything, hidden ones back too;
            # unselect all = everything not hidden (where the chat writes stays, below).
            if body.state == "hidden":
                names = [r["slug"] for r in rows if r["kind"] != "personal"]
            elif body.state == "active" and scope == "default":
                names = [r["slug"] for r in rows]                     # unhide all
            elif body.state == "active":
                names = [r["slug"] for r in rows if r.get("state") != "hidden"]
            else:
                names = [r["slug"] for r in rows if r.get("state") != "hidden"]
            if body.excluding:
                kept = {_resolve(rows, subject, w) for w in body.excluding}
                names = [n for n in names if n not in kept]
        else:
            names = [_resolve(rows, subject, w) for w in body.workspaces]
        if not names:
            raise HTTPException(status_code=422, detail="name at least one workspace")
        if every:
            names = [n for n in names if n == DESK or reads_within(request, n)]
        else:
            require_in_ceiling(request, *[n for n in names if n != DESK])
        kind = {r["slug"]: r["kind"] for r in rows}
        changed: list[str] = []

        def _per_person(slug: str, state: str) -> None:
            if state == "active" and kind.get(slug) == "shared":
                try:
                    membership_mod.require_role(wsr.root, slug, subject, "viewer")
                except MembershipError as exc:
                    raise HTTPException(status_code=exc.status if hasattr(exc, "status") else 403,
                                        detail=str(exc))
            try:
                if attach.set_workspace_state(wsr.root, subject, slug, state):
                    changed.append(slug)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no workspace {slug!r}")

        if body.state == "hidden" or scope == "default":
            for slug in names:
                _per_person(slug, body.state)
        else:
            # Unhiding from a chat brings it back for the person too — "unhide" means active.
            for slug in names:
                if next((r for r in rows if r["slug"] == slug), {}).get("state") == "hidden":
                    _per_person(slug, "active")
        if scope == "chat" and session and body.state in ("active", "inactive"):
            rows_now = _rows(subject)
            before = _chat_view(subject, session, rows_now)
            current, target = before["reads"], before["target"]
            if body.state == "active":
                sess.set_reads(subject, session, [*current, *[n for n in names if n not in current]])
            else:
                sess.set_reads(subject, session, [w for w in current if w not in names])
                if target is not None and target in names:
                    # UNSELECTING WHERE IT WRITES leaves it writing nowhere — "no workspaces" when
                    # nothing else is read. Never reported as done while still reading it (friction 12:20).
                    sess.set_no_target(subject, session)
            after = _chat_view(subject, session, _rows(subject))
            moved = (set(before["reads"]) ^ set(after["reads"])) | (
                {target} if before["target"] != after["target"] and target else set())
            changed.extend(n for n in names if n in moved and n not in changed)
        if session and body.state == "hidden":
            own = sess.reads(subject, session)
            if own is not None:
                sess.set_reads(subject, session, [w for w in own if w not in names])
            # A chat cannot keep writing into a workspace its person just hid: back to Personal.
            if (sess.target(subject, session) or "") in names:
                sess.set_target(subject, session, "")
        if changed and (body.state == "hidden" or scope == "default"):
            try:
                sess.mark_all_stale(subject)     # every warm worker of theirs remounts on its next turn
            except Exception:  # noqa: BLE001
                logger.warning("mounts not marked stale for subject=%s", subject)
        view = _view(request, subject, session, include_hidden=False)
        done = ", ".join(_name(rows, n) if n != DESK else "Personal" for n in names)
        where = ("for you, everywhere" if body.state == "hidden"
                 else ("in this chat" if scope == "chat" and session else "for new chats"))
        return {"state": body.state, "scope": scope if body.state != "hidden" else "person",
                "workspaces": names, "changed": changed, "chat": view["chat"], "line": view["line"],
                "hidden": view["hidden"],
                "say": f"{body.state.capitalize()} {where}: {done}. Now: {view['line']}."}

    def _summary(prof: dict, rows: list[dict]) -> str:
        """One line for the menu: where it writes, how many it reads, memory."""
        if prof.get("incognito"):
            return "no workspaces · nothing remembered"
        t = prof.get("target")
        if isinstance(t, str) and t.startswith("{{"):
            where = "the shared workspace it is opened for"
        elif t:
            row = _row_for(rows, t)
            where = (row.get("display") or row["name"]) if row else t
        else:
            where = "Personal"
        reads = prof.get("reads")
        n = "your active workspaces" if reads is None else str(len(reads))
        return f"writes to {where} · reads {n} · memory on"

    @router.get("/api/workspace/profiles")
    def ws_profiles(request: Request, session: str = Query(default="", max_length=128)):
        """THE PERSON'S CHAT PROFILES — work, study, hobby, incognito and any they made: what each
        writes to and reads, and which one new chats start in (`default`). Incognito chats mount no
        workspace, remember nothing, are never read by another chat and are left out of chat search."""
        subject = str(subject_of(request))
        company, company_default = company_profiles(settings, wsr.root)
        profs, default = attach.chat_profiles(wsr.root, subject, company)
        rows = _rows(subject)
        chat = _session_of(request, session)
        active = ""
        if chat:
            try:
                active = sess.profile(subject, chat)
            except Exception:  # noqa: BLE001
                active = ""
        for p in profs:
            p["summary"] = _summary(p, rows)
        return {"profiles": profs, "default": default or company_default or None,
                "company_default": company_default or None, "active": active or None}

    @router.post("/api/workspace/profiles/delete")
    def ws_profile_delete(request: Request, body: ChatProfileDeleteBody = Body(...)):
        """DELETE A PROFILE when the person says so — one they made is gone; a starter (work, study,
        hobby, shared work) goes back to how it was. Chats using it keep their scope. Say it in one
        line."""
        subject = str(subject_of(request))
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="profiles are the person's to change")
        changed = attach.delete_chat_profile(wsr.root, subject, body.name)
        return {"name": body.name.strip().lower(), "deleted": changed,
                "say": (f"The {body.name} profile is removed." if changed
                        else f"There was nothing to remove for {body.name}.")}

    @router.post("/api/workspace/profile")
    def ws_profile_use(request: Request, body: ChatProfileBody = Body(...)):
        """USE A PROFILE IN THIS CHAT when the person says so — "use my work profile", "make this chat
        incognito". The chat's target and reads become the profile's from the next message. Say it in
        one line."""
        subject = str(subject_of(request))
        if body.workspace:
            require_in_ceiling(request, body.workspace)
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="a chat's profile is the person's to change")
        session = _session_of(request, body.session)
        if not body.new_chat and _not_the_owner(subject, session):
            raise HTTPException(status_code=403, detail="only the chat's owner switches a shared chat's profile")
        if body.new_chat:
            import time as _time
            session = "pchat-" + _base36(int(_time.time() * 1000))
        if not session:
            raise HTTPException(status_code=422, detail="say which chat")
        profs, _ = attach.chat_profiles(wsr.root, subject, company_profiles(settings, wsr.root)[0])
        prof = next((p for p in profs if p["name"] == body.name.strip().lower()), None)
        if prof is None:
            raise HTTPException(status_code=404, detail=f"no profile called {body.name!r}")
        rows = _rows(subject)
        try:
            scope_ = profile_scope(prof, rows, subject, {attach.WORKSPACE_VAR: body.workspace})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if prof.get("variables") and body.workspace:
            require_in_ceiling(request, scope_[0] or None)
            if scope_[0] and not _row_for(rows, scope_[0]):
                raise HTTPException(status_code=404, detail=f"no workspace called {body.workspace!r}")
        apply_profile(sess, subject, session, prof["name"], scope_)
        view = _view(request, subject, session, include_hidden=False)
        if body.new_chat:
            return {"profile": prof["name"], "chat": view["chat"], "line": view["line"],
                    "ui_action": "open_chat", "session": session,
                    "say": f"A new {prof['name']} chat is open. Say so in one line."}
        if prof.get("incognito"):
            # SAY FROM WHEN (founder 2026-10-14): what changes at the next message, and what that
            # means for the messages already in this chat.
            days = _retention_days(settings, wsr.root)
            return {"profile": prof["name"], "chat": view["chat"], "line": view["line"],
                    "say": ("This chat is now incognito. From your next message on, nothing is kept: no "
                            "workspace is mounted or written, nothing goes to memory, and no other chat or "
                            f"search sees it. The whole chat, earlier messages included, is deleted after "
                            f"{days} days. Say that in one line.")}
        return {"profile": prof["name"], "chat": view["chat"], "line": view["line"],
                "say": f"This chat now uses the {prof['name']} profile. Now: {view['line']}."}

    @router.post("/api/workspace/profiles/edit")
    def ws_profile_edit(request: Request, body: ChatProfileEditBody = Body(...)):
        """CHANGE A PROFILE when the person says so — "my work profile writes to Acme and reads Acme
        and Personal", "start new chats in study". Existing chats keep what they have."""
        subject = str(subject_of(request))
        require_in_ceiling(request, *[w for w in [body.target, *(body.reads or [])]
                                      if w and w.strip().casefold() not in (DESK, "desk")
                                      and not w.strip().startswith("{{")])
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="profiles are the person's to change")
        rows = _rows(subject)
        kw = {}

        def _ref(w: str) -> str:
            # a VARIABLE ("{{workspace}}") is kept as written: it is bound when the profile is used
            w = str(w or "").strip()
            return w if re.fullmatch(r"\{\{[a-z_]+\}\}", w) else _resolve(rows, subject, w)
        if body.target is not None:
            kw["target"] = _ref(body.target)
        if body.reads is not None:
            kw["reads"] = [_ref(w) for w in body.reads]
        try:
            prof = attach.edit_chat_profile(wsr.root, subject, body.name, default=body.default, **kw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"profile": prof, "say": f"The {prof['name']} profile is updated."}

    @router.post("/api/workspace/defaults")
    def ws_defaults(request: Request, body: WorkspaceDefaultsBody = Body(...)):
        """SET WHAT NEW CHATS START WITH when the person asks — "new chats should write to Example
        Bank", "new chats read only Personal and finos". `target` must be a workspace they can write
        ("personal" for their own). `active` lists exactly the workspaces new chats read; every other
        one that is not hidden becomes inactive (still listed, not mounted). Existing chats keep
        theirs. Say what changed in one line."""
        subject = str(subject_of(request))
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="defaults are the person's to change")
        if global_layer.is_admin_account(settings, subject):
            raise HTTPException(status_code=403, detail="the admin account has one workspace, Company")
        rows = _rows(subject)
        said = []
        if body.target is not None:
            t = _resolve(rows, subject, body.target) if body.target.strip() else DESK
            wid = "" if t == DESK else t
            require_in_ceiling(request, wid or None)
            if wid:
                if not _is_slug(wid):
                    raise HTTPException(status_code=400, detail="not a workspace slug")
                if next((r for r in rows if r["slug"] == wid), {}).get("state") == "hidden":
                    raise HTTPException(status_code=409, detail="that workspace is hidden — unhide it first")
                try:
                    _read_target(request, wid, write=True)
                except HTTPException:
                    raise
                except Exception:  # noqa: BLE001
                    raise HTTPException(status_code=403, detail="not a workspace you can write")
            sess.set_default_target(subject, wid)
            said.append(f"new chats write to {_name(rows, t) if t != DESK else 'Personal'}")
        if body.active is not None:
            want = {_resolve(rows, subject, w) for w in body.active}
            require_in_ceiling(request, *[w for w in want if w != DESK])
            for r in rows:
                if r.get("state") == "hidden" and r["slug"] not in want:
                    continue
                st = "active" if r["slug"] in want else "inactive"
                if r.get("state") != st:
                    if st == "active" and r["kind"] == "shared":
                        try:
                            membership_mod.require_role(wsr.root, r["slug"], subject, "viewer")
                        except MembershipError:
                            continue
                    try:
                        attach.set_workspace_state(wsr.root, subject, r["slug"], st)
                    except (KeyError, ValueError):
                        continue
            said.append("new chats read " + ", ".join(
                (_name(rows, s) if s != DESK else "Personal") for s in sorted(want)))
        if not said:
            raise HTTPException(status_code=422, detail="say a target, an active set, or both")
        if body.active is not None:
            try:
                sess.mark_all_stale(subject)
            except Exception:  # noqa: BLE001
                logger.warning("mounts not marked stale for subject=%s", subject)
        view = _view(request, subject, _session_of(request), include_hidden=False)
        return {"defaults": view["defaults"], "say": "Defaults set: " + "; ".join(said) + "."}

    return router
