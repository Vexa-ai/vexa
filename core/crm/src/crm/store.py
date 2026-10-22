"""Permission-aware record operations shared by HTTP, MCP and Minutes."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
import json
import hashlib
import logging
from urllib.parse import urlencode
from jsonschema import Draft202012Validator
from sqlalchemy import select, update, delete, or_, false, text, func
from . import schema as s
from .cards import CardLayout, visible_layout
from .caller import Caller, as_caller, REFUSAL, SHARED_REFUSAL, CONFIRM_REFUSAL
from .field_classes import (Classes, FieldView, MATCH_IDS, NARRATIVE, PSEUDO, ROOM_RULE_KEY, ROOM_RULES,
                            DEFAULT_ROOM_RULE, as_set, intersect)
from .migrate import ERASE_APPEND_ONLY, erase_plan
from .orgs import PERSONAL_ORG

log = logging.getLogger('crm.store')

#: H9 (PRD 0.13.4 §5.3): the audit row of the operation in flight, written IN THE SAME TRANSACTION as
#: the operation's own writes (``Store.begin``). crm-api's ``audited`` sets it per request; a read
#: path that commits nothing has it written by ``audited`` before the answer leaves, so no answer is
#: ever returned without its audit row and no write commits without one.
PENDING_AUDIT: ContextVar = ContextVar('crm_pending_audit', default=None)


#: The fields a record shows a cross-org room (delegation.v1 ``scope.audience``): who or what it is,
#: never what the org knows about it. An object's policy may add its own (``public_fields``).
PUBLIC_FIELDS = ('Name', 'FirstName', 'LastName', 'Title', 'Website', 'Domain', 'Industry', 'LinkedIn')


def public_fields(policy, object_type):
    """The fields of this type a cross-org room reads: PUBLIC_FIELDS plus the type's own."""
    rule = (policy.get('objects', {}) or {}).get(object_type, {}) or {}
    return set(PUBLIC_FIELDS) | set(rule.get('public_fields') or [])


#: The keys an answer to a cross-org room keeps, per kind of answer. Every other key is BLANKED
#: (``[]``, ``{}`` or ``None`` by its type), so the answer keeps its shape and a key added to an
#: answer later reaches a room with people from outside the org empty until it is put on this list.
#: Record fields are already projected by ``Store._visible``; this is the envelope around them.
CROSS_ORG_KEYS = {
    'record': frozenset({'id', 'org_id', 'object_type', 'revision', 'quarantined', 'fields', 'narrative',
                         'audience', 'card', 'href', 'links', 'actions'}),
    'link': frozenset({'field', 'record_id', 'object_type', 'label'}),
    'revision': frozenset({'revision', 'created_at', 'before', 'after'}),
    'history': frozenset({'revisions'}),
    'search': frozenset({'records', 'next_offset', 'card', 'href'}),
    'resolve': frozenset({'records'}),
}
_CROSS_ORG_NESTED = {'record': {'links': 'link'}, 'revision': {'before': 'record', 'after': 'record'},
                     'history': {'revisions': 'revision'}, 'search': {'records': 'record'},
                     'resolve': {'records': 'record'}}


def _blank(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


def cross_org_answer(kind, answer):
    """``answer`` as a room with people from outside the org receives it (``CROSS_ORG_KEYS``)."""
    if not isinstance(answer, dict):
        return answer
    keep, nested = CROSS_ORG_KEYS[kind], _CROSS_ORG_NESTED.get(kind, {})
    out = {}
    for key, value in answer.items():
        if key not in keep:
            out[key] = _blank(value)
        elif key in nested and isinstance(value, list):
            out[key] = [cross_org_answer(nested[key], item) for item in value]
        elif key in nested:
            out[key] = cross_org_answer(nested[key], value)
        else:
            out[key] = value
    return out

class CRMError(Exception):
    status = 400
class Forbidden(CRMError):
    status = 403
class NotFound(CRMError):
    status = 404
class Conflict(CRMError):
    status = 409
class Refused(CRMError):
    """A typed refusal a worker reads the same way whichever service refused it."""
    status = 403
    def __init__(self, body, message=''):
        super().__init__(message or body['reason'])
        self.body = body
class SharedRefused(NotFound):
    """A record the speaker may read, outside what every participant of the turn may read
    (ADR-0047 d7, ADR-0048 d4). A NotFound to every loop that skips unreadable links, so a linked
    record outside the intersection is omitted; a refusal with its own body at the top level."""
    status = 403
    body = SHARED_REFUSAL
    def __init__(self):
        super().__init__('shared_conversation')

#: The roles whose members a binding gives a derived WRITE grant; every member gets read.
WRITE_ROLES = ('owner', 'contributor')

class Store:
    def __init__(self, engine):
        self.engine = engine
        #: Desktop D2: the card service (card_ops.Cards) registers itself here, so every write path
        #: — crm_change as much as the card tools — keeps a card type's State on its lifecycle and
        #: its Face sanitised, and a card's members may review its proposals.
        self.cards = None
        #: D35 (``persons.py``): an account resolves to its Person by verified address; one Person may
        #: answer for several accounts, and merging them is the person's own act.
        from .persons import Persons
        self.persons = Persons(self)
        #: D35 founder addition (``person_profiles.py``): a profile is a claimed Person record.
        from .person_profiles import Profiles
        self.profiles = Profiles(self)

    @contextmanager
    def begin(self):
        """``engine.begin()`` that writes the pending audit row (H9) inside the same transaction,
        just before it commits. Write paths use it; with no audit pending it is ``engine.begin()``."""
        with self.engine.begin() as c:
            yield c
            pending = PENDING_AUDIT.get()
            if pending and not pending.get('written'):
                caller = as_caller(pending['caller'])
                c.execute(s.audit.insert().values(
                    org_id=pending['org'], id=str(uuid4()), at=self._now(), subject=caller.subject or '-',
                    caller=caller.kind, session=caller.session, trace=caller.trace, op=pending['op'],
                    record_id=str(pending.get('record_id') or ''), outcome='ok', **audit_principal(caller)))
                pending['written'] = True

    def audit_names(self, record_id):
        """The record a write made, for its audit row (a create learns its id inside the operation)."""
        pending = PENDING_AUDIT.get()
        if pending and not pending.get('written') and not pending.get('record_id'):
            pending['record_id'] = str(record_id)

    def _policy(self, c, org, caller, context=True):
        caller=as_caller(caller)
        if (caller.extra or {}).get('principal_kind') == 'workspace':
            # D2 + D4 (deny test 36): a WORKSPACE principal (a room's own account) has no CRM policy
            # of its own and NEVER falls to the organisation's default. It is the room: it reaches a
            # record only through a workspace in its ceiling (``_rights``: shared needs ``bound``),
            # and its fields are the members' intersection (``_room_allowed``), nothing wider.
            value = {'active': True, 'objects': {}, 'records': {}, '_principal': 'workspace'}
        else:
            value = c.execute(select(s.policies.c.policy).where(
                s.policies.c.org_id == org, s.policies.c.subject_id == caller.subject)).scalar_one_or_none()
        if value is None and not caller.internal:
            # A signed-in member of the organisation with no policy of their own gets the
            # organisation's default (ADR-0047 d6) — read, never written, so no policy row appears.
            value = self.org_policy(c, org)
        if caller.is_admin and not caller.shared:
            # An instance administrator is a CRM administrator (ADR-0047 d6: admin sees all).
            value = {**(value or {}), 'active': True, 'admin': True}
        if not value or not value.get('active', False):
            raise Forbidden('No active CRM membership')
        if caller.shared:
            # D4: a shared (room) caller is never an administrator or a reviewer, whatever the
            # person behind it holds; its fields are the room's (``_view``). Whether the person is
            # one is kept only to choose the refusal (``_rights``), never to grant.
            person_admin = bool(value.get('admin')) or bool((caller.extra or {}).get('person_admin'))
            value = {k: v for k, v in value.items() if k not in ('admin', 'reviewer')}
            value['_person_admin'] = person_admin
        # Derived grants (d6): the records bound to a workspace the caller is a member of NOW, as
        # identity answered it — for a worker already narrowed to its ceiling, and in a turn other
        # people read, the participants' intersection. The roster gives the role a write needs.
        workspaces=set(caller.workspaces)
        bound={};roles={}
        if workspaces:
            b=s.workspace_bindings
            for workspace,record_id in c.execute(select(b.c.workspace,b.c.record_id).where(
                    b.c.org_id==org,b.c.workspace.in_(workspaces))).all():
                bound.setdefault(record_id,set()).add(workspace)
            r=s.workspace_rosters
            roles=dict(c.execute(select(r.c.workspace,r.c.role).where(r.c.org_id==org,
                r.c.subject==caller.subject,r.c.workspace.in_(workspaces))).all())
        # Card membership (desktop D2): the cards this person was added to, and how far.
        m=s.card_members
        members=dict(c.execute(select(m.c.record_id,m.c.status).where(m.c.org_id==org,
            m.c.subject==caller.subject)).all()) if caller.subject else {}
        out = {**value, "subject_id": caller.subject, '_caller': caller, '_bound': bound, '_roles': roles,
               '_members': members}
        if context:
            out['_classes'] = self._classes(c, org)
            if caller.shared:
                out['_room'] = self._room(c, org, caller)
        return out

    # ── D4: field classification and the room ceiling (field_classes.py) ─────────────────────
    def _classes(self, c, org):
        fc = s.field_classes
        rows = [dict(r) for r in c.execute(select(fc.c.object_type, fc.c.field, fc.c['class'], fc.c.workspaces)
                                           .where(fc.c.org_id == org)).mappings()]
        defs = dict(c.execute(select(s.object_types.c.name, s.object_types.c.definition)
                              .where(s.object_types.c.org_id == org)).all())
        return Classes(rows, defs)

    def _room(self, c, org, caller):
        """The room a shared caller answers: its workspaces, every current member's policy (as each
        member, never as an administrator acting for the room), and the organisation's room rule."""
        ceiling = set(caller.workspaces)
        roster = {}
        if ceiling:
            r = s.workspace_rosters
            for workspace, subject in c.execute(select(r.c.workspace, r.c.subject).where(
                    r.c.org_id == org, r.c.workspace.in_(ceiling))).all():
                roster.setdefault(subject, set()).add(workspace)
        members = {}
        for subject, workspaces in roster.items():
            if str(subject).startswith('ws:'):
                continue
            try:
                members[subject] = self._policy(c, org, Caller(subject=subject, workspaces=tuple(sorted(workspaces))),
                                                context=False)
            except Forbidden:
                members[subject] = None     # a member with no CRM membership reads nothing: so does the room
        rule = (self.org_policy(c, org) or {}).get(ROOM_RULE_KEY, DEFAULT_ROOM_RULE)
        if rule not in ROOM_RULES:
            rule = 'classified'             # an unreadable rule fails closed to the narrower one
        return {'workspaces': ceiling, 'members': members, 'rule': rule}

    def _room_allowed(self, policy, kind, member_fields):
        """(allowed, excluded, explicit) for a shared caller; ``member_fields(policy)`` is one
        member's field set for this record or type (None = every field)."""
        classes = policy.get('_classes') or Classes([], {})
        room = policy.get('_room') or {'workspaces': set(), 'members': {}, 'rule': 'classified'}
        explicit = classes.shared_with(kind, room['workspaces'])
        if room['rule'] == 'classified':
            return {n for n in explicit if n not in PSEUDO}, set(), explicit
        allowed = member_fields(policy)
        if not room['members']:
            allowed = set()                 # no roster: no member to intersect, so nothing
        for member in room['members'].values():
            allowed = intersect(allowed, member_fields(member) if member is not None else set())
        excluded = (classes.private(kind) | classes.shared_elsewhere(kind, room['workspaces'])) - explicit
        if allowed is not None:
            allowed |= {n for n in explicit if n not in PSEUDO}
        return allowed, excluded, explicit

    def _view(self, policy, row):
        """What this caller reads of this record (``FieldView``), or NotFound/SharedRefused. Every read
        path takes its fields from here: restricted fields are absent, never nulled."""
        raw = self._rights(policy, row, 'read')
        caller = policy.get('_caller') or as_caller(policy.get('subject_id', ''))
        classes = policy.get('_classes') or Classes([], {})
        kind = row['object_type']
        props = tuple(classes.props(kind))
        if caller.shared:
            def fields_of(p):
                try:
                    return as_set(self._rights(p, row, 'read'))
                except NotFound:
                    return set()
            allowed, excluded, explicit = self._room_allowed(policy, kind, fields_of)
            return FieldView(allowed, excluded, full=False, narrative=NARRATIVE in explicit,
                             match_ids=MATCH_IDS in explicit, props=props)
        allowed = as_set(raw)
        owner = bool(caller.subject) and row.get('owner_id') == caller.subject
        excluded = set() if (policy.get('admin') or owner) else classes.private(kind)
        full = allowed is None
        return FieldView(allowed, excluded, full=full, narrative=full and NARRATIVE not in excluded,
                         match_ids=full and MATCH_IDS not in excluded, props=props)

    def _type_view(self, policy, kind):
        """The fields of a type this caller may filter or lay out by (search, describe)."""
        caller = policy.get('_caller') or as_caller(policy.get('subject_id', ''))
        classes = policy.get('_classes') or Classes([], {})
        props = tuple(classes.props(kind))
        def fields_of(p):
            if p.get('admin'):
                return None
            return as_set((p.get('objects', {}) or {}).get(kind, {}).get('read_fields', ['*']))
        if caller.shared:
            allowed, excluded, explicit = self._room_allowed(policy, kind, fields_of)
            return FieldView(allowed, excluded, props=props)
        allowed = None if policy.get('admin') else as_set(policy.get('objects', {}).get(kind, {}).get('read_fields', []))
        # A private field is the owner's: filtering by it would say which records hold a value.
        excluded = set() if policy.get('admin') else classes.private(kind)
        return FieldView(allowed, excluded, props=props)

    def _definition(self, c, org, kind):
        value = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == org, s.object_types.c.name == kind)).scalar_one_or_none()
        if value is None:
            raise NotFound('Unknown object type')
        return value

    def _rights(self, policy, row, permission):
        """The fields this caller may read or write on this record, or NotFound.

        Two gates, neither widens the other (ADR-0047 d6): the CRM policy (scope all/own/none per
        record type, per-record grants), OR a derived grant from a bound workspace the caller is a
        member of — which never widens a field restriction the policy sets for that type. In a turn
        other people read, a record must also be bound to a workspace in the turn's ceiling.
        """
        caller=policy.get('_caller') or as_caller(policy.get('subject_id',''))
        bound=policy.get('_bound',{}).get(row['id'],set())
        if policy.get('admin'):
            fields=['*']
        else:
            if caller.shared and policy.get('_person_admin') and not bound:
                # The speaker (an administrator) may read it in their own chat; this room may not.
                raise SharedRefused()
            if row.get('quarantined'):
                raise NotFound('Record not found')
            rule = policy.get('objects', {}).get(row['object_type'], {})
            scope = rule.get(permission, 'none')
            granted = policy.get('records', {}).get(row['id'], [])
            allowed = scope == 'all' or (scope == 'own' and row['owner_id'] == policy.get('subject_id')) or permission in granted
            fields = rule.get(permission + '_fields', [])
            membership=policy.get('_members',{}).get(row['id'])
            if not allowed and membership and (permission=='read' or membership=='member'):
                # A card's member reads it (an invited one too, to decide) and a member writes it —
                # within any field restriction the policy sets for the type. Never its About target.
                allowed=True
                fields=rule.get(permission+'_fields',['*'])
            if not allowed and bound:
                roles=policy.get('_roles',{})
                # Write: identity's live answer of the memberships that may write, when the caller
                # came from identity; the workspace roster only for a caller that did not.
                writes=(any(w in caller.writable for w in bound) if caller.writable is not None
                        else any(roles.get(w) in WRITE_ROLES for w in bound))
                if permission=='read' or writes:
                    allowed=True
                    fields=rule.get(permission+'_fields',['*'])
            if not allowed:
                raise NotFound('Record not found')
        if caller.shared and not bound:
            raise SharedRefused()
        return fields

    def _row(self, c, org, record_id):
        """The record, or the record it was merged into (d3: one id answers everywhere)."""
        for _ in range(8):
            row = c.execute(select(s.records).where(s.records.c.org_id == org, s.records.c.id == record_id)).mappings().first()
            if row is not None and row['merged_into']:
                record_id=row['merged_into'];continue
            break
        if row is None or row['deleted'] or row['merged_into']:
            raise NotFound('Record not found')
        return dict(row)

    def _visible(self, policy, row):
        view = self._view(policy, row)
        result = {k: row[k] for k in ('id', 'org_id', 'object_type', 'revision')}
        result['quarantined'] = row['quarantined']
        result['fields'] = view.filter(row['fields'])
        # Narrative may contain any field: partial-field users cannot read it, nor a room unless
        # the narrative is classified shareable to the room's workspace (D4).
        result['narrative'] = row['narrative'] if view.narrative else None
        caller = policy.get('_caller')
        if caller is not None and getattr(caller, 'cross_org', False):
            # A ROOM WITH PEOPLE FROM OUTSIDE THE ORG (rooms, K1): the org's private facts never reach
            # it. Projected here, in the one place a record becomes an answer, so no read path — get,
            # list, search, links, revisions — can forget it; the narrative is never public.
            public = public_fields(policy, row['object_type'])
            result['fields'] = {k: v for k, v in result['fields'].items() if k in public}
            result['narrative'] = None
            result['audience'] = 'cross_org'
        return result

    def _card(self, c, org, kind):
        row = c.execute(select(s.card_layouts).where(s.card_layouts.c.org_id == org,
            s.card_layouts.c.object_type == kind).order_by(s.card_layouts.c.version.desc()).limit(1)).mappings().first()
        return {'version': row['version'], 'layout': row['layout']} if row else {'version': 0, 'layout': None}

    def configure(self, org, actor, object_type, layout=None, expected_version=0, reason=''):
        caller=as_caller(actor);actor=caller.subject
        with self.begin() as c:
            policy = self._policy(c,org,caller)
            if not policy.get('admin'):
                raise Forbidden('CRM administrator rights required to configure shared cards')
            # Serialize the first insert too, without granting writes on imported definitions.
            if self.engine.dialect.name == 'postgresql':
                lock = int.from_bytes(hashlib.sha256(json.dumps([org, object_type]).encode()).digest()[:8], 'big', signed=True)
                c.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
            definition = c.execute(select(s.object_types.c.definition).where(
                s.object_types.c.org_id == org, s.object_types.c.name == object_type)).scalar_one_or_none()
            if definition is None: raise NotFound('Unknown object type')
            current = self._card(c, org, object_type)
            if layout is None: return current
            if caller.unwatched: raise Refused(REFUSAL)
            if expected_version != current['version']: raise Conflict('Card layout changed; read configuration again')
            if not reason.strip(): raise CRMError('Configuration changes require a reason')
            try: layout = CardLayout.model_validate(layout).model_dump()
            except ValueError as e: raise CRMError('Invalid card layout: '+str(e)) from e
            properties = definition.get('properties', {})
            references = [layout['title_field']] + [f['field'] for sec in layout['sections'] for f in sec['fields']]
            if any(name and name not in properties for name in references): raise CRMError('Unknown field in card layout')
            version = current['version'] + 1
            c.execute(s.card_layouts.insert().values(org_id=org, object_type=object_type, version=version,
                layout=layout, actor_id=actor, reason=reason, created_at=datetime.now(timezone.utc).isoformat()))
            return {'version': version, 'layout': layout}

    def describe(self, org, actor, object_type=None):
        caller=as_caller(actor);actor=caller.subject
        with self.engine.connect() as c:
            policy = self._policy(c,org,caller)
            rows = c.execute(select(s.object_types).where(s.object_types.c.org_id == org)).mappings()
            result = []
            for row in rows:
                kind = row['name']
                if object_type and kind != object_type:
                    continue
                rule = policy.get('objects', {}).get(kind, {})
                if not policy.get('admin') and rule.get('read', 'none') == 'none':
                    continue
                allowed = ['*'] if policy.get('admin') else rule.get('read_fields', [])
                definition = row['definition']
                fields = {k: v for k, v in definition.get('properties', {}).items() if '*' in allowed or k in allowed}
                if policy['_caller'].shared:
                    view = self._type_view(policy, kind)       # D4: a room describes the room's fields
                    fields = {k: v for k, v in fields.items() if view.has(k)}
                result.append({'object_type': kind, 'fields': fields})
            return {'objects': result}

    def get(self, org, actor, record_id):
        caller=as_caller(actor);actor=caller.subject
        with self.engine.connect() as c:
            policy = self._policy(c,org,caller)
            row=self._row(c, org, record_id)
            record_id=row['id']
            result=self._visible(policy,row)
            card=self._card(c,org,row['object_type'])
            result['card']={**card,'layout':visible_layout(card['layout'],result['fields']) if card['layout'] else None}
            result['href']='/crm?'+urlencode({'record':record_id})
            result['sources']=[dict(r) for r in c.execute(select(s.source_mappings.c.system,
                s.source_mappings.c.source_org,s.source_mappings.c.object_type,s.source_mappings.c.source_id).where(
                s.source_mappings.c.org_id==org,s.source_mappings.c.record_id==record_id)).mappings()]
            result['links']=[]
            for link in c.execute(select(s.relationships).where(s.relationships.c.org_id==org,
                    s.relationships.c.record_id==record_id)).mappings():
                if link['target_domain']!='crm':continue
                if link['kind']=='Description':
                    if result['narrative'] is None:continue
                elif link['kind'] not in result['fields']:continue
                try:
                    target=self._visible(policy,self._row(c,org,link['target_id']))
                except NotFound:continue
                result['links'].append({'field':link['kind'],'record_id':target['id'],
                    'object_type':target['object_type'], 'label':target['fields'].get('Name') or ' '.join(str(target['fields'].get(k) or '') for k in ('FirstName','LastName')).strip() or target['fields'].get('Title') or target['object_type']})
            for link in c.execute(select(s.relationships).where(s.relationships.c.org_id==org,
                    s.relationships.c.target_domain=='crm',s.relationships.c.target_id==record_id)).mappings():
                try:source=self._visible(policy,self._row(c,org,link['record_id']))
                except NotFound:continue
                if link['kind']=='Description':
                    if source['narrative'] is None:continue
                elif link['kind'] not in source['fields']:continue
                result['links'].append({'field':'Referenced by' if link['kind']=='Description' else 'Related via '+link['kind'],'record_id':source['id'],
                    'object_type':source['object_type'],'label':source['fields'].get('Name') or source['fields'].get('name') or source['fields'].get('Subject') or ' '.join(str(source['fields'].get(k) or '') for k in ('FirstName','LastName')).strip() or source['fields'].get('Title') or source['object_type']})
            can_write=False
            try:can_write=bool(self._rights(policy,row,'write'))
            except NotFound:pass
            sees_all=can_write and bool(policy.get('admin') or policy.get('reviewer') or self._is_card(c,org,record_id))
            can_review=sees_all and not caller.delegated
            result['actions']={'can_review':can_review,'can_update':can_write and not caller.delegated and not policy.get('review_required',False),'can_propose':True}
            result['mentioned_in']=self._mentioned_in(c,org,policy,row)
            # D4: every workspace a record is bound to only for an administrator in person; a worker
            # (and so every room) sees only the bindings inside its ceiling.
            result['bound_to']=self._bindings(c,org,record_id) if policy.get('admin') and not caller.delegated \
                else sorted(w for w in policy.get('_bound',{}).get(record_id,set()))
            view=self._view(policy,row)
            if view.match_ids:
                # Identifiers are email-derived (private by default): absent, never blanked, otherwise.
                result['match_ids']=sorted(m for (m,) in c.execute(select(s.match_ids.c.identifier).where(
                    s.match_ids.c.org_id==org,s.match_ids.c.record_id==record_id)).all())
                result['account_id']=row.get('account_id')
            result['proposals']=[]
            query=select(s.change_proposals).where(s.change_proposals.c.org_id==org,
                s.change_proposals.c.record_id==record_id,s.change_proposals.c.status=='pending').limit(50)
            if not sees_all:query=query.where(s.change_proposals.c.author_id==actor)
            for proposal in c.execute(query).mappings():
                result['proposals'].append({'id':proposal['id'],'base_revision':proposal['base_revision'],
                    'author_kind':proposal['patch'].get('author_kind','person'),
                    'fields':view.filter(proposal['patch']['fields']),
                    'narrative':proposal['patch'].get('narrative') if view.narrative else None,
                    'reason':proposal['reason'] if view.full else None,
                    'evidence':proposal['evidence'] if view.full else []})
            # A room with people from outside the org: the answer's envelope too, by an allow-list.
            return cross_org_answer('record',result) if caller.cross_org else result

    def search(self, org, actor, object_type, filters=None, limit=20, offset=0):
        caller=as_caller(actor);actor=caller.subject
        if not 1 <= limit <= 100 or offset < 0:
            raise CRMError('Limit must be 1..100 and offset nonnegative')
        with self.engine.connect() as c:
            policy = self._policy(c,org,caller)
            definition = self._definition(c, org, object_type)
            allowed = ['*'] if policy.get('admin') else policy.get('objects', {}).get(object_type, {}).get('read_fields', [])
            # A room with people from outside the org filters on public fields only: a filter on any
            # other field would answer which records hold a value the room may not read.
            public = public_fields(policy, object_type) if caller.cross_org else None
            # D4: a room filters only on the room's fields, a person never on another owner's private
            # field — either would answer which records hold a value the caller may not read.
            tview = self._type_view(policy, object_type)
            for name in (filters or {}):
                if name not in definition.get('properties', {}) or ('*' not in allowed and name not in allowed) \
                        or not tview.has(name) or (public is not None and name not in public):
                    raise Forbidden('Filter field unavailable')
            query = select(s.records).where(s.records.c.org_id == org,
                s.records.c.object_type == object_type, s.records.c.deleted == False).order_by(s.records.c.id)
            if not policy.get('admin'):
                rule=policy.get('objects',{}).get(object_type,{})
                scope=rule.get('read','none')
                granted=[record_id for record_id, rights in policy.get('records',{}).items() if 'read' in rights]
                access=s.records.c.id.in_(granted) if granted else false()
                if scope=='all':
                    access=s.records.c.org_id==org
                elif scope=='own':
                    access=or_(access,s.records.c.owner_id==actor)
                derived=list(policy.get('_bound',{}))
                if derived:access=or_(access,s.records.c.id.in_(derived))
                query=query.where(access,s.records.c.quarantined==False)
            if caller.shared:
                # A turn other people read lists only records bound to the turn's ceiling.
                derived=list(policy.get('_bound',{}))
                query=query.where(s.records.c.id.in_(derived) if derived else false())
            for name,value in (filters or {}).items():
                errors=list(Draft202012Validator(definition['properties'][name]).iter_errors(value))
                if errors:raise CRMError('Invalid filter value for '+name)
                column=s.records.c.fields[name]
                if isinstance(value,bool):column=column.as_boolean()
                elif isinstance(value,int):column=column.as_integer()
                elif isinstance(value,float):column=column.as_float()
                else:column=column.as_string()
                query=query.where(column==value)
            rows=c.execute(query.offset(offset).limit(limit+1)).mappings().all()
            result=[self._visible(policy,dict(row)) for row in rows[:limit]]
            for record in result:record['href']='/crm?'+urlencode({'record':record['id']})
            by_id={record['id']:record for record in result}
            links=c.execute(select(s.relationships).where(s.relationships.c.org_id==org,
                s.relationships.c.record_id.in_(by_id),s.relationships.c.target_domain=='crm')).mappings().all() if by_id else []
            target_ids={link['target_id'] for link in links}
            targets={}
            if target_ids:
                for target in c.execute(select(s.records).where(s.records.c.org_id==org,
                        s.records.c.id.in_(target_ids),s.records.c.deleted==False)).mappings():
                    try:targets[target['id']]=self._visible(policy,dict(target))
                    except NotFound:pass
            for link in links:
                record=by_id[link['record_id']]
                target=targets.get(link['target_id'])
                if target and link['kind'] in record['fields']:
                    record.setdefault('links',[]).append({'field':link['kind'],'record_id':target['id'],
                        'label':target['fields'].get('Name') or ' '.join(str(target['fields'].get(k) or '') for k in ('FirstName','LastName')).strip() or target['fields'].get('Title') or target['object_type']})
            card=self._card(c,org,object_type)
            readable=set(definition.get('properties',{})) if '*' in allowed else set(allowed)
            if caller.shared:
                readable={n for n in definition.get('properties',{}) if tview.has(n)}
            if public is not None:readable&=public
            out={'records':result,'next_offset':offset+limit if len(rows)>limit else None,
                 'card':{**card,'layout':visible_layout(card['layout'],readable) if card['layout'] else None},
                 'href':'/crm?'+urlencode({'object':object_type,'filters':json.dumps(filters or {},separators=(',',':'))})}
            return cross_org_answer('search',out) if caller.cross_org else out

    def resolve_name(self, org, actor, name):
        caller=as_caller(actor);actor=caller.subject
        """Case-insensitive entity names, preferring exact matches over readable prefixes."""
        from sqlalchemy import func
        name=name.strip().casefold()
        if not name:raise CRMError('Entity name is required')
        with self.engine.connect() as c:
            policy=self._policy(c,org,caller)
            fields=s.records.c.fields
            full_name=func.trim(func.coalesce(fields['FirstName'].as_string(),'')+' '+func.coalesce(fields['LastName'].as_string(),''))
            query=select(s.records).where(s.records.c.org_id==org,s.records.c.deleted==False,
                or_(*[func.lower(fields[k].as_string()).startswith(name,autoescape=True) for k in ('Name','name','Subject')],func.lower(full_name).startswith(name,autoescape=True))).order_by(s.records.c.id)
            matches=[];prefixes=[]
            for row in c.execute(query).mappings():
                try:record=self._visible(policy,dict(row))
                except NotFound:continue
                visible=record['fields']
                labels=[str(visible.get(k) or '') for k in ('Name','name','Subject')]
                labels.append(' '.join(str(visible.get(k) or '') for k in ('FirstName','LastName')).strip())
                exact=any(label.casefold()==name for label in labels)
                if not exact and not any(label.casefold().startswith(name) for label in labels):continue
                record['href']='/crm?'+urlencode({'record':record['id']})
                (matches if exact else prefixes).append(record)
            out={'records':matches or prefixes}
            return cross_org_answer('resolve',out) if caller.cross_org else out

    def _validate(self, definition, fields):
        errors = sorted(Draft202012Validator(definition).iter_errors(fields), key=lambda e: str(e.path))
        if errors:
            raise CRMError('Invalid fields: ' + errors[0].message)

    def _sync_relationships(self,c,row):
        org=row['org_id']
        definition=self._definition(c,org,row['object_type'])
        c.execute(delete(s.relationships).where(s.relationships.c.org_id==org,
            s.relationships.c.record_id==row['id'],s.relationships.c.target_domain=='crm'))
        for name,field in definition.get('properties',{}).items():
            targets=field.get('referenceTo',[])
            value=row['fields'].get(name)
            if not targets or not value:continue
            # Native records can reference another record by its stable CRM ID.
            target=c.execute(select(s.records.c.id).where(s.records.c.org_id==org,
                s.records.c.id==value,s.records.c.object_type.in_(targets),
                s.records.c.deleted==False)).scalar_one_or_none()
            if target:
                c.execute(s.relationships.insert().values(org_id=org,id=str(uuid4()),record_id=row['id'],
                    kind=name,target_domain='crm',target_id=target))
                continue
            # Source references resolve within the importing source org, never across orgs.
            sources=c.execute(select(s.source_mappings.c.system,s.source_mappings.c.source_org).where(
                s.source_mappings.c.org_id==org,s.source_mappings.c.record_id==row['id'])).all()
            for system,source_org in sources:
                target=c.execute(select(s.source_mappings.c.record_id).where(s.source_mappings.c.org_id==org,
                    s.source_mappings.c.system==system,s.source_mappings.c.source_org==source_org,
                    s.source_mappings.c.object_type.in_(targets),s.source_mappings.c.source_id==value)).scalar_one_or_none()
                if target:
                    c.execute(s.relationships.insert().values(org_id=org,id=str(uuid4()),record_id=row['id'],
                        kind=name,target_domain='crm',target_id=target))

        # Stable Markdown record URLs are graph edges; prose and workspace URLs stay in the document.
        from .markdown_links import crm_references
        for target in crm_references(row['narrative']):
            exists=c.execute(select(s.records.c.id).where(s.records.c.org_id==org,
                s.records.c.id==target,s.records.c.deleted==False)).scalar_one_or_none()
            if exists:
                c.execute(s.relationships.insert().values(org_id=org,id=str(uuid4()),record_id=row['id'],
                    kind='Description',target_domain='crm',target_id=target))

    def _revision(self, c, org, actor, before, after, reason, evidence, decision=None):
        """One append-only revision and its fact. ``decision`` (D13) makes it a decision record:
        ``{decision, credential_class, nonce, content_hash}``."""
        record = {k: str((decision or {}).get(k) or '') for k in ('decision', 'credential_class', 'nonce', 'content_hash')}
        c.execute(s.revisions.insert().values(org_id=org, record_id=after['id'], revision=after['revision'],
            actor_id=actor, created_at=datetime.now(timezone.utc).isoformat(), reason=reason,
            before=before, after=after, evidence=evidence, **record))
        event_id=str(uuid4())
        c.execute(s.outbox.insert().values(org_id=org, id=event_id, record_id=after['id'], revision=after['revision'],
            event={'event_type':'crm.record.changed', 'source_event_id':event_id,
                   'subject_refs':{'org_id':org,'record_id':after['id'],'revision':after['revision'],'uid':actor},
                   'envelope':self._envelope(c, org, after)}, delivered=False))

    def _envelope(self, c, org, row):
        """D8: WHO a fact reaches, computed by its producer (this store) into the fact envelope
        (``event.v2``; the outbox seals it, flows admits by subject ∪ audience). A card's audience is
        its next actor, its requester, its owner, its members and the principals of the workspaces
        whose boards hold it; its envelope carries the enumerated lifecycle coordination fields and
        NO content (C8, M5: no title, no summary, no field value). Any other record reaches its owner."""
        fields = row.get('fields') or {}
        is_card = bool(self._definition(c, org, row['object_type']).get('x-card'))
        if not is_card:
            return {'v': 2, 'producer': 'crm', 'object': 'record', 'owner': str(row['owner_id']),
                    'visibility': 'owner', 'audience': [str(row['owner_id'])]}
        m = s.card_members
        members = [r for (r,) in c.execute(select(m.c.subject).where(m.c.org_id == org, m.c.record_id == row['id'])
                                           .order_by(m.c.subject))]
        bindings = self._bindings(c, org, row['id'])
        people = [fields.get('NextActor'), fields.get('Requester'), row['owner_id'], *members]
        audience = list(dict.fromkeys(str(p) for p in people if p)) + ['ws:' + w for w in bindings]
        lifecycle = {'kind': fields.get('Kind') or '', 'state': fields.get('State') or '',
                     'next_actor': fields.get('NextActor') or '', 'next_check_at': fields.get('NextCheckAt') or '',
                     'handoff': fields.get('Handoff') or ''}
        return {'v': 2, 'producer': 'crm', 'object': 'card', 'owner': str(row['owner_id']),
                'visibility': 'workspace' if bindings else 'members', 'audience': audience[:200],
                # an empty coordination value (no next actor on a finished card) is omitted, never sent
                # as "" — the sealed shape takes 1..200-character strings only (event.v2)
                'lifecycle': {k: str(v) for k, v in lifecycle.items() if v}}

    def create(self, org, actor, object_type, fields, narrative='', reason='', evidence=None, idempotency_key='', match=None):
        caller=as_caller(actor);actor=caller.subject
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            if caller.unwatched:
                raise Refused(REFUSAL)
            rule=policy.get('objects',{}).get(object_type,{})
            if not policy.get('admin') and not rule.get('create'):
                raise Forbidden('Create not permitted')
            allowed=['*'] if policy.get('admin') else rule.get('write_fields',[])
            if '*' not in allowed and (set(fields)-set(allowed) or narrative):
                raise Forbidden('Field write not permitted')
            fields=self._card_fields(c,org,object_type,fields,None)
            self._validate(self._definition(c,org,object_type), fields)
            record_id=str(uuid5(NAMESPACE_URL,json.dumps(['crm-create',org,actor,idempotency_key]))) if idempotency_key else str(uuid4())
            if idempotency_key:
                # Serialize concurrent retries without a process-local lock or another table.
                if self.engine.dialect.name=='postgresql':
                    from sqlalchemy import text
                    c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':record_id})
                original=c.execute(select(s.revisions.c.after).where(s.revisions.c.org_id==org,
                    s.revisions.c.record_id==record_id,s.revisions.c.revision==1)).scalar_one_or_none()
                if original:
                    if original['object_type']!=object_type or original['fields']!=fields or original['narrative']!=narrative:
                        raise Conflict('Idempotency key was used for a different create request')
                    self._rights(policy,self._row(c,org,record_id),'read')
                    return {'status':'saved','record_id':record_id,'revision':1,'replayed':True}
            row=dict(org_id=org,id=record_id,object_type=object_type,owner_id=actor,
                     revision=1,fields=fields,narrative=narrative,quarantined=False,deleted=False)
            c.execute(s.records.insert().values(**row))
            self.add_match_ids(c,org,record_id,match)
            self.audit_names(record_id)
            self._sync_relationships(c,row)
            self._revision(c,org,actor,None,row,reason,evidence or [])
            return {'status':'saved','record_id':row['id'],'revision':1}

    def _edit(self,c,org,actor,policy,record_id,expected_revision,fields,narrative,reason,evidence):
        row=self._row(c,org,record_id)
        self._rights(policy,row,'read')
        allowed=self._rights(policy,row,'write')
        if '*' not in allowed and (set(fields)-set(allowed) or narrative is not None):
            raise Forbidden('Field write not permitted')
        if row['revision'] != expected_revision:
            raise Conflict('Record changed; read the current revision')
        fields=self._card_fields(c,org,row['object_type'],fields,row['fields'])
        after={**row,'fields':{**row['fields'],**fields},'revision':row['revision']+1}
        if narrative is not None:
            after['narrative']=narrative
        self._validate(self._definition(c,org,row['object_type']),after['fields'])
        changed=c.execute(update(s.records).where(s.records.c.org_id==org,s.records.c.id==record_id,
                          s.records.c.revision==expected_revision).values(**after)).rowcount
        if changed != 1:
            raise Conflict('Record changed; read the current revision')
        self._sync_relationships(c,after)
        self._revision(c,org,actor,row,after,reason,evidence)
        return {'status':'saved','record_id':record_id,'revision':after['revision']}

    def change(self,org,actor,record_id,expected_revision,fields,narrative=None,reason='',evidence=None,propose=False):
        caller=as_caller(actor);actor=caller.subject
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            if caller.delegated and not propose:
                # ADR-0047 d7: an agent proposes; a person accepts. Policy may let an agent with a
                # person in the loop write named fields directly (agent_direct_fields).
                direct=set(policy.get('agent_direct_fields',[]))
                if caller.unwatched or narrative is not None or not fields or not set(fields)<=direct:
                    propose=True
                elif 'State' in fields and self._is_card(c,org,record_id):
                    propose=True        # desktop D2 (founder Q3): a card's stage is always a person's to accept
            if propose:
                row=self._row(c,org,record_id)
                view=self._view(policy,row)
                if row['revision']!=expected_revision:
                    raise Conflict('Record changed; read the current revision')
                if any(not view.has(k) for k in fields) or (not view.narrative and narrative is not None):
                    raise Forbidden('Cannot propose changes to unreadable fields')
                fields=self._card_fields(c,org,row['object_type'],fields,row['fields'])
                self._validate(self._definition(c,org,row['object_type']),{**row['fields'],**fields})
                proposal=str(uuid4())
                c.execute(s.change_proposals.insert().values(org_id=org,id=proposal,record_id=row['id'],
                    base_revision=expected_revision,author_id=actor,patch={'fields':fields,'narrative':narrative,'author_kind':caller.kind,
                        'session':caller.session,'created_at':self._now()},
                    evidence=evidence or [],reason=reason,status='pending'))
                return {'status':'proposed','proposal_id':proposal}
            if policy.get('review_required') and not policy.get('admin'):
                raise Forbidden('This caller must submit a proposal')
            return self._edit(c,org,actor,policy,record_id,expected_revision,fields,narrative,reason,evidence or [])

    def history(self,org,actor,record_id):
        caller=as_caller(actor);actor=caller.subject
        with self.engine.connect() as c:
            policy=self._policy(c,org,caller)
            row=self._row(c,org,record_id)
            view=self._view(policy,row)
            result=[]
            for revision in c.execute(select(s.revisions).where(s.revisions.c.org_id==org,
                    s.revisions.c.record_id==record_id).order_by(s.revisions.c.revision.desc()).limit(100)).mappings():
                # Reason/evidence/snapshots can reveal restricted fields. Partial readers get
                # filtered snapshots, but no free-form reason/evidence or historical ownership.
                result.append({'revision':revision['revision'],'created_at':revision['created_at'],
                    'before':self._visible(policy,revision['before']) if revision['before'] else None,
                    'after':self._visible(policy,revision['after']),
                    'reason':revision['reason'] if view.full else None,
                    'evidence':revision['evidence'] if view.full else []})
            out={'revisions':result}
            return cross_org_answer('history',out) if caller.cross_org else out

    def review(self,org,actor,proposal_id,accept):
        caller=as_caller(actor);actor=caller.subject
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            if caller.delegated:
                raise Refused(CONFIRM_REFUSAL)
            p=c.execute(select(s.change_proposals).where(s.change_proposals.c.org_id==org,
                         s.change_proposals.c.id==proposal_id).with_for_update()).mappings().first()
            if not p:
                raise NotFound('Proposal not found')
            if p['status']!='pending':
                raise Conflict('Proposal already reviewed')
            if p['record_id'] is None or self._is_card(c,org,p['record_id']):
                # Desktop D2: a proposed card, or a change to a card — its members (and the
                # workspace's writers, for a proposed card) review it; cards_ops says who may.
                if self.cards is None:
                    raise NotFound('Proposal not found')
                result=self.cards.review(c,org,caller,policy,dict(p),accept)
                c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id==org,
                    s.change_proposals.c.id==proposal_id).values(status='accepted' if accept else 'rejected'))
                return result
            if not policy.get('admin') and not policy.get('reviewer'):
                raise Forbidden('Review not permitted')
            self._rights(policy,self._row(c,org,p['record_id']),'write')
            result={'status':'rejected','proposal_id':proposal_id}
            if accept:
                result=self._edit(c,org,actor,policy,p['record_id'],p['base_revision'],
                    p['patch']['fields'],p['patch']['narrative'],p['reason'],p['evidence'])
            c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id==org,
                s.change_proposals.c.id==proposal_id).values(status='accepted' if accept else 'rejected'))
            return result

    # ── Desktop D2: card types keep their lifecycle and a sanitised face on every write path ───
    def _is_card(self, c, org, record_id):
        kind=c.execute(select(s.records.c.object_type).where(s.records.c.org_id==org,
            s.records.c.id==record_id)).scalar_one_or_none()
        return bool(kind) and bool(self._definition(c,org,kind).get('x-card'))

    def _card_fields(self, c, org, object_type, fields, before):
        if self.cards is None or not self._definition(c,org,object_type).get('x-card'):
            return fields
        return self.cards.normalise_fields(object_type, fields, before)

    # ── ADR-0047: organisation, accounts, merges, bindings, references, audit ──────────────────
    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _merged_ids(self, c, org, record_id):
        """The record and every record merged into it, so a page that names an old id still counts."""
        ids=[record_id];frontier=[record_id]
        for _ in range(8):
            if not frontier:break
            frontier=[r for (r,) in c.execute(select(s.records.c.id).where(s.records.c.org_id==org,
                s.records.c.merged_into.in_(frontier))).all()]
            ids+=frontier
        return ids

    def _bindings_of(self, c, org, workspace):
        b=s.workspace_bindings
        return sorted(r for (r,) in c.execute(select(b.c.record_id).where(b.c.org_id==org,b.c.workspace==workspace)).all())

    def _bindings(self, c, org, record_id):
        b=s.workspace_bindings
        return sorted(w for (w,) in c.execute(select(b.c.workspace).where(b.c.org_id==org,b.c.record_id==record_id)).all())

    def _mentioned_in(self, c, org, policy, row):
        """Decision 5: backlinks only from workspaces the caller is a member of — others are omitted
        entirely, with no count. An administrator sees all, unless a worker acts for them: a worker
        sees only its ceiling, and a turn other people read only their intersection."""
        caller=policy.get('_caller') or as_caller(policy.get('subject_id',''))
        r=s.page_references
        query=select(r.c.workspace,r.c.page).where(r.c.org_id==org,r.c.record_id.in_(self._merged_ids(c,org,row['id'])))
        if not (policy.get('admin') and not caller.delegated):
            visible=set(caller.workspaces)
            if not caller.delegated:
                # A person's own desk is in no membership list; its roster names them its owner.
                ro=s.workspace_rosters
                visible|={w for (w,) in c.execute(select(ro.c.workspace).where(ro.c.org_id==org,
                    ro.c.subject==caller.subject,ro.c.role=='owner')).all()}
            if not visible:return []
            query=query.where(r.c.workspace.in_(visible))
        seen=set();out=[]
        for workspace,page in c.execute(query.order_by(r.c.workspace,r.c.page)).all():
            if (workspace,page) in seen:continue
            seen.add((workspace,page));out.append({'workspace':workspace,'page':page})
        return out

    def org_policy(self, c, org):
        return c.execute(select(s.orgs.c.default_policy).where(s.orgs.c.org_id==org)).scalar_one_or_none()

    def ensure_account(self, org, actor):
        """Decision 3, as D35 reads it: resolve the signed-in account to its Person — by the account's
        verified email among the match identifiers first (I10), else by the record it is attached to,
        else by creating the person. A Person that already has another account attached gets this one
        LINKED (``persons.link``: an ``account:`` identifier, and the merge card the person confirms),
        never a second record. Only an address identity says a sign-in proved (``email_verified``)
        matches a record or becomes one's ``Email`` and match identifier; an unproven one only names
        the person created. Returns the person's record id, or None where the organisation has no
        Person type yet."""
        caller=as_caller(actor)
        if caller.internal or not caller.subject:
            return None
        email=caller.email if caller.email_verified else ''
        with self.engine.begin() as c:
            if email:
                matched=self.persons.resolve(c,org,'',email)
                if matched is not None:
                    if matched['account_id']==caller.subject:
                        self.persons.offer(c,org,matched,{caller.subject:email})
                        return matched['id']
                    attached_elsewhere=c.execute(select(s.records.c.id).where(s.records.c.org_id==org,
                        s.records.c.account_id==caller.subject)).scalar_one_or_none()
                    if not matched['account_id'] and not attached_elsewhere:
                        before=dict(matched);after={**before,'account_id':caller.subject,'revision':before['revision']+1}
                        c.execute(update(s.records).where(s.records.c.org_id==org,s.records.c.id==matched['id'])
                            .values(account_id=caller.subject,revision=after['revision']))
                        self._revision(c,org,caller.subject,before,after,'Account attached on sign-in (verified email)',[])
                        return matched['id']
                    self.persons.link(c,org,matched,caller.subject,email)
                    return matched['id']
            found=self.persons.resolve(c,org,caller.subject)
            if found is not None:return found['id']
            if c.execute(select(s.object_types.c.name).where(s.object_types.c.org_id==org,
                    s.object_types.c.name=='Person')).scalar_one_or_none() is None:
                return None
            if self.engine.dialect.name=='postgresql':
                c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'crm-account-'+caller.subject})
                found=c.execute(select(s.records.c.id).where(s.records.c.org_id==org,
                    s.records.c.account_id==caller.subject)).scalar_one_or_none()
                if found:return found
            record_id=str(uuid5(NAMESPACE_URL,json.dumps(['crm-account',org,caller.subject])))
            fields={'Name':caller.email or caller.subject}
            if email:fields['Email']=email
            row=dict(org_id=org,id=record_id,object_type='Person',owner_id=caller.subject,revision=1,fields=fields,
                     narrative='',quarantined=False,deleted=False,account_id=caller.subject,merged_into=None)
            c.execute(s.records.insert().values(**row))
            if email and c.execute(select(s.match_ids.c.record_id).where(s.match_ids.c.org_id==org,
                    s.match_ids.c.identifier=='email:'+email)).scalar_one_or_none() is None:
                c.execute(s.match_ids.insert().values(org_id=org,identifier='email:'+email,record_id=record_id))
            self._revision(c,org,caller.subject,None,row,'Person created on sign-in',[])
            return record_id

    def add_match_ids(self, c, org, record_id, identifiers):
        for raw in identifiers or []:
            identifier=normalise_match(raw)
            held=c.execute(select(s.match_ids.c.record_id).where(s.match_ids.c.org_id==org,
                s.match_ids.c.identifier==identifier)).scalar_one_or_none()
            if held==record_id:continue
            if held:raise Conflict('Identifier already belongs to another record; search for it or merge')
            c.execute(s.match_ids.insert().values(org_id=org,identifier=identifier,record_id=record_id))

    def merge(self, org, actor, record_id, into, reason=''):
        """Decision 3: duplicates merge. The merged record names its survivor, its identifiers and
        workspace bindings move to the survivor, and every read of its id answers the survivor.

        A merge moves data, never an account. A record an account answers to (its attached
        ``account_id`` or an ``account:`` link) is someone's sign-in; it joins another Person only on
        that person's own merge card (``persons.Persons.confirm``), confirmed from each account's
        session. So a record carrying an account binding never merges away here; merging the other
        way, a duplicate INTO the record the account answers to, keeps the account where it is."""
        caller=as_caller(actor);actor=caller.subject
        if not reason.strip():raise CRMError('A merge requires a reason')
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            if caller.delegated:raise Refused(CONFIRM_REFUSAL)
            loser=self._row(c,org,record_id);winner=self._row(c,org,into)
            if loser['id']==winner['id']:raise CRMError('A record cannot merge into itself')
            if loser['object_type']!=winner['object_type']:raise CRMError('Only records of one type merge')
            for row in (loser,winner):
                if '*' not in self._rights(policy,row,'write'):raise Forbidden('Merge needs write access to every field of both records')
            if loser['account_id'] or c.execute(select(s.match_ids.c.identifier).where(s.match_ids.c.org_id==org,
                    s.match_ids.c.record_id==loser['id'],s.match_ids.c.identifier.like('account:%')).limit(1)).first():
                raise Conflict('This record is a person\'s sign-in: it joins another person only on their own merge '
                               'card. Merge the duplicate into this record instead')
            after={**loser,'merged_into':winner['id'],'revision':loser['revision']+1}
            c.execute(update(s.records).where(s.records.c.org_id==org,s.records.c.id==loser['id'],
                s.records.c.revision==loser['revision']).values(merged_into=winner['id'],revision=after['revision']))
            c.execute(update(s.match_ids).where(s.match_ids.c.org_id==org,s.match_ids.c.record_id==loser['id']).values(record_id=winner['id']))
            b=s.workspace_bindings
            have=set(self._bindings(c,org,winner['id']))
            for workspace in self._bindings(c,org,loser['id']):
                if workspace not in have:
                    c.execute(b.insert().values(org_id=org,workspace=workspace,record_id=winner['id'],created_by=actor,created_at=self._now()))
            c.execute(delete(b).where(b.c.org_id==org,b.c.record_id==loser['id']))
            self._revision(c,org,actor,loser,after,reason,[{'merged_into':winner['id']}])
            return {'status':'merged','record_id':loser['id'],'merged_into':winner['id']}

    def profile_link(self, org, actor, record_id, handle=None, linked=True):
        """R4 (``profiles.py``): link this record to a published public profile, or unlink it. Only
        someone who may CHANGE the record does it, never a worker alone. One profile per record."""
        from .profiles import DOMAIN, KIND
        caller=as_caller(actor)
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            row=self._row(c,org,record_id)
            self._rights(policy,row,'write')
            if caller.unwatched:raise Refused(REFUSAL)
            r=s.relationships
            old=c.execute(select(r.c.target_id).where(r.c.org_id==org,r.c.record_id==row['id'],
                r.c.kind==KIND,r.c.target_domain==DOMAIN)).scalar_one_or_none()
            c.execute(delete(r).where(r.c.org_id==org,r.c.record_id==row['id'],r.c.kind==KIND,r.c.target_domain==DOMAIN))
            if linked:
                c.execute(r.insert().values(org_id=org,id=str(uuid4()),record_id=row['id'],kind=KIND,
                    target_domain=DOMAIN,target_id=handle))
            return {'record_id':row['id'],'handle':handle if linked else None,'previous':old}

    def profile_link_of(self, org, actor, record_id):
        """The handle this record links to, if the caller may read the record (else NotFound)."""
        from .profiles import DOMAIN, KIND
        caller=as_caller(actor)
        with self.engine.connect() as c:
            policy=self._policy(c,org,caller)
            row=self._row(c,org,record_id)
            self._rights(policy,row,'read')
            r=s.relationships
            return row['id'],c.execute(select(r.c.target_id).where(r.c.org_id==org,r.c.record_id==row['id'],
                r.c.kind==KIND,r.c.target_domain==DOMAIN)).scalar_one_or_none()

    def bind(self, org, actor, workspace, record_id, bound=True):
        """Decision 6: an administrator binds a workspace to a record; its members get derived
        grants, which end when their membership does. Binding grants nothing by itself."""
        caller=as_caller(actor);actor=caller.subject
        workspace=check_workspace(workspace)
        with self.begin() as c:
            policy=self._policy(c,org,caller)
            if not policy.get('admin'):raise Forbidden('CRM administrator rights required to bind a workspace')
            if caller.unwatched:raise Refused(REFUSAL)
            row=self._row(c,org,record_id)
            b=s.workspace_bindings
            exists=c.execute(select(b.c.workspace).where(b.c.org_id==org,b.c.workspace==workspace,b.c.record_id==row['id'])).scalar_one_or_none()
            if bound and not exists:
                c.execute(b.insert().values(org_id=org,workspace=workspace,record_id=row['id'],created_by=actor,created_at=self._now()))
            elif not bound and exists:
                c.execute(delete(b).where(b.c.org_id==org,b.c.workspace==workspace,b.c.record_id==row['id']))
            return {'status':'bound' if bound else 'unbound','workspace':workspace,'record_id':row['id'],
                    'bound_to':self._bindings(c,org,row['id'])}

    def apply_workspace_snapshot(self, org, workspace, commit, committed_at, references, members=None):
        """Decision 5, the CRM's half: replace one workspace's references (and roster) with the full
        set at one commit. Idempotent, and an older commit never overwrites a newer one."""
        workspace=check_workspace(workspace)
        refs=set()
        for ref in references or []:
            page=str(ref.get('page') or '').strip();record=str(ref.get('record') or ref.get('record_id') or '').strip()
            if not page or len(page)>500 or not record or len(record)>100:raise CRMError('A reference names a page and a record')
            refs.add((page,record))
        stamp=str(committed_at or '')
        with self.begin() as c:
            h=s.workspace_heads
            head=c.execute(select(h.c.commit,h.c.updated_at).where(h.c.org_id==org,h.c.workspace==workspace)).first()
            if head and stamp and head[1] and stamp<head[1]:
                return {'status':'stale','workspace':workspace,'commit':head[0]}
            r=s.page_references
            c.execute(delete(r).where(r.c.org_id==org,r.c.workspace==workspace))
            for page,record in sorted(refs):
                c.execute(r.insert().values(org_id=org,workspace=workspace,page=page,record_id=record))
            if members is not None:
                ro=s.workspace_rosters
                c.execute(delete(ro).where(ro.c.org_id==org,ro.c.workspace==workspace))
                seen=set()
                for m in members:
                    subject=str(m.get('subject') or '').strip();role=str(m.get('role') or '').strip().lower()
                    if not subject or subject in seen or role not in ('owner','contributor','viewer'):continue
                    seen.add(subject);c.execute(ro.insert().values(org_id=org,workspace=workspace,subject=subject,role=role))
            if head:
                c.execute(update(h).where(h.c.org_id==org,h.c.workspace==workspace).values(commit=str(commit or ''),updated_at=stamp))
            else:
                c.execute(h.insert().values(org_id=org,workspace=workspace,commit=str(commit or ''),updated_at=stamp))
            return {'status':'applied','workspace':workspace,'references':len(refs),'members':None if members is None else len(seen)}

    def erase_org(self, org):
        """Erase an individual's personal CRM (``p-<id>``, on account delete; any other org is
        refused), in one transaction, by ``migrate.erase_plan``: every row keyed to the org is
        deleted, children before parents, EXCEPT the append-only tables, left untouched until their
        erasure is decided (``migrate.ERASE_APPEND_ONLY``) — the records their revisions point at stay
        as empty, deleted rows and the object definitions stay.

        crm-api's role has no DELETE on most of these tables, and none at all on the append-only
        ones, so on Postgres this is one call to ``crm_erase_personal_org``, a function the
        migrations path owns and lets that role execute (``migrate.erase_function_sql``).

        Answers ``{rows, removed, tombstoned, pinned, left}``: ``rows`` deleted in all, per table."""
        org = str(org or '')
        if not PERSONAL_ORG.fullmatch(org):
            raise CRMError("Only an individual account's own CRM (p-<id>) can be erased")
        if self.engine.dialect.name == 'postgresql':
            with self.engine.begin() as c:
                out = c.execute(text('SELECT crm_erase_personal_org(:org)'), {'org': org}).scalar_one()
            out = json.loads(out) if isinstance(out, str) else dict(out)
        else:
            out = {'rows': 0, 'removed': {}, 'tombstoned': {}, 'pinned': {}, 'left': {}}
            with self.engine.begin() as c:
                for name, action in erase_plan(ERASE_APPEND_ONLY):
                    table = s.metadata.tables[name]
                    where = table.c.org_id == org
                    if action == 'delete':
                        n = c.execute(delete(table).where(where)).rowcount or 0
                        out['removed'][name] = n
                        out['rows'] += n
                    elif action == 'tombstone':
                        out['tombstoned'][name] = c.execute(update(table).where(where).values(
                            fields={}, narrative='', deleted=True, account_id=None, merged_into=None,
                            owner_id='')).rowcount or 0
                    else:
                        n = c.execute(select(func.count()).select_from(table).where(where)).scalar_one()
                        out['left' if action == 'leave' else 'pinned'][name] = n
        if any(out['left'].values()):
            log.warning('crm erase %s: append-only rows left in place pending a decision: %s', org,
                        ', '.join(f'{t}={n}' for t, n in sorted(out['left'].items()) if n))
        return out

    def audit(self, org, actor, op, record_id='', outcome='ok'):
        caller=as_caller(actor)
        with self.engine.begin() as c:
            c.execute(s.audit.insert().values(org_id=org,id=str(uuid4()),at=self._now(),subject=caller.subject or '-',
                caller=caller.kind,session=caller.session,trace=caller.trace,op=op,record_id=str(record_id or ''),outcome=outcome,
                **audit_principal(caller)))


def audit_principal(caller):
    """D4/H9: who a read was for. A shared caller's principal is its workspace (``ws:<slug>``; the
    ``ws:`` subject itself once identity mints one, S1-B); a worker acts on behalf of its person (the
    speaker once the delegation carries ``on_behalf_of``); ``workspace`` is the ceiling."""
    subject=str(caller.subject or '')
    ceiling=','.join(caller.workspaces)
    if subject.startswith('ws:'):
        principal=subject
    elif caller.shared:
        principal='ws:'+(caller.workspaces[0] if caller.workspaces else '-')
    else:
        principal=subject or ('internal' if caller.internal else '-')
    on_behalf_of=str((caller.extra or {}).get('on_behalf_of') or '')
    if not on_behalf_of and caller.delegated and not subject.startswith('ws:'):
        on_behalf_of=subject
    return {'principal':principal,'on_behalf_of':on_behalf_of,'workspace':ceiling if caller.delegated else ''}


_WORKSPACE=__import__('re').compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$')

def check_workspace(workspace):
    workspace=str(workspace or '').strip()
    if not _WORKSPACE.fullmatch(workspace) or '..' in workspace:
        raise CRMError('Not a workspace slug')
    return workspace

def normalise_match(raw):
    """`email:a@b.c`, `domain:b.c`, `calendar:<event id>` — kind and value, the value lower-cased
    for emails and domains, so one address is one identifier however it was typed."""
    kind,_,value=str(raw or '').partition(':')
    kind=kind.strip().lower();value=value.strip()
    if kind not in ('email','domain','calendar') or not value or len(value)>300:
        raise CRMError('A match identifier is email:…, domain:… or calendar:…')
    if kind in ('email','domain'):value=value.lower()
    return kind+':'+value
