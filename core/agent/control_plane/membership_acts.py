"""membership_acts.py — ADDING A MEMBER IS A CONVERSATION (Vexa-ai/vexa#1632).

Founder, 2026-09-06, pressing **Add a member…** on a workspace front page and reading
``invite role must be one of ('contributor',)`` back: *"this add member should just ask chat to do
that with mcp, asking their emails etc."* — and, a minute later, *"so we do not have to create UI
here — button to trigger the chat."*

So there is no form. The front page's three controls queue an ACT on the workspace's chat; the agent
asks for the address and the role in ONE question, confirms in ONE sentence, and then calls a verb.
This module is what the verb reaches: the two acts, and the gate in front of both.

WHY A MODULE AND NOT TWO ROUTE BODIES. Everything here is a decision — who may act, what an address
resolves to, whether a link is handed over or mailed — and every one of them has to be provable
without a running app. The routes in ``routers/workspaces.py`` are twelve lines each on top of this;
the reasoning is here, where a test can drive it with a directory and three callables.

── THE GATE, AND WHY IT IS THREE BRANCHES ───────────────────────────────────────────────────────
``assert_may_manage`` is the whole of the issue's "owner-only on the server side, same check as the
route; refused on ``_system``; on ``_global`` admin-only". They are three branches because the three
tiers are three different KINDS of thing and not three settings of one:

  * a **group** has members, so membership is a question about it, and the answer is its owner's —
    the same ``require_role(..., "owner")`` the existing role/remove routes already use. Identical
    check, not a similar one: a permission that exists twice is a permission that will disagree
    with itself.
  * ``_system`` is *"the one genuinely private tier"* (``behavior/global/POLICIES.md``), and no rule
    on that page can widen it. Not admin-only: **nobody**, including the administrator.
  * ``_global`` is the company layer, and its writers are a NAMED SET in ``POLICIES.md``
    (``global_admin_only``), not a membership store. Admin-only at the gate, and then refused with
    that sentence — because granting somebody ``contributor`` on ``_global`` would write a record
    that authorises nothing (the `_global` mount is read-only except in the admin's own session) and
    tell the person who asked that it did. A control whose only outcome is a lie is worse than one
    that refuses, which is this panel's own rule 1 read one layer down.

── WHAT THE ACTS ACTUALLY DO ────────────────────────────────────────────────────────────────────
``invite`` mints the invite the existing ``POST /api/workspace/invites`` route mints — the same
``mint_invite``, the same ``policy/invites.json``, the same hash-only storage — and then answers the
question that route never had to: **this invite is for a named person, so how does it reach them?**

  * an address this instance already knows (``resolve_subject`` answers) is INTERNAL: the link is
    handed straight back to the agent, which gives it to the person in the chat they are already in.
    Mailing somebody who is signed in on the other side of the same screen is a worse product.
  * every other address is EXTERNAL, and the invite is published onto flows as ``workspace.invited``
    for the mail carrier to send (``core/flows`` — ``mail_workspace_invite``). A publish is not a
    dependency (``control_plane/publish.py``), so a deployment with no flows domain is not a failure
    here: it is a deployment where the link comes back to be handed over, and the answer SAYS that
    rather than reporting a mail nobody sent.

``set_membership`` is the other two buttons in one verb, because they are one decision with three
answers: this person is an owner, a contributor, a reader, or not here. ``role="remove"`` is the
fourth, spelled as a role because that is how a person says it — *make them a reader*, *remove them*
— and because two verbs would mean the agent has to pick one before it has asked the question.

── THE ADDRESS IS THE HANDLE, AND THAT IS THE POINT ─────────────────────────────────────────────
Every existing membership route takes a ``subject``: the opaque platform id. Right for a panel that
just read the roster and holds the id; useless to an agent whose person said a name out loud. So
both acts take an EMAIL and resolve it in two steps — the workspace's own roster first
(``member_by_email``), identity second (``resolve_subject``) — and refuse rather than guess when
neither answers. A membership granted to an address nobody can resolve is a row that grants nothing
to nobody.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional

from control_plane import workspace_membership as membership_mod
from control_plane.workspace_membership import (
    MembershipError, ROLE_WORDS, normalize_role, role_sentence, role_word)

log = logging.getLogger(__name__)

#: The private tier, in every spelling ``RESERVED_SLUGS`` holds for it. Refused for everyone.
SYSTEM_SLUGS = frozenset({"sys", "_system", "system"})

#: The company layer. Admin-only, and then refused — see the module header.
GLOBAL_SLUGS = frozenset({"_global", "global"})

#: WHY ``_system`` IS NOT ADMIN-ONLY. Straight out of ``behavior/global/POLICIES.md``: *"`_system`
#: is read by no agent for anybody else — chats, sessions and settings are the one genuinely private
#: tier, and no rule below can widen it."* An administrator is somebody, so this refusal is theirs
#: too.
SYSTEM_SENTENCE = ("the private tier is nobody's to share — chats, sessions and settings are the one "
                   "thing in this deployment with no members, and no rule can give it any")

#: WHY ``_global`` REFUSES EVEN THE ADMIN. Its writers are named in ``POLICIES.md``, and a membership
#: record on the company layer would authorise nothing.
GLOBAL_SENTENCE = ("the company layer's editors are named in `_global/POLICIES.md` under "
                   "`global_admin_only`, not invited — it is a file the administrator edits, and "
                   "every edit to it is a commit with an author. A membership record here would "
                   "grant nothing and say it had")

#: The one spelling of "take them off this workspace". A role, because that is how the question is
#: answered out loud: *owner, contributor, reader — or remove them*.
REMOVE = "remove"

#: What ``role`` may be on ``set_membership``.
SETTABLE = tuple(ROLE_WORDS) + (REMOVE,)

#: An address, checked only for the shape that makes it deliverable. Deliberately not RFC 5322: the
#: point is to refuse ``"jane"`` and ``"jane@"`` before an invite is minted against them, not to
#: adjudicate the grammar of the internet.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

#: How long an invite minted by a verb lives, and how many times it may be redeemed. One use,
#: because it names ONE person: an invite for jsmith that a second person redeems is the failure
#: ``restricted`` mode exists to make impossible, and a use count of one makes it impossible twice.
INVITE_MAX_USES = 1


class ActRefused(RuntimeError):
    """An act was refused for a reason a person can act on. Carries the status the route answers with,
    exactly as ``MembershipError`` does — same shape, so ``routers/workspaces.py`` needs no second
    translation table."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def normalize_address(email) -> str:
    """One address, lower-cased and trimmed, or ``ActRefused``. The only door an address comes
    through, so nothing below has to ask whether it was checked."""
    addr = str(email or "").strip().lower()
    if not addr or not _ADDRESS.match(addr):
        raise ActRefused(
            f"{email!r} is not an email address — ask them for the address they would sign in with",
            status=400)
    return addr


def assert_may_manage(root: Path, slug: str, subject: str, *, is_admin: bool) -> None:
    """May ``subject`` change who is in ``slug``? Raises ``ActRefused`` when not. See the header for
    why the three tiers answer differently."""
    target = str(slug or "").strip()
    if not target:
        raise ActRefused("name the workspace this is about", status=400)
    if target in SYSTEM_SLUGS or target.startswith("."):
        raise ActRefused(SYSTEM_SENTENCE, status=403)
    if target in GLOBAL_SLUGS:
        # ADMIN-ONLY FIRST, THEN REFUSED. The order is the whole distinction the issue asks for: a
        # person who is not the administrator is told they are not authorised, and the administrator
        # — who is — is told what the company layer actually does instead. Collapsing the two into
        # one refusal would teach the admin that they lack a permission they hold.
        if not is_admin:
            raise ActRefused("only this deployment's administrator manages the company layer",
                             status=403)
        raise ActRefused(GLOBAL_SENTENCE, status=409)
    try:
        membership_mod.require_role(root, target, subject, "owner")
    except MembershipError as exc:
        raise ActRefused(str(exc), status=exc.status) from exc


def _resolve(root: Path, slug: str, address: str,
             resolve_subject: Optional[Callable[[str], Optional[str]]]) -> tuple[str, Optional[dict]]:
    """``(subject, member_record)`` for an address — the roster first, identity second.

    The roster answers for somebody already here, which is the case both role acts are about, and it
    answers without leaving the process. Identity answers for everybody else, and is allowed to
    answer nothing: an address with no account on this instance is EXTERNAL, which is a state and
    not a failure."""
    record = membership_mod.member_by_email(root, slug, address)
    if record and record.get("subject"):
        return str(record["subject"]), record
    found = None
    if resolve_subject is not None:
        try:
            found = resolve_subject(address)
        except Exception as exc:  # noqa: BLE001 — an unreachable identity is "we do not know", not a 500
            log.warning("membership act: could not resolve %s (%s) — treating as external", address, exc)
            found = None
    return (str(found) if found else ""), record


def invite(root: Path, slug: str, *, email, role, inviter: str, index,
           ui_url: str, commit_fn=None,
           resolve_subject: Optional[Callable[[str], Optional[str]]] = None,
           mail: Optional[Callable[[dict], bool]] = None,
           inviter_email: str = "", workspace_name: str = "",
           now: Optional[float] = None) -> dict:
    """Invite ``email`` to ``slug`` as ``role``, and get it to them. The caller has already gated.

    Returns what it DID, in the shape the verb hands back to the agent, because the agent has to say
    it in one line and must not have to infer anything: who, where, as what, what that role means,
    and whether the link was mailed or is theirs to pass on."""
    address = normalize_address(email)
    lattice = normalize_role(role)
    word = role_word(lattice)
    if not (ui_url or "").strip():
        # THE SAME REFUSAL THE SCAFFOLD MINT MAKES, for the same reason (``routers/scaffolds.py``):
        # an invite whose link has no origin is a link to nowhere, and it is better to say so before
        # a token exists than to mail somebody a path.
        raise ActRefused("VEXA_UI_URL is not set on agent-api — an invite link would have no origin",
                         status=503)

    subject, existing = _resolve(root, slug, address, resolve_subject)
    if existing:
        # ALREADY HERE. Not an error and not a second invite: the honest answer is what they already
        # are, and the next move belongs to the person who asked — which is usually `Change role`.
        held = role_word(existing.get("role"))
        return {"workspace": slug, "email": address, "role": held,
                "role_sentence": role_sentence(held), "already_member": True, "invited": False,
                "delivery": "none",
                "said": f"{address} is already a member of {slug} — {held}: {role_sentence(held)}."}

    minted = membership_mod.mint_invite(
        root, slug, role=word, created_by=inviter,
        max_uses=INVITE_MAX_USES,
        # RESTRICTED, ALWAYS, BECAUSE THE ADDRESS WAS NAMED. An ``open`` invite is anyone-with-the-
        # link; this one was minted for one person a human typed, so a forwarded link grants nothing
        # (``accept_invite`` checks the redeemer's VERIFIED email against this list). The panel's old
        # button minted ``open`` because it had nobody in mind — that difference is the whole reason
        # this act exists.
        mode="restricted", allowed_emails=[address],
        commit_fn=commit_fn, now=now)
    # ONE COMPOSER (Vexa-ai/vexa#1635). This used to build `<ui>/?invite=<token>` here; the path
    # that serves an invite is `/join?i=<token>`, and it is `workspace_membership.invite_link` for
    # both callers — this act and the older mint route — so the link and the page cannot drift apart.
    link = membership_mod.invite_link(ui_url, minted.token)

    delivery = "link"
    mailed = False
    if not subject and mail is not None:
        # EXTERNAL: hand the fact to the carrier. `mail` returns whether it landed; a publish that
        # did not land is not an error (see `control_plane/publish.py` — a publish edge is not a
        # dependency), it just means the link is still ours to pass on, and the answer says so.
        mailed = bool(mail({
            "uid": str(inviter), "email": address, "workspace": slug,
            "workspace_name": str(workspace_name or slug), "role": word,
            "role_sentence": role_sentence(word),
            "inviter": str(inviter_email or inviter), "link": link,
            "expires_at": int(minted.expires_at), "invite_id": minted.id,
        }))
        delivery = "mailed" if mailed else "link"

    where = ("mailed to them" if mailed
             else "yours to give them — this deployment sends no mail" if not subject
             else "theirs to open — they already have an account here")
    return {
        "workspace": slug, "email": address, "role": word, "role_sentence": role_sentence(word),
        "already_member": False, "invited": True,
        "internal": bool(subject), "delivery": delivery, "link": link,
        "invite_id": minted.id, "expires_at": int(minted.expires_at),
        "said": f"Invited {address} to {slug} as a {word} — {role_sentence(word)}. The link is {where}.",
    }


def set_membership(root: Path, slug: str, *, email, role, actor: str, index, commit_fn=None,
                   resolve_subject: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """Change what ``email`` is in ``slug``, or take them off it. The caller has already gated.

    ``role`` is one of the three, or ``remove``. Both halves are here rather than in two verbs
    because they are one question — *what is this person here?* — and an agent that had to choose a
    verb before asking would have to guess the answer first."""
    address = normalize_address(email)
    asked = str(role or "").strip().lower()
    if asked not in SETTABLE:
        raise ActRefused(
            f"{role!r} is not something a member can be — " + ", ".join(ROLE_WORDS)
            + f", or {REMOVE}. " + "; ".join(f"{r}: {role_sentence(r)}" for r in ROLE_WORDS),
            status=400)

    subject, existing = _resolve(root, slug, address, resolve_subject)
    if not existing and subject:
        # The roster had no email for them — an older record, granted before emails were stored — so
        # ask it again by subject before deciding they are not here. ``backfill_member_email`` fills
        # that gap when they next open the panel; this act must not depend on their having done so.
        held = membership_mod.is_member(root, slug, subject)
        existing = {"subject": subject, "role": held} if held else None
    if not existing:
        raise ActRefused(
            f"{address} is not a member of {slug} — invite them before changing what they are",
            status=404)
    subject = str(existing.get("subject") or subject)
    if not subject:
        raise ActRefused(f"{address} is on the roster of {slug} with no id behind it — that record "
                         f"cannot be changed from here", status=409)

    try:
        if asked == REMOVE:
            membership_mod.remove_member(root, slug, subject, index=index, commit_fn=commit_fn)
            return {"workspace": slug, "email": address, "subject": subject, "removed": True,
                    "role": None, "role_sentence": "",
                    "said": f"Removed {address} from {slug}."}
        record = membership_mod.set_role(root, slug, subject, asked, changed_by=actor,
                                         index=index, commit_fn=commit_fn)
    except MembershipError as exc:
        # THE LAST-OWNER REFUSAL REACHES THE PERSON AS ITSELF (409). It is the one refusal here that
        # is about the workspace rather than about them, and rewriting it into a generic failure
        # would leave somebody trying the same thing again.
        raise ActRefused(str(exc), status=exc.status) from exc
    word = role_word(record.get("role"))
    return {"workspace": slug, "email": address, "subject": subject, "removed": False,
            "role": word, "role_sentence": role_sentence(word),
            "said": f"{address} is now a {word} in {slug} — {role_sentence(word)}."}
