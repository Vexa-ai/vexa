"""crm-api: the CRM's one door. The gateway MCP assembles its tools from this OpenAPI (ADR-0047 d8).

Every person-facing route resolves the caller through identity (`caller.resolve`): who they are,
their live workspace memberships narrowed to a worker's ceiling, and whether the turn is one other
people read. Every route is audited with the caller's kind and the session's trace. The internal
routes (`/internal/*`) take the internal tier's secret and act for nobody: they carry facts in
(a workspace's references at a commit, an account that signed in), never answers out.
"""
import dataclasses
import hmac
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import create_engine

from . import blocks as blocks_mod
from . import caller as caller_mod
from . import features
from . import privacy
from . import profiles as profiles_mod
from .caller import Caller
from .orgs import PERSONAL_ORG, OrgResolver
from .card_ops import Cards, PolicyText
from .desk import Desk
from .member_settings import MemberSettings
from .policies import DOOR_ENV, Policies
from .persons import PersonError, PersonRefused
from .store import CRMError, PENDING_AUDIT, Refused, SharedRefused, Store, check_workspace

TRACE_HEADER = 'x-trace-id'


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Describe(Body):
    object_type: str = Field(default='', description='Optional object type; omit to discover permitted object types.')
class Search(Body):
    object_type: str
    filters: dict = Field(default_factory=dict, description='Equality filters on readable fields; discover field names with crm_describe.')
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
class Resolve(Body):
    name: str = Field(min_length=1, max_length=300)
class Read(Body):
    record_id: str
class BlockRead(Body):
    workspace: str = Field(default='', max_length=128, description="The page's workspace: which fields it may see too.")
    view: str = Field(max_length=20, description='company · person · people · deals · timeline')
    args: dict = Field(default_factory=dict, description="The block's own arguments (company=, stage=, person=, limit=).")
class Change(Body):
    action: Literal['create','update','propose','merge']
    idempotency_key: str = Field(default='', max_length=200, description='Required for create: stable caller-generated key reused when retrying the same request.')
    object_type: str = ''
    record_id: str = ''
    into: str = Field(default='', description='For merge: the record that survives. The merged record_id then answers this one everywhere.')
    expected_revision: int = Field(default=0, ge=0)
    fields: dict = Field(default_factory=dict)
    match: list[str] = Field(default_factory=list, max_length=20, description='For create: match identifiers — email:a@b.c, domain:b.c, calendar:<event id>. One identifier belongs to one record; a clash means search or merge, never a second record.')
    narrative: str | None = Field(default=None, description="Native Markdown description of this CRM record. Supports headings, lists, tables, workspace links and stable [label](/crm?record=UUID) links. Stable CRM links are indexed as relationships with backlinks. Search for mentioned people, companies and products first and link verified matches by their href; never invent identities, facts or URLs. Null preserves the description; empty string clears it. Requires current revision and reason.")
    reason: str = Field(min_length=1)
    evidence: list[dict] = Field(default_factory=list)
class Configure(Body):
    object_type: str
    layout: dict = Field(default_factory=dict, description="Card layout: {title_field: 'Name', sections: [{title: 'Overview', fields: [{field: 'Amount', label: 'Investment', format: 'currency', currency: 'USD'}]}], show_narrative: true, show_related: true}. Field formats: text, markdown, number, currency, percent (0..100), date, badge.")
    expected_version: int = Field(default=0, ge=0)
    reason: str = Field(default='', max_length=2000)
class Review(Body):
    proposal_id: str
    accept: bool
class Bind(Body):
    workspace: str = Field(min_length=1, max_length=128, description='Workspace slug whose members get derived grants on the record.')
    record_id: str
    bound: bool = Field(default=True, description='false removes the binding.')
class ProfileLink(Body):
    record_id: str
    handle: str = Field(default='', max_length=64, description="the profile's opaque handle")
    linked: bool = Field(default=True, description='false removes the link')
class Reference(Body):
    page: str = Field(min_length=1, max_length=500)
    record: str = Field(min_length=1, max_length=100)
class Member(BaseModel):
    subject: str
    role: str
class Snapshot(Body):
    workspace: str
    commit: str = Field(min_length=1)
    committed_at: str
    references: list[Reference] = Field(default_factory=list, max_length=20000)
    members: list[Member] | None = None
class OfferSpec(Body):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal['commitment', 'question', 'approval', 'ask', 'invitation', 'policy_change']
    next_actor: str = Field(min_length=1, max_length=128)
    requester: str = Field(default='', max_length=128)
    due: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    about: str | None = Field(default=None, max_length=600)
class MeetingOffer(Body):
    principal: str = Field(min_length=1, max_length=128, description="The room's workspace principal id (identity).")
    workspace: str | None = Field(default=None, max_length=128)
    on_behalf_of: str = Field(min_length=1, max_length=128, description='The person the run answers (the meeting owner).')
    session: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)
    cards: list[OfferSpec] = Field(min_length=1, max_length=20)
class Account(Body):
    subject: str = Field(min_length=1)
    email: str = ''
class Accounts(Body):
    accounts: list[Account] = Field(min_length=1, max_length=500)
class PersonMe(Body):
    pass
class PersonProfile(Body):
    action: Literal['read', 'edit', 'claim', 'unclaim', 'publish', 'import']
    fields: dict = Field(default_factory=dict, description='edit: profile fields (display_name, photo, headline, company, bio, location, timezone, links, contact). An empty value removes one.')
    record_id: str = Field(default='', max_length=100, description='claim: the record picked on the claim card; edit: an unclaimed profile (the org edits it).')
    public: bool = Field(default=False, description='publish: switch the public page on or off.')
    public_fields: list[str] | None = Field(default=None, max_length=20, description='publish: the profile fields the public page shows.')
    source: str = Field(default='', max_length=40, description='import: a registered importer (none ships).')
class Handle(Body):
    handle: str = Field(min_length=1, max_length=64)
class PersonMerge(Body):
    action: Literal['confirm', 'decline', 'primary', 'offer']
    card: str = Field(default='', max_length=100, description='The merge card id (confirm, decline).')
    primary: str = Field(default='', max_length=32, description="An address option from the card: where invites and nudges go.")
    address: str = Field(default='', max_length=300, description='offer: another address that is yours.')


# ── the desk (desktop phase D1) ──────────────────────────────────────────────────────────────
OPS_DESCRIPTION = (
    "1..50 declarative operations on the person's own desktop, applied in order; a bad one refuses the "
    "whole list. Sections: workspaces, agents, files, cards. Refs: agent:<routine id>, chat:<chat session id> "
    "(a chat, drawn in agents), "
    "file:<workspace slug>/<path>, workspace:<slug> (ordering only; workspaces are never placed). "
    "{op:'order',section,ids:[refs]} · {op:'move',id,before|after} (a ref within its section, or a section id "
    "among sections) · {op:'group',section,by:'workspace'|'type'|'state'|'about'} or "
    "{op:'group',section,named:[{title,ids}]} · {op:'show'|'hide'|'collapse'|'expand'|'section_top',section} · "
    "{op:'section_order',sections:[…]} · {op:'section_place',section,column:1..3,span?:1..3,side_by_side_with?} · "
    "{op:'section_size',section,size:'compact'|'normal'|'tall'} · {op:'place'|'remove',ref} (an agent, a chat or a file "
    "this person can read; it never creates, edits or shares anything) · "
    "{op:'filter',section,where:'<predicate>'|null,until?:'<ISO time>'} · {op:'save',name} · {op:'restore',name}.")


class DeskRead(Body):
    pass
class DeskArrange(Body):
    ops: list[dict] = Field(min_length=1, max_length=50, description=OPS_DESCRIPTION)
    base_version: int = Field(ge=0, description='The version desktop_read returned. A different current version is refused with desk_changed: read again.')
    name: str | None = Field(default=None, max_length=80, description='Optional: save the resulting arrangement under this name, to restore later.')
class DeskPropose(Body):
    ops: list[dict] = Field(min_length=1, max_length=50, description=OPS_DESCRIPTION)
    reason: str = Field(min_length=1, max_length=500, description='One line the person reads beside Apply / Not now: why this arrangement helps them.')
class DeskProposalReview(Body):
    proposal_id: str = Field(min_length=1, max_length=100)
    apply: bool
    base_version: int = Field(ge=0)
class DeskUndo(Body):
    version: int = Field(ge=0, description='The version to restore (undo_version from the last change); 0 is the default desktop.')
    base_version: int = Field(ge=0)


# ── workspace settings: the person's own, on the CRM `member_of` relation (member_settings.py) ──
class WorkspaceSettingsRead(Body):
    pass
class SettingsPatch(Body):
    pinned: StrictBool | None = Field(default=None, description='Pin the workspace to the top of the home.')
    hidden: StrictBool | None = Field(default=None, description="Hide it from the person's home (it stays open to them).")
    muted: StrictBool | None = Field(default=None, description='Mute it: no notifications from it.')
    notify: Literal['all', 'mentions', 'none'] | None = Field(default=None, deprecated=True, description='RETIRED: refused. The notification level lives in the person\'s identity notification policy (Settings → Notifications).')
    room_order: list[str] | None = Field(default=None, max_length=100, description="Chat ids in the person's order (at most 100).")
    level: StrictInt | None = Field(default=None, ge=1, le=5, description="The person's level override for this workspace, 1..5; null follows their default.")
    collapsed: list[str] | None = Field(default=None, max_length=20, description='Section ids the person collapsed (at most 20).')
class WorkspaceSettingsSet(Body):
    workspace: str = Field(min_length=1, max_length=128, description="The workspace slug; 'personal' is the person's own.")
    settings: SettingsPatch = Field(description='Only the keys to change; the rest stay as they are.')


# ── cards (desktop phase D2) ─────────────────────────────────────────────────────────────────
ABOUT_DESCRIPTION = ('What the card is about, as a reference: page:<workspace>/<path>, chat:<id>, workspace:<slug>, '
                     'meeting:<id> or crm:<record id>. Members of the card never get access to it through the card.')
FIELDS_DESCRIPTION = ("Optional card fields: Next (what happens next, <=200), Waiting (who or what it waits on), Summary "
                      "(<=500), Due (YYYY-MM-DD), Progress (0..1), Checklist ([{text, done}] <=10), Title. Never State: "
                      "a stage moves with `transition`. AN OBLIGATION (D11): Kind (commitment | approval | question | ask | "
                      "invitation | policy_change) with NextActor (the person id whose move it is — required with a Kind), "
                      "Requester (the person id waiting on it; defaults to the person you act for), Due, Origin "
                      "(session:<id> or message:<session>/<message id>) and Chain ({blocks: [card ids], blocked_by: [card ids]}). "
                      "A card with a Kind is OFFERED to its next actor at once and only that person moves it.")
FACE_DESCRIPTION = ("Optional face: up to 8 blocks. Structural: {type: stepper|people|movement|waiting|next|subcards|ref|status}. "
                    "Data blocks NAME a field and never carry a value: {type: metric|text|progress|due|checklist|list, "
                    "field: '<card field>', label?: '<=40 chars'}. To say something on the face, put it in a field "
                    "(Next, Summary) and show that field. Unknown blocks are dropped.")


class CardList(Body):
    workspace: str | None = Field(default=None, max_length=128, description="A workspace's board: the cards it references.")
    about: str | None = Field(default=None, max_length=600, description='Only cards about this reference.')
    state: str | None = Field(default=None, max_length=60, description='Only cards in this stage.')
    mine: bool = Field(default=False, description='Only cards the person is a member of.')
    limit: int = Field(default=50, ge=1, le=50)
class CardPropose(Body):
    title: str = Field(min_length=1, max_length=200)
    workspace: str | None = Field(default=None, max_length=128, description="The workspace whose board it goes on — one you work in. Omit for the person's own card.")
    about: str | None = Field(default=None, max_length=600, description=ABOUT_DESCRIPTION)
    type: str = Field(default='Card', max_length=120, description='A card type; Card unless the company declared others.')
    parent: str | None = Field(default=None, max_length=128, description='Make it a sub-card of this card id.')
    fields: dict = Field(default_factory=dict, description=FIELDS_DESCRIPTION)
    face: list[dict] | None = Field(default=None, max_length=20, description=FACE_DESCRIPTION)
    reason: str = Field(min_length=1, max_length=500, description='One line the person reads beside Add / Not now: why this card.')
class CardUpdatePropose(Body):
    card: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1, description='The revision card_list or the card returned.')
    fields: dict = Field(default_factory=dict, description=FIELDS_DESCRIPTION)
    face: list[dict] | None = Field(default=None, max_length=20, description=FACE_DESCRIPTION)
    transition: str | None = Field(default=None, max_length=60, description="Move the card to this stage of its lifecycle (one of the card's states or side_states). Always a proposal a person accepts.")
    reason: str = Field(min_length=1, max_length=500, description='One line the person reads: why this change.')
class CardRead(Body):
    card: str = Field(min_length=1, max_length=128)
class CardCreate(Body):
    title: str = Field(min_length=1, max_length=200)
    workspace: str | None = Field(default=None, max_length=128)
    about: str | None = Field(default=None, max_length=600)
    type: str = Field(default='Card', max_length=120)
    parent: str | None = Field(default=None, max_length=128)
    fields: dict = Field(default_factory=dict)
    face: list[dict] | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=20000)
class CardUpdate(Body):
    card: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    fields: dict = Field(default_factory=dict)
    face: list[dict] | None = Field(default=None, max_length=20)
    transition: str | None = Field(default=None, max_length=60)
class CardMembers(Body):
    card: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128, description='The person id to add (invited) or remove.')
    add: bool = True
class CardMembership(Body):
    card: str = Field(min_length=1, max_length=128)
    accept: bool
class CardInbox(Body):
    pass
class CardRef(Body):
    card: str = Field(min_length=1, max_length=128)
class CardDecide(Body):
    card: str = Field(min_length=1, max_length=128)
    action: Literal['acknowledge', 'decline', 'deliver', 'confirm', 'decide', 'execute', 'answer', 'use']
    nonce: str = Field(min_length=16, max_length=100, description='From POST /card/nonce, minted for this person and this face.')
    content_hash: str = Field(min_length=64, max_length=64, description='The content hash /card/nonce answered: the face the person saw.')
    evidence: list[str] | None = Field(default=None, max_length=10, description='deliver: references the delivery cites.')
    receipt: str | None = Field(default=None, max_length=300, description="execute: the effect receipt reference.")
    answer: str | None = Field(default=None, max_length=500, description='answer: the answer.')
    reason: str | None = Field(default=None, max_length=500)


class Identity:
    """Resolves a bearer through identity's `/internal/validate` — the one resolver."""
    def __init__(self, base, secret, transport=None):
        self.base, self.secret, self.transport = base.rstrip('/'), secret, transport

    def __call__(self, request: Request) -> Caller:
        token = request.headers.get('x-api-key', '')
        if not token:
            scheme, _, value = request.headers.get('authorization', '').partition(' ')
            token = value if scheme.lower() == 'bearer' else ''
        if not token:
            raise HTTPException(401, 'Caller credential required')
        try:
            return caller_mod.resolve(self.base, self.secret, token, trace=request.headers.get(TRACE_HEADER, ''),
                                      transport=self.transport)
        except caller_mod.SubjectUnknown as e:
            raise HTTPException(401, 'Invalid caller credential') from e
        except caller_mod.IdentityUnavailable as e:
            raise HTTPException(503, 'Identity service unavailable') from e


def create_app(store: Store, identity, org_id: str, internal_secret: str = '', limits=None, policy_text=None,
               profiles=None, org_resolver=None):
    if not org_id or not org_id.strip():
        raise ValueError("CRM_ORG_ID is required")
    app = FastAPI(title='Vexa CRM', version='0.13.3')
    # the desk's limits: the company's `_global/POLICIES.md` (policies.py), the defaults without one
    # cards (desktop D2): lifecycles from the same `_global/POLICIES.md`, the same limits as the desk
    cards = Cards(store, policy_text=policy_text, limits=limits) if store is not None else None
    desk = Desk(store, limits=limits, cards=cards) if store is not None else None
    member_settings = MemberSettings(store) if store is not None else None

    @app.middleware('http')
    async def feature_switch(request, call_next):
        # VEXA_FEATURES (features.py): a release install does not serve the unproven CRM. Its rows
        # stay; every route but liveness, the manifest and the OpenAPI refuses before it authenticates.
        # The desk rides `desktop` as well, and needs both.
        if request.url.path.startswith(features.CARD_PREFIX) and not features.cards_served():
            return JSONResponse(status_code=403, content={
                'detail': features.refusal(features.CARDS, features.CARDS_SUMMARY)})
        if request.url.path.startswith(features.DESK_PREFIX) and not features.desk_served():
            return JSONResponse(status_code=403, content={
                'detail': features.refusal(features.DESKTOP, features.DESKTOP_SUMMARY)})
        # erasure is never switched off: an account's personal CRM must go when the account does
        if request.url.path not in features.ALWAYS_SERVED and not features.ERASE_PATH.match(request.url.path) \
                and not features.switched_on():
            return JSONResponse(status_code=403, content={'detail': features.refusal()})
        # R4 public profiles (profiles.py): explicit — on only when VEXA_FEATURES names it
        if request.url.path.startswith(profiles_mod.PREFIX) and not profiles_mod.switched_on():
            return JSONResponse(status_code=403, content={
                'detail': features.refusal(profiles_mod.FEATURE, profiles_mod.SUMMARY)})
        return await call_next(request)

    @app.middleware('http')
    async def privacy_label(request, call_next):
        # D6: every CRM answer is the person's private data until the field classes land (S1-C):
        # the gateway/MCP edge marks a worker's session from this label (`privacy.py`).
        response = await call_next(request)
        if privacy.LABEL_HEADER not in response.headers:
            response.headers[privacy.LABEL_HEADER] = privacy.CRM_LABEL
        return response

    @app.middleware('http')
    async def check_binding(request, call_next):
        binding = request.headers.get('x-crm-org')
        if binding is not None and binding != org_id:
            return JSONResponse(status_code=403, content={'detail': 'CRM instance binding mismatch'})
        return await call_next(request)

    @app.exception_handler(CRMError)
    async def error(request, exc):
        if isinstance(exc, (Refused, SharedRefused)):
            return JSONResponse(status_code=exc.status, content=exc.body)
        return JSONResponse(status_code=exc.status, content={'detail': str(exc)})

    @app.exception_handler(PersonError)
    async def person_error(request, exc):
        if isinstance(exc, PersonRefused):
            return JSONResponse(status_code=exc.status, content=exc.body)
        return JSONResponse(status_code=exc.status, content={'detail': str(exc)})

    # Which CRM org serves the caller (orgs.py): the install org, unless `personal_crm` is on and
    # identity says the caller is an individual with an org of one — their personal CRM.
    resolver = org_resolver if org_resolver is not None else OrgResolver(org_id)
    if profiles is not None and hasattr(profiles, 'legacy'):
        # D35 retirement: identity's old profile hands its values to the claimed profile, once
        store.profiles.legacy = profiles.legacy

    def session_person(request: Request) -> Caller:
        """D13 (deny test 12): a decision — review, a card's gate, the seen relation, a decision nonce —
        takes the terminal's SESSION credential: no API key (a browser-scoped one included), no
        delegation token. Refused before anything is read."""
        caller = person(request)
        if caller.delegated or caller.credential_class != 'session':
            raise Refused(caller_mod.SESSION_REFUSAL)
        return caller

    def person(request: Request) -> Caller:
        caller = identity(request)
        try:
            caller = dataclasses.replace(caller, org=resolver.org_for(caller))
        except caller_mod.IdentityUnavailable as e:
            raise HTTPException(503, 'Identity service unavailable') from e
        try:
            store.ensure_account(caller.org, caller)    # ADR-0047 d3: attached on sign-in
        except CRMError:
            pass                                          # a clash is the admin's to merge, not a 500
        try:
            # D35: the one record matching the verified address is claimed as this person's profile,
            # and identity's retired profile's entries move into it (once, while it is empty)
            with store.engine.begin() as c:
                claimed = store.profiles.auto_claim(c, caller.org, caller)
            if claimed:
                store.profiles.carry_legacy(caller.org, caller.subject, claimed)
        except Exception:                                 # noqa: BLE001 — a claim is never a 500
            pass
        return caller

    def audited(op, caller, record_id, call):
        """H9: every route is audited with the caller's kind, principal, the person acted for and the
        workspace. A write path writes its row IN ITS OWN TRANSACTION (``Store.begin`` reads the pending
        row), so no write commits without one; a read path, which commits nothing, writes it here
        before the answer leaves, so no answer is returned without one. A refusal is audited too."""
        org = caller.org or org_id
        pending = {'org': org, 'caller': caller, 'op': op, 'record_id': record_id or ''}
        token = PENDING_AUDIT.set(pending)
        try:
            try:
                result = call()
            except CRMError as exc:
                store.audit(org, caller, op, record_id, type(exc).__name__)
                raise
            if not pending.get('written'):
                store.audit(org, caller, op, record_id or returned_ids(result), 'ok')
            return result
        finally:
            PENDING_AUDIT.reset(token)

    def returned_ids(result):
        """The record ids an answer carries, comma-joined (H9: a search names what it returned)."""
        if not isinstance(result, dict):
            return ''
        if result.get('record_id'):
            return str(result['record_id'])
        ids = [str(r.get('id')) for key in ('records', 'cards', 'rows') for r in (result.get(key) or [])
               if isinstance(r, dict) and r.get('id')]
        return ','.join(ids)

    def internal(request: Request):
        presented = request.headers.get('x-internal-secret', '')
        if not internal_secret or not presented or not hmac.compare_digest(presented, internal_secret):
            raise HTTPException(403, 'Internal credential required')
        return Caller(subject='', internal=True, trace=request.headers.get(TRACE_HEADER, ''))

    @app.get('/health')
    def health():
        return {'status': 'ok', 'domain': 'crm'}

    @app.get('/.well-known/mcp-tools.json')
    def manifest():
        doc = json.loads((Path(__file__).resolve().parents[2] / 'mcp.tools.v1.json').read_text())
        if not features.switched_on():
            doc['tools'] = []       # switched off: the MCP edge assembles no crm_* tool
        elif not features.desk_served():
            # the desk's tools, and every other tool on a /desk/ route (workspace settings)
            doc['tools'] = [t for t in doc['tools'] if not t['name'].startswith(features.DESK_TOOL_PREFIX)
                            and not t['route']['path'].startswith(features.DESK_PREFIX)]
        if not features.cards_served():
            doc['tools'] = [t for t in doc['tools'] if not t['name'].startswith(features.CARD_TOOL_PREFIX)]
        return doc

    @app.post('/describe', operation_id='crm_describe')
    def describe(body: Describe, caller=Depends(person)):
        """Discover the CRM's record types (Person, Company, Deal, Project and any imported ones) and the fields you may read. The organisation is fixed by the server; never ask the person to choose one. Use before filtering or writing unfamiliar fields."""
        return audited('describe', caller, '', lambda: store.describe(caller.org, caller, body.object_type or None))

    @app.post('/search', operation_id='crm_search')
    def search(body: Search, caller=Depends(person)):
        """Find CRM records you may read, with equality filters and pagination. Each result has a stable id, revision and href. Link an entity in a workspace page with its href ([Name](/crm?record=<id>)) instead of copying its fields into the page. In a conversation other people read, only records every participant can read are listed."""
        return audited('search', caller, '', lambda: store.search(caller.org, caller, body.object_type, body.filters, body.limit, body.offset))

    @app.post('/resolve')
    def resolve_name(body: Resolve, caller=Depends(person)):
        return audited('resolve', caller, '', lambda: store.resolve_name(caller.org, caller, body.name))

    @app.post('/read', operation_id='crm_read')
    def read(body: Read, caller=Depends(person)):
        """Read one record: permitted fields, sources, links, pending proposals, the workspaces bound to it, and 'mentioned_in' — the pages that link it, only from workspaces you are a member of. A merged id answers the surviving record. If this is refused with reason shared_conversation, say you cannot use it here and offer the person's private chat. Read before changing a record."""
        return audited('read', caller, body.record_id, lambda: store.get(caller.org, caller, body.record_id))

    @app.post('/change', operation_id='crm_change')
    def change(request: Request, body: Change, caller=Depends(person)):
        """Create a record, change one, propose a change, or merge a duplicate into the record that survives. As an agent your changes are PROPOSALS: the result says status 'proposed', and a person accepts or rejects them on the record page — tell them it is waiting and give the record link. Updates need the revision crm_read returned; include a reason and source evidence (a meeting id, a page). Create needs an idempotency_key."""
        # D6: a CRM write is read by the org — a marked session's write is held for the person.
        privacy.hold(request, caller, surface='crm_write')
        if body.action == 'create':
            if not body.idempotency_key:
                raise HTTPException(400, 'Create requires an idempotency_key')
            return audited('create', caller, '', lambda: store.create(caller.org, caller, body.object_type, body.fields, body.narrative or '',
                                                                    body.reason, body.evidence, body.idempotency_key, body.match))
        if body.action == 'merge':
            if not body.into:
                raise HTTPException(400, 'Merge requires into')
            return audited('merge', caller, body.record_id, lambda: store.merge(caller.org, caller, body.record_id, body.into, body.reason))
        return audited(body.action, caller, body.record_id, lambda: store.change(
            caller.org, caller, body.record_id, body.expected_revision, body.fields, body.narrative, body.reason,
            body.evidence, body.action == 'propose'))

    @app.post('/history', operation_id='crm_history')
    def history(body: Read, caller=Depends(person)):
        """Read up to 100 recent revisions of a record. Your current access filters historical values too."""
        return audited('history', caller, body.record_id, lambda: store.history(caller.org, caller, body.record_id))

    @app.post('/review', operation_id='crm_review')
    def review(body: Review, caller=Depends(session_person)):
        """Accept or reject a pending proposal — for a person with review rights using their own credential. An agent is refused with reason person_confirmation_required: point the person at the record page instead. Acceptance conflicts if the record changed since the proposal."""
        return audited('review', caller, body.proposal_id, lambda: store.review(caller.org, caller, body.proposal_id, body.accept))

    @app.post('/configure', operation_id='crm_configure')
    def configure(body: Configure, caller=Depends(person)):
        """Read or configure a shared per-type card layout (CRM administrators). Omit layout to read its version; send a layout with expected_version and reason to save. Sections reference real fields; formats are text, markdown, number, currency, percent, date or badge. This changes presentation, never record values or access."""
        return audited('configure', caller, '', lambda: store.configure(caller.org, caller, body.object_type, body.layout or None, body.expected_version, body.reason))

    @app.post('/bind', operation_id='crm_bind')
    def bind(body: Bind, caller=Depends(person)):
        """Bind a workspace to a record, or remove the binding (CRM administrators). Members of a bound workspace may then read the record, and its contributors and owners may change it; the grant ends when their membership does. A page linking a record grants nothing — only a binding does. Confirm with the person in one sentence before binding."""
        return audited('bind' if body.bound else 'unbind', caller, body.record_id,
                       lambda: store.bind(caller.org, caller, body.workspace, body.record_id, body.bound))

    # ── R4: a contact linked to a person's own published profile (profiles.py) ───────────────────
    @app.post('/profile/link', include_in_schema=False)
    def profile_link(body: ProfileLink, caller=Depends(person)):
        """Link this record to a person's published public profile (by its handle), or unlink it. The
        record then shows their public fields live, next to your organisation's own data."""
        if profiles is None:
            raise HTTPException(503, 'Public profiles are not configured')
        if body.linked and (not body.handle or not profiles_mod.HANDLE.match(body.handle)):
            raise HTTPException(422, 'A profile handle is required')
        # The record first: someone who may not change it learns nothing about the profile.
        _, current = store.profile_link_of(caller.org, caller, body.record_id)
        try:
            if body.linked and profiles.public(body.handle) is None:
                raise HTTPException(404, 'No published profile with that handle')
            result = audited('profile_link' if body.linked else 'profile_unlink', caller, body.record_id,
                             lambda: store.profile_link(caller.org, caller, body.record_id, body.handle, body.linked))
            gone = result.get('previous')
            if gone and gone != (body.handle if body.linked else None):
                profiles.tell(gone, caller.org, False)
            if body.linked and gone != body.handle:
                profiles.tell(body.handle, caller.org, True)
        except profiles_mod.ProfileUnavailable as e:
            raise HTTPException(503, 'Identity service unavailable') from e
        return {'record_id': result['record_id'], 'linked': body.linked, 'handle': result['handle']}

    @app.post('/profile/read', include_in_schema=False)
    def profile_read(body: Read, caller=Depends(person)):
        """The public profile this record links to, read live — or none (unlinked, or withdrawn)."""
        rid, handle = audited('profile.read', caller, body.record_id,
                              lambda: store.profile_link_of(caller.org, caller, body.record_id))
        if not handle:
            return {'record_id': rid, 'linked': False, 'profile': None, 'withdrawn': False}
        if profiles is None:
            raise HTTPException(503, 'Public profiles are not configured')
        try:
            prof = profiles.public(handle)
        except profiles_mod.ProfileUnavailable as e:
            raise HTTPException(503, 'Identity service unavailable') from e
        return {'record_id': rid, 'linked': True, 'profile': prof, 'withdrawn': prof is None}

    @app.post('/blocks/read', include_in_schema=False)
    def block_read(body: BlockRead, caller=Depends(person)):
        """A page's ``{{crm …}}`` block, for this one viewer (PRD 0.13.4 D17/D20, `blocks.py`): the
        rows they may read, each field marked ``shared`` when the page's workspace may see it too.
        No totals. A room is refused (it reads with crm_read under its own ceiling)."""
        if caller.shared:
            raise HTTPException(403, {'reason': 'person_only', 'detail': 'A page block is rendered for one person.'})
        args = {str(k)[:40]: str(v)[:200] for k, v in list((body.args or {}).items())[:8]}
        return audited('block.read', caller, '',
                       lambda: blocks_mod.read_block(store, caller.org, caller, body.workspace, body.view, args))

    # ── the desk: the person's own desktop (placements, arrangement versions, desk proposals) ────
    @app.post('/desk/read', operation_id='desktop_read')
    def desk_read(body: DeskRead, caller=Depends(person)):
        """Read the person's own desktop: its version, its layout (sections in order with hidden, collapsed, column, span and size; item order; grouping; filters), the agents and files placed on it, pending suggestions and saved arrangements. Read it before arranging, and pass its version as base_version."""
        return audited('desk.read', caller, '', lambda: desk.read(caller.org, caller))

    @app.post('/desk/arrange', operation_id='desktop_arrange')
    def desk_arrange(body: DeskArrange, caller=Depends(person)):
        """Change the person's desktop directly — ONLY when the person asked for this in the current turn ("put agents first", "hide files", "place my pricing notes"). Then reply in one line saying what changed and that Undo is on the desktop. If you were not asked in this turn, or you run in a routine, on an event, or in a chat other people share, you are refused with propose_instead: call desktop_arrange_propose instead. Refused with desk_changed: read again and retry once on the new version."""
        return audited('desk.arrange', caller, '', lambda: desk.arrange(caller.org, caller, body.ops, body.base_version, body.name))

    @app.post('/desk/arrange/propose', operation_id='desktop_arrange_propose')
    def desk_propose(body: DeskPropose, caller=Depends(person)):
        """Suggest a desktop arrangement the person did not ask for in this turn. It waits on their desktop with your one-line reason beside Apply and Not now; nothing changes until they apply it. Suggest sparingly: at most 5 wait per person and each session may suggest 20 a day. Tell the person in one line that a suggestion is waiting."""
        return audited('desk.propose', caller, '', lambda: desk.propose(caller.org, caller, body.ops, body.reason))

    @app.post('/desk/proposal')
    def desk_proposal(body: DeskProposalReview, caller=Depends(person)):
        """The person applies or dismisses a desktop suggestion, with their own credential (a confirmation, not a tool)."""
        return audited('desk.proposal', caller, body.proposal_id,
                       lambda: desk.review(caller.org, caller, body.proposal_id, body.apply, body.base_version))

    @app.post('/desk/undo')
    def desk_undo(body: DeskUndo, caller=Depends(person)):
        """Restore an earlier desktop version, layout and placements, as a new version (the desktop's Undo)."""
        return audited('desk.undo', caller, '', lambda: desk.undo(caller.org, caller, body.version, body.base_version))

    # ── workspace settings: the person's own pins, mute, notification level, room order, hidden, level ─
    @app.post('/desk/workspaces/read', operation_id='workspace_settings_read')
    def workspace_settings_read(body: WorkspaceSettingsRead, caller=Depends(person)):
        """Read the person's own settings for their workspaces: pinned, hidden from home, muted, room_order, level and collapsed sections, keyed by workspace slug. Only workspaces the person changed are listed; any other reads as the defaults (not pinned, not hidden, not muted, no order, no level, nothing collapsed). They are private to the person and say nothing about who else is in a workspace."""
        return audited('desk.workspaces.read', caller, '', lambda: member_settings.read(caller.org, caller))

    @app.post('/desk/workspaces/set', operation_id='workspace_settings_set')
    def workspace_settings_set(body: WorkspaceSettingsSet, caller=Depends(person)):
        """Change the person's own settings for one workspace — ONLY when the person asked for it in this turn ("mute Growth", "pin Product planning to the top", "hide Old archive from my home"). Send only the keys that change; the answer is the full settings after the change. Then reply in one line saying what changed. Settings grant no access and share nothing. In a routine, on an event, or in a chat other people share you are refused with person_only: tell the person in one line how to do it themselves."""
        return audited('desk.workspaces.set', caller, '', lambda: member_settings.set(
            caller.org, caller, body.workspace, body.settings.model_dump(exclude_unset=True)))

    # ── cards (desktop D2): agents list and propose; a person makes, moves, shares and accepts ────
    @app.post('/card/list', operation_id='card_list')
    def card_list(body: CardList, caller=Depends(person)):
        """List cards: a workspace's board (the cards it references), the person's own (mine), or both by default; filter by about or stage. Each card has its stage, what's next, who it waits on, its sub-cards and members, and its revision. Also lists the proposals waiting for this person's answer. Read before proposing, so you do not propose a card that exists."""
        return audited('card.list', caller, '', lambda: cards.list(caller.org, caller, body.workspace, body.about,
                                                                    body.state, body.mine, body.limit))

    @app.post('/card/propose', operation_id='card_propose')
    def card_propose(request: Request, body: CardPropose, caller=Depends(person)):
        """Propose a new card — a shared, Trello-like card in the organisation's registry: a title, what it is about, a stage, what's next. It lands as a proposal: on the person's own desktop, or on a workspace's board (one you work in) for its members; a person adds it or says not now. You never make a card yourself. Give a one-line reason. Tell the person in one line that it is waiting."""
        if body.workspace:      # onto a board its members read (the person's own desktop is theirs)
            privacy.hold(request, caller, surface='card_proposal')
        return audited('card.propose', caller, '', lambda: cards.propose(
            caller.org, caller, body.title, body.workspace, body.about, body.type, body.parent, body.fields, body.face, body.reason))

    @app.post('/card/update/propose', operation_id='card_update_propose')
    def card_update_propose(request: Request, body: CardUpdatePropose, caller=Depends(person)):
        """Propose a change to a card: its fields (Next, Waiting, Summary, Due, Progress, Checklist, Title), its face, or a stage move (`transition`). A member accepts it; a stage never moves without a person. Pass the card's current revision and a one-line reason, and tell the person in one line that it is waiting."""
        privacy.hold(request, caller, surface='card_proposal')
        return audited('card.update.propose', caller, body.card, lambda: cards.update_propose(
            caller.org, caller, body.card, body.expected_revision, body.fields, body.face, body.transition, body.reason))

    @app.post('/card/read')
    def card_read(body: CardRead, caller=Depends(person)):
        """One card for the person, with its activity (its movement history). Reading never marks it
        seen; the terminal does, on render in view (/card/seen)."""
        return audited('card.read', caller, body.card, lambda: cards.read(caller.org, caller, body.card))

    # ── S5 (PRD 0.13.4 D12–D14): the inbox, the seen relation, and decisions ────────────────────
    @app.post('/card/inbox')
    def card_inbox(body: CardInbox, caller=Depends(person)):
        """The person's Inbox: open cards whose next actor they are, and the counts the Now strip,
        the Inbox and a room badge read (one predicate)."""
        return audited('card.inbox', caller, '', lambda: cards.inbox(caller.org, caller))

    @app.post('/card/seen')
    def card_seen(body: CardRef, caller=Depends(session_person)):
        """The terminal rendered the card in view (D14): the only writer of `seen`. Session credential only."""
        return audited('card.seen', caller, body.card, lambda: cards.seen(caller.org, caller, body.card))

    @app.post('/card/nonce')
    def card_nonce(body: CardRef, caller=Depends(session_person)):
        """A server nonce bound to this person, this card at its revision and the face they see (D13)."""
        return audited('card.nonce', caller, body.card, lambda: cards.nonce(caller.org, caller, body.card))

    @app.post('/card/decide')
    def card_decide(body: CardDecide, caller=Depends(session_person)):
        """A gate decision on a card (D12) by its next actor, recorded as a decision record (D13):
        session credential, the nonce, and the content hash of the face they saw. Never an agent."""
        return audited('card.decide.' + body.action, caller, body.card, lambda: cards.decide(
            caller.org, caller, body.card, body.action, body.nonce, body.content_hash, body.evidence, body.receipt,
            body.answer, body.reason))

    @app.post('/card/create')
    def card_create(body: CardCreate, caller=Depends(person)):
        """The person makes a card ("Make a card"), with their own credential. An agent is refused with propose_instead."""
        return audited('card.create', caller, '', lambda: cards.create(
            caller.org, caller, body.title, body.workspace, body.about, body.type, body.parent, body.fields, body.face,
            body.description))

    @app.post('/card/update')
    def card_update(body: CardUpdate, caller=Depends(person)):
        """A member changes a card or moves its stage, with their own credential."""
        return audited('card.update', caller, body.card, lambda: cards.update(
            caller.org, caller, body.card, body.expected_revision, body.fields, body.face, body.transition))

    @app.post('/card/members')
    def card_members(body: CardMembers, caller=Depends(person)):
        """A member adds a person to a card (they are invited, and decide) or removes one; anyone may leave."""
        return audited('card.members', caller, body.card, lambda: cards.members(caller.org, caller, body.card, body.subject, body.add))

    @app.post('/card/membership')
    def card_membership(body: CardMembership, caller=Depends(person)):
        """The person added to a card accepts or declines."""
        return audited('card.membership', caller, body.card, lambda: cards.membership(caller.org, caller, body.card, body.accept))

    @app.post('/internal/references', include_in_schema=False)
    def references(body: Snapshot, caller=Depends(internal)):
        return audited('references', caller, '', lambda: store.apply_workspace_snapshot(
            org_id, body.workspace, body.commit, body.committed_at, [r.model_dump() for r in body.references],
            None if body.members is None else [m.model_dump() for m in body.members]))

    @app.post('/internal/cards/offer', include_in_schema=False)
    def internal_cards_offer(body: MeetingOffer, caller=Depends(internal)):
        """S5/E2: the post-meeting run offers the meeting's follow-ups as cards of a kind, AS THE ROOM'S
        WORKSPACE PRINCIPAL (D2) — the caller crm-api builds here is exactly the one a room turn's
        delegation resolves to (shared, never admin, the members' intersection), so it is the same
        ``card_propose`` path with the same caps. Internal tier only (flows holds the secret); a
        workspace principal never writes anything but an offer, and only the next actor moves it."""
        ws = check_workspace(body.workspace) if body.workspace else None
        actor = Caller(subject=body.principal, workspaces=(ws,) if ws else (), writable=(ws,) if ws else (),
                       delegated=True, regime='autonomous', shared=bool(ws), session=body.session,
                       trace=caller.trace, credential_class='delegation',
                       extra={'on_behalf_of': body.on_behalf_of,
                              **({'principal_kind': 'workspace', 'principal_workspace': ws} if ws else {})})
        specs = [{'title': c.title, 'about': c.about, 'fields': {
            'Kind': c.kind, 'NextActor': c.next_actor, 'Requester': c.requester or body.on_behalf_of,
            **({'Due': c.due} if c.due else {})}} for c in body.cards]
        return audited('card.offer', actor, '', lambda: cards.offer_many(org_id, actor, ws, body.origin, specs,
                                                                         body.reason))

    @app.post('/internal/orgs/{org}/erase', include_in_schema=False)
    def erase_org(org: str, caller=Depends(internal)):
        """Erase an individual's personal CRM: the rows keyed to their org of one (``p-<id>``),
        on account delete. Never the install's org, never any other shape of org. The answer names
        what it left: the append-only tables stay until their erasure is decided
        (``migrate.ERASE_APPEND_ONLY``)."""
        if org == org_id or not PERSONAL_ORG.fullmatch(org):
            raise HTTPException(400, 'Only an individual account\'s own CRM (p-<id>) can be erased')
        return {'org': org, **store.erase_org(org)}

    # ── D35: one Person, many addresses (persons.py) ─────────────────────────────────────────
    @app.post('/person/me', include_in_schema=False)
    def person_me(body: PersonMe, caller=Depends(person)):
        """The signed-in person's own Person: how many accounts answer for it, their addresses (their
        own in full, any other masked), the primary address, and the merge card waiting for them."""
        return audited('person.me', caller, '', lambda: store.persons.me(caller.org, caller))

    @app.post('/person/merge', include_in_schema=False)
    def person_merge(body: PersonMerge, caller=Depends(person)):
        """The person's own act on their merge card: confirm (with the primary address), decline, change
        the primary later, or offer another address of theirs. A person's own credential only."""
        p = store.persons
        if body.action == 'confirm':
            call = lambda: p.confirm(caller.org, caller, body.card, body.primary or None)
        elif body.action == 'decline':
            call = lambda: p.decline(caller.org, caller, body.card)
        elif body.action == 'primary':
            call = lambda: p.set_primary(caller.org, caller, body.primary)
        else:
            call = lambda: p.offer_for_address(caller.org, caller, body.address)
        return audited('person.merge.' + body.action, caller, body.card, call)

    @app.post('/person/profile', include_in_schema=False)
    def person_profile(body: PersonProfile, caller=Depends(person)):
        """The person's own profile: read, edit (theirs only), claim, unclaim, the public page. A
        person's own credential only; a worker is refused."""
        p = store.profiles
        org = caller.org

        def _claimed(result):
            if result.get('status') == 'claimed':
                try:
                    p.carry_legacy(org, caller.subject, result['record_id'])
                except Exception:                         # noqa: BLE001 — the claim stands without it
                    pass
            return result
        calls = {'read': lambda: p.mine(org, caller),
                 'edit': lambda: p.edit(org, caller, body.fields, body.record_id or None),
                 'claim': lambda: _claimed(p.claim(org, caller, body.record_id or None)),
                 'unclaim': lambda: p.unclaim(org, caller),
                 'publish': lambda: p.publish(org, caller, body.public, body.public_fields),
                 'import': lambda: p.import_profile(org, caller, body.source)}
        return audited('person.profile.' + body.action, caller, body.record_id, calls[body.action])

    @app.post('/internal/profiles', include_in_schema=False)
    def profiles_directory(body: Accounts, caller=Depends(internal)):
        """Profile fields of the people agent-api's directory rule let a viewer see. Never org fields."""
        out = store.profiles.directory(org_id, [a.model_dump() for a in body.accounts])
        store.audit(org_id, caller, 'profiles.directory', '', 'ok')
        return {'profiles': out}

    @app.post('/internal/profiles/public', include_in_schema=False)
    def profiles_public(body: Handle, caller=Depends(internal)):
        """A public page: only while the person has it on, only the fields they marked public."""
        page = store.profiles.public(body.handle)
        if page is None:
            raise HTTPException(404, 'No public profile')
        return page

    @app.post('/internal/persons', include_in_schema=False)
    def persons(body: Accounts, caller=Depends(internal)):
        """agent-api's picker and grants: each account's Person, every account linked to it and the
        primary address. Facts for the platform, never shown to another person as they are."""
        out = store.persons.group(org_id, [a.model_dump() for a in body.accounts])
        store.audit(org_id, caller, 'persons', '', 'ok')
        return {'persons': out}

    @app.post('/internal/accounts', include_in_schema=False)
    def accounts(body: Account, caller=Depends(internal)):
        record_id = store.ensure_account(org_id, Caller(subject=body.subject, email=body.email.strip().lower()))
        store.audit(org_id, caller, 'account', record_id or '', 'ok')
        return {'record_id': record_id}

    return app


def company_limits(env=None):
    """The desk limits' source: `_global/POLICIES.md` through agent-api's door (``CRM_AGENT_API_URL``,
    optional — unset, the defaults apply), with the internal tier's secret."""
    env = os.environ if env is None else env
    return Policies(env.get(DOOR_ENV, ''), env.get('INTERNAL_API_SECRET', ''))


def configured_app():
    url = os.environ.get('CRM_DATABASE_URL', '')
    base = os.environ.get('ADMIN_API_URL', '')
    secret = os.environ.get('INTERNAL_API_SECRET', '')
    org_id = os.environ.get('CRM_ORG_ID', '').strip()
    if not all((url, base, secret, org_id)):
        raise RuntimeError('CRM_DATABASE_URL, ADMIN_API_URL, INTERNAL_API_SECRET and CRM_ORG_ID are required for CRM service')
    if not url.startswith('postgresql+pg8000://'):
        raise RuntimeError('CRM deployment requires PostgreSQL')
    from .migrate import check
    engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=0)
    check(engine)       # its own role, never a superuser, and the schema version this code knows
    limits = company_limits()
    from .bootstrap import ensure_personal_org
    # an individual's personal CRM is made on its first use (only with `personal_crm` on), through the
    # migrations path's function: crm-api's role cannot write orgs, policies or definitions itself
    resolver = OrgResolver(org_id, base, secret,
                           ensure_org=lambda org, name: ensure_personal_org(engine, org, name))
    return create_app(Store(engine), Identity(base, secret), org_id, secret, limits=limits,
                      policy_text=PolicyText(limits), profiles=profiles_mod.ProfileClient(base, secret),
                      org_resolver=resolver)
