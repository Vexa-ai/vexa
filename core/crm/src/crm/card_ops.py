"""Cards on the CRM (desktop ADR phase D2): one shared registry record per card, never a copy.

A **card type** is an object definition marked ``"x-card": true`` (bootstrap adds ``Card``). A card:

- **moves through its type's lifecycle**, declared in ``_global/POLICIES.md`` as
  ``lifecycles.<type>`` (``To do > Doing > Done | Dropped`` by default: the stages in order, then side
  states). Its ``State`` is only ever a declared state, on every write path — the card routes and
  ``crm_change`` alike. An agent never moves a card: its stage change is a proposal a person accepts
  (founder, ADR Q3);
- **is shared by membership** (``crm_card_members``): a person added is ``invited`` (they read the
  card and it waits on their desk) until they accept and become a ``member``, who writes it.
  Membership grants the card, never what it is about (``About``);
- **is on a workspace's board** when that workspace references it — a ``crm_workspace_bindings`` row,
  so the workspace's members read it and its contributors write it (ADR-0047 d6, unchanged). A person
  references a card only from a workspace they can write;
- **has sub-cards**: a card whose ``Parent`` names it (``crm_relationships`` kind ``Parent``);
- **has a face** composed from blocks.v1's face profile (``card_face.py``), sanitised on every write
  and resolved per viewer, so a face never shows a field its viewer could not read;
- **is proposed by agents** (``card_propose``: a ``crm_change_proposals`` row with no record yet;
  ``card_update_propose``: an ordinary change proposal). A person accepts with ``crm_review``: for a
  proposed card, someone who can write the workspace it was proposed into, or — proposed into no
  workspace — the person the agent acted for. Proposals are capped and expire by the desk's limits.

Its movement and activity are the record's own revisions; nothing else is stored for history.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import delete, select, update

from . import card_face
from . import features
from . import schema as s
from .caller import Caller, as_caller
from .policies import DEFAULTS as LIMIT_DEFAULTS, front_matter
from .store import CRMError, Conflict, Forbidden, NotFound, Refused, SharedRefused, check_workspace, public_fields

DEFAULT_TYPE = 'Card'
DEFAULT_LIFECYCLE = {'stages': ['To do', 'Doing', 'Done'], 'side': ['Dropped']}
#: A card that has not moved for this long, and is not finished, shows as stalled. `_global` policy
#: `card_stalled_days` sets it (a whole number of days, at least 1); anything else is this default.
STALLED_AFTER = timedelta(days=14)
LIST_MAX = 50
TITLE_MAX = 200
REASON_MAX = 500
#: The fields a person or a proposal may set on a card. State moves only by `transition`.
EDITABLE = ('Title', 'About', 'Next', 'Waiting', 'Summary', 'Due', 'Progress', 'Checklist', 'Parent', 'Face')

_ID = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,127}$')
_SLUG = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f\\]')
_STATE = re.compile(r'^[^\x00-\x1f\x7f|>]{1,60}$')


def _refusal(reason, instruction, **extra):
    return {'status': 'refused', 'reason': reason, 'instruction': instruction, **extra}


PROPOSE_CARD = _refusal(
    'propose_instead',
    'You may not make or change a card yourself. Call card_propose (a new card) or card_update_propose '
    '(a change or a stage move) with a one-line reason; a person accepts it. Do not retry it another way.')
MEMBERS_PERSON_ONLY = _refusal(
    'person_confirmation_required',
    "Only a person adds or removes a card's members, or accepts being added. Tell them, and give the "
    "card's link; do not retry it another way.")


def pending_full(n):
    return _refusal('proposals_pending_max',
                    f'{n} card proposals from this person already wait for an answer. Do not add another; '
                    'mention the waiting ones instead.')


def daily_full(n):
    return _refusal('proposals_daily_max',
                    f'This session has made its {n} card proposals for today. Stop proposing.')


# ── the lifecycle ─────────────────────────────────────────────────────────────────────────────
def parse_lifecycle(value):
    """``Stage > Stage > Stage | Side | Side`` — at least two stages; anything else is the default."""
    text = str(value or '').strip()
    if not text:
        return {k: list(v) for k, v in DEFAULT_LIFECYCLE.items()}
    main, _, rest = text.partition('|')
    stages = [x.strip() for x in main.split('>') if x.strip()]
    side = [x.strip() for x in rest.split('|') if x.strip()] if rest else []
    names = stages + side
    if len(stages) < 2 or len(set(n.casefold() for n in names)) != len(names) \
            or not all(_STATE.fullmatch(n) for n in names) or len(names) > 12:
        return {k: list(v) for k, v in DEFAULT_LIFECYCLE.items()}
    return {'stages': stages, 'side': side}


class PolicyText:
    """``_global/POLICIES.md`` as text, through the desk's policy door (``policies.Policies.fetch``),
    kept 30 seconds; None when there is no door or it does not answer (the defaults then apply)."""

    def __init__(self, policies, ttl=30.0, now=time.monotonic):
        self.policies, self.ttl, self.now = policies, ttl, now
        self._at, self._text, self._lock = None, None, threading.Lock()

    def __call__(self):
        with self._lock:
            if self._at is None or self.now() - self._at >= self.ttl:
                try:
                    self._text = self.policies.fetch() if self.policies is not None else None
                except Exception:  # noqa: BLE001 — a policy we cannot read is the default
                    self._text = None
                self._at = self.now()
            return self._text


# ── refs ──────────────────────────────────────────────────────────────────────────────────────
def check_about(ref):
    """``page:<workspace>/<path>`` · ``chat:<id>`` · ``workspace:<slug>`` · ``meeting:<id>`` · ``crm:<id>``."""
    if not isinstance(ref, str) or ':' not in ref or len(ref) > 600:
        raise CRMError('About is a reference: page:<workspace>/<path>, chat:<id>, workspace:<slug>, '
                       'meeting:<id> or crm:<record id>')
    kind, _, rest = ref.partition(':')
    if kind in ('chat', 'meeting', 'crm') and _ID.fullmatch(rest):
        return {'kind': kind, 'ref': ref}
    if kind == 'workspace' and _SLUG.fullmatch(rest) and '..' not in rest:
        return {'kind': kind, 'ref': ref}
    if kind == 'page':
        slug, _, path = rest.partition('/')
        parts = path.split('/')
        if (_SLUG.fullmatch(slug) and '..' not in slug and path and len(path) <= 500
                and not _CONTROL.search(path) and all(p not in ('', '.', '..') for p in parts)):
            return {'kind': kind, 'ref': ref}
    raise CRMError('About is a reference: page:<workspace>/<path>, chat:<id>, workspace:<slug>, '
                   'meeting:<id> or crm:<record id>')


def card_id(ref):
    """The record id of a ``card:<id>`` desk ref."""
    return str(ref).split(':', 1)[1] if str(ref).startswith('card:') else ''


def _at(iso):
    at = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _clip(value, n=80):
    return card_face.plain(value, n)


def _writable(caller: Caller):
    return set(caller.writable if caller.writable is not None else caller.workspaces)


class Cards:
    def __init__(self, store, clock=None, policy_text=None, limits=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy_text = policy_text
        self._limits = limits
        store.cards = self

    @property
    def engine(self):
        return self.store.engine

    # ── policy ────────────────────────────────────────────────────────────────────────────────
    def lifecycle(self, object_type):
        attrs = {}
        if self.policy_text is not None:
            try:
                attrs = front_matter(self.policy_text() or '')
            except Exception:  # noqa: BLE001
                attrs = {}
        return parse_lifecycle(attrs.get('lifecycles.' + str(object_type).lower()) or attrs.get('lifecycles.card'))

    def stalled_after(self):
        attrs = {}
        if self.policy_text is not None:
            try:
                attrs = front_matter(self.policy_text() or '')
            except Exception:  # noqa: BLE001
                attrs = {}
        value = str(attrs.get('card_stalled_days', '')).strip()
        return timedelta(days=int(value)) if value.isdigit() and 1 <= int(value) <= 3650 else STALLED_AFTER

    def limits(self):
        """The desk's limits (``policies.py``), narrow-only; the defaults if the source fails."""
        out = dict(LIMIT_DEFAULTS)
        if self._limits is None:
            return out
        try:
            got = self._limits() or {}
        except Exception:  # noqa: BLE001
            return out
        for key, default in LIMIT_DEFAULTS.items():
            v = got.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= default:
                out[key] = v
        return out

    def _ttl(self, limits=None):
        return timedelta(days=(limits or self.limits())['card_proposal_ttl_days'])

    # ── the write-path hook (store._card_fields) ───────────────────────────────────────────────
    def normalise_fields(self, object_type, fields, before):
        fields = dict(fields or {})
        if 'Face' in fields:
            fields['Face'] = card_face.sanitize(fields['Face'])
        if fields.get('About'):
            check_about(fields['About'])
        life = self.lifecycle(object_type)
        states = life['stages'] + life['side']
        if 'State' in fields:
            if fields['State'] not in states:
                raise CRMError(f'"{_clip(fields["State"], 60)}" is not a stage of this card type; '
                               f'its stages are {", ".join(states)}')
        elif before is None:
            fields['State'] = life['stages'][0]
        return fields

    # ── reading one card ──────────────────────────────────────────────────────────────────────
    def _definition(self, c, org, object_type):
        definition = self.store._definition(c, org, object_type)
        if not definition.get('x-card'):
            raise CRMError('Not a card type')
        return definition

    def _card_row(self, c, org, record_id):
        row = self.store._row(c, org, str(record_id or ''))
        if not self.store._definition(c, org, row['object_type']).get('x-card'):
            raise NotFound('Card not found')
        return row

    def _members(self, c, org, record_id):
        m = s.card_members
        return [{'subject': r['subject'], 'status': r['status']} for r in c.execute(
            select(m.c.subject, m.c.status).where(m.c.org_id == org, m.c.record_id == record_id)
            .order_by(m.c.added_at, m.c.subject)).mappings()]

    def _describe(self, before, after, readable):
        def can(name):
            return '*' in readable or name in readable
        if before is None:
            return 'Made the card'
        b, a = before.get('fields') or {}, after.get('fields') or {}
        changed = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        if 'State' in changed and can('State'):
            return f'Moved to {_clip(a.get("State"), 60)}'
        if 'Next' in changed and can('Next') and a.get('Next'):
            return f'Next: {_clip(a.get("Next"))}'
        if 'Waiting' in changed and can('Waiting') and a.get('Waiting'):
            return f'Waiting on {_clip(a.get("Waiting"))}'
        if 'Title' in changed and can('Title'):
            return f'Renamed to {_clip(a.get("Title"))}'
        if before.get('merged_into') != after.get('merged_into'):
            return 'Merged'
        visible = [k for k in changed if can(k)]
        if visible == ['Face']:
            return 'Changed the face'
        if visible:
            return 'Updated ' + ', '.join(k.lower() for k in visible[:3])
        return 'Updated the card'

    def _movement(self, rev, readable):
        by, kind = rev['actor_id'], 'person'
        for item in rev['evidence'] or []:
            if isinstance(item, dict) and item.get('proposal'):
                by = str(item.get('author') or by)
                kind = 'agent' if str(item.get('author_kind') or '').startswith('agent') else 'person'
        return {'by': by, 'kind': kind, 'what': self._describe(rev['before'], rev['after'], readable),
                'at': rev['created_at']}

    def view(self, c, org, policy, row, activity=False):
        """One card as this viewer may read it (``CardView``).

        A room with people from outside the org (``caller.cross_org``) reads a card the way it reads
        any record: its public fields only (``store.public_fields``; for a card, its title). Its
        movement and activity (revision text: "Next: …", "Waiting on …", "Moved to …"), its members
        and the boards it is on are the org's own, so they are blank: ``movement`` None, ``activity``,
        ``members`` and ``workspaces`` empty, never stalled. The card list answers it no proposals."""
        store = self.store
        caller = policy.get('_caller') or as_caller(policy.get('subject_id', ''))
        cross_org = bool(getattr(caller, 'cross_org', False))
        visible = store._visible(policy, row)
        readable = set(store._rights(policy, row, 'read'))
        if cross_org:
            readable = public_fields(policy, row['object_type'])
        f = visible['fields']
        life = self.lifecycle(row['object_type'])
        can_write = False
        try:
            can_write = bool(store._rights(policy, row, 'write')) and not caller.delegated
        except NotFound:
            pass
        bindings = store._bindings(c, org, row['id'])
        if not policy.get('admin') or caller.delegated:
            bindings = [w for w in bindings if w in set(caller.workspaces)]
        if cross_org:
            bindings = []
        parent = None
        if f.get('Parent'):
            try:
                prow = self._card_row(c, org, f['Parent'])
                parent = {'id': prow['id'], 'title': store._visible(policy, prow)['fields'].get('Title') or 'A card'}
            except NotFound:
                parent = None
        items = []
        r = s.relationships
        for (child_id,) in c.execute(select(r.c.record_id).where(r.c.org_id == org, r.c.target_domain == 'crm',
                                                               r.c.target_id == row['id'], r.c.kind == 'Parent')):
            try:
                crow = self._card_row(c, org, child_id)
                cf = store._visible(policy, crow)['fields']
            except NotFound:
                continue        # a child this viewer cannot read is not counted (its existence would leak)
            clife = self.lifecycle(crow['object_type'])
            items.append({'id': crow['id'], 'title': cf.get('Title') or 'A card', 'state': cf.get('State') or '',
                          'done': cf.get('State') == clife['stages'][-1]})
        items.sort(key=lambda i: (i['done'], i['title'].casefold()))
        revs = [] if cross_org else c.execute(
            select(s.revisions).where(s.revisions.c.org_id == org, s.revisions.c.record_id == row['id'])
            .order_by(s.revisions.c.revision.desc()).limit(50 if activity else 1)).mappings().all()
        movement = self._movement(revs[0], readable) if revs else None
        state = f.get('State')
        finished = state == life['stages'][-1] or state in life['side']
        stalled = bool(movement) and not finished and self.clock() - _at(movement['at']) > self.stalled_after()
        seen = c.execute(select(s.card_seen.c.seen_at).where(s.card_seen.c.org_id == org, s.card_seen.c.person == caller.subject,
                                                              s.card_seen.c.record_id == row['id'])).first()
        new = seen is None and row['owner_id'] != caller.subject
        about = None
        if f.get('About'):
            try:
                about = check_about(f['About'])
            except CRMError:
                about = None
        face_source = f.get('Face') if ('*' in readable or 'Face' in readable) else None
        formats = {k: v.get('x-format') for k, v in self.store._definition(c, org, row['object_type'])
                   .get('properties', {}).items() if isinstance(v, dict) and v.get('x-format')}
        out = {
            'id': row['id'], 'ref': 'card:' + row['id'], 'type': row['object_type'], 'revision': row['revision'],
            'title': f.get('Title') or 'A card', 'about': about, 'state': state,
            'states': life['stages'], 'side_states': life['side'],
            'next': f.get('Next'), 'waiting': f.get('Waiting'), 'summary': f.get('Summary'),
            'due': f.get('Due'), 'progress': f.get('Progress'),
            'workspaces': bindings, 'parent': parent,
            'children': {'done': sum(1 for i in items if i['done']), 'total': len(items), 'items': items[:20]},
            'members': [] if cross_org else self._members(c, org, row['id']),
            'face': card_face.resolve(face_source, f, readable, formats),
            'movement': movement, 'stalled': stalled, 'new': new,
            'can': {'edit': can_write, 'transition': can_write, 'review': can_write, 'members': can_write},
            'href': '/crm?' + urlencode({'record': row['id']}),
        }
        if activity:
            out['activity'] = [self._movement(rev, readable) for rev in revs]
        return out

    def readable(self, c, org, caller, record_id):
        """Whether this caller may read this card now (the desk's ``place`` check)."""
        try:
            policy = self.store._policy(c, org, caller)
            self.store._visible(policy, self._card_row(c, org, record_id))
            return True
        except (NotFound, Forbidden, SharedRefused):
            return False

    def read(self, org, actor, card):
        """One card with its activity. The person opening it (never an agent reading it) marks it seen,
        so its "New" clears for them."""
        caller = as_caller(actor)
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            out = self.view(c, org, policy, row, activity=True)
            if not caller.delegated and not caller.internal and caller.subject:
                t, now = s.card_seen, self.clock().isoformat()
                where = (t.c.org_id == org, t.c.person == caller.subject, t.c.record_id == row['id'])
                if c.execute(select(t.c.seen_at).where(*where)).first() is None:
                    c.execute(t.insert().values(org_id=org, person=caller.subject, record_id=row['id'], seen_at=now))
                else:
                    c.execute(update(t).where(*where).values(seen_at=now))
            return out

    # ── the person's own verbs ────────────────────────────────────────────────────────────────
    def _new_fields(self, title, about, parent, fields, face):
        out = {k: v for k, v in (fields or {}).items() if k in EDITABLE and k not in ('Title', 'About', 'Parent', 'Face')}
        unknown = set(fields or {}) - set(EDITABLE)
        if unknown:
            raise CRMError('A card does not take ' + ', '.join(sorted(unknown)))
        title = _clip(title, TITLE_MAX)
        if not title:
            raise CRMError('A card needs a title')
        out['Title'] = title
        if about:
            check_about(about)
            out['About'] = about
        if parent:
            out['Parent'] = str(parent)
        if face is not None:
            out['Face'] = face
        return out

    def _insert(self, c, org, owner, object_type, fields, narrative, reason, evidence, workspace, members):
        store = self.store
        definition = self._definition(c, org, object_type)
        fields = self.normalise_fields(object_type, fields, None)
        store._validate(definition, fields)
        record_id = str(uuid4())
        row = dict(org_id=org, id=record_id, object_type=object_type, owner_id=owner, revision=1, fields=fields,
                   narrative=narrative or '', quarantined=False, deleted=False)
        c.execute(s.records.insert().values(**row))
        store._sync_relationships(c, row)
        store._revision(c, org, owner, None, row, reason, evidence)
        now = self.clock().isoformat()
        for subject in dict.fromkeys(members):
            c.execute(s.card_members.insert().values(org_id=org, record_id=record_id, subject=subject,
                                                     status='member', added_by=owner, added_at=now))
        if workspace:
            c.execute(s.workspace_bindings.insert().values(org_id=org, workspace=workspace, record_id=record_id,
                                                           created_by=owner, created_at=now))
        return record_id

    def _check_parent(self, c, org, policy, parent):
        if parent:
            try:
                self.store._visible(policy, self._card_row(c, org, parent))
            except (NotFound, SharedRefused):
                raise NotFound('Parent card not found') from None

    def create(self, org, actor, title, workspace=None, about=None, type=DEFAULT_TYPE, parent=None, fields=None,
               face=None, narrative=None):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            if workspace:
                workspace = check_workspace(workspace)
                if workspace not in _writable(caller) and not policy.get('admin'):
                    raise Forbidden('A card goes on the board of a workspace you can write')
            self._check_parent(c, org, policy, parent)
            data = self._new_fields(title, about, parent, fields, face)
            record_id = self._insert(c, org, caller.subject, type or DEFAULT_TYPE, data, narrative,
                                     'Made the card', [], workspace, [caller.subject])
            policy = self.store._policy(c, org, caller)
            return {'status': 'saved', 'card': self.view(c, org, policy, self._card_row(c, org, record_id))}

    def update(self, org, actor, card, expected_revision, fields=None, face=None, transition=None):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        patch = self._patch(fields, face, transition)
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            if 'Parent' in patch:
                self._check_parent(c, org, policy, patch['Parent'])
            reason = f'Moved to {transition}' if transition else 'Updated the card'
            self.store._edit(c, org, caller.subject, policy, row['id'], expected_revision, patch, None, reason, [])
            return {'status': 'saved', 'card': self.view(c, org, policy, self._card_row(c, org, row['id']))}

    def _patch(self, fields, face, transition):
        fields = dict(fields or {})
        if 'State' in fields:
            transition = transition or fields.pop('State')
        unknown = set(fields) - set(EDITABLE)
        if unknown:
            raise CRMError('A card does not take ' + ', '.join(sorted(unknown)))
        if 'Title' in fields:
            fields['Title'] = _clip(fields['Title'], TITLE_MAX)
            if not fields['Title']:
                raise CRMError('A card needs a title')
        if face is not None:
            fields['Face'] = face
        if transition:
            fields['State'] = transition
        if not fields:
            raise CRMError('Nothing to change')
        return fields

    def members(self, org, actor, card, subject, add=True):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(MEMBERS_PERSON_ONLY)
        subject = str(subject or '').strip()
        if not subject or len(subject) > 128 or _CONTROL.search(subject):
            raise CRMError('A member is a person id')
        m = s.card_members
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            self.store._visible(policy, row)
            leaving = not add and subject == caller.subject
            if not leaving:
                self.store._rights(policy, row, 'write')
            have = c.execute(select(m.c.status).where(m.c.org_id == org, m.c.record_id == row['id'],
                                                      m.c.subject == subject)).scalar_one_or_none()
            if add and have is None:
                c.execute(m.insert().values(org_id=org, record_id=row['id'], subject=subject, status='invited',
                                            added_by=caller.subject, added_at=self.clock().isoformat()))
            elif not add and have is not None:
                c.execute(delete(m).where(m.c.org_id == org, m.c.record_id == row['id'], m.c.subject == subject))
            return {'members': self._members(c, org, row['id'])}

    def membership(self, org, actor, card, accept):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(MEMBERS_PERSON_ONLY)
        m = s.card_members
        with self.engine.begin() as c:
            have = c.execute(select(m.c.status).where(m.c.org_id == org, m.c.record_id == str(card),
                                                      m.c.subject == caller.subject)).scalar_one_or_none()
            if have is None:
                raise NotFound('You are not on this card')
            where = (m.c.org_id == org, m.c.record_id == str(card), m.c.subject == caller.subject)
            if accept:
                c.execute(update(m).where(*where).values(status='member'))
                return {'status': 'member'}
            c.execute(delete(m).where(*where))
            return {'status': 'declined' if have == 'invited' else 'left'}

    # ── proposals ─────────────────────────────────────────────────────────────────────────────
    def _pending(self, c, org, ttl):
        now = self.clock()
        p = s.change_proposals
        rows = c.execute(select(p).where(p.c.org_id == org, p.c.status == 'pending')).mappings().all()
        out = []
        for row in rows:
            row = dict(row)
            if row['record_id'] is not None and not self.store._is_card(c, org, row['record_id']):
                continue
            at = (row['patch'] or {}).get('created_at')
            if at and now - _at(at) >= ttl:
                continue
            out.append(row)
        return out

    def _caps(self, c, org, caller):
        limits = self.limits()
        pending = self._pending(c, org, self._ttl(limits))
        mine = [p for p in pending if p['author_id'] == caller.subject]
        if len(mine) >= limits['card_proposals_pending_max']:
            raise Refused(pending_full(limits['card_proposals_pending_max']))
        if caller.delegated:
            day = [p for p in mine if (p['patch'] or {}).get('session') == caller.session]
            if len(day) >= limits['card_proposals_per_agent_per_day']:
                raise Refused(daily_full(limits['card_proposals_per_agent_per_day']))

    def _reason(self, reason):
        reason = _clip(reason, REASON_MAX)
        if not reason:
            raise CRMError('A proposal needs a one-line reason the person reads')
        return reason

    def propose(self, org, actor, title, workspace=None, about=None, type=DEFAULT_TYPE, parent=None, fields=None,
                face=None, reason=''):
        caller = as_caller(actor)
        if caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        reason = self._reason(reason)
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            if workspace:
                workspace = check_workspace(workspace)
                if workspace not in set(caller.workspaces):
                    raise Forbidden('Propose a card only into a workspace you work in')
            self._check_parent(c, org, policy, parent)
            object_type = type or DEFAULT_TYPE
            definition = self._definition(c, org, object_type)
            data = self.normalise_fields(object_type, self._new_fields(title, about, parent, fields, face), None)
            self.store._validate(definition, data)
            self._caps(c, org, caller)
            pid = str(uuid4())
            c.execute(s.change_proposals.insert().values(
                org_id=org, id=pid, record_id=None, base_revision=0, author_id=caller.subject,
                patch={'create': {'object_type': object_type, 'fields': data, 'workspace': workspace or None,
                                  'parent': parent or None},
                       'fields': data, 'narrative': None, 'author_kind': caller.kind, 'session': caller.session,
                       'created_at': self.clock().isoformat()},
                evidence=[], reason=reason, status='pending'))
            return {'status': 'proposed', 'proposal_id': pid}

    def update_propose(self, org, actor, card, expected_revision, fields=None, face=None, transition=None, reason=''):
        caller = as_caller(actor)
        if caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        reason = self._reason(reason)
        patch = self._patch(fields, face, transition)
        with self.engine.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            self.store._visible(policy, row)
            if row['revision'] != expected_revision:
                raise Conflict('Card changed; read it again')
            allowed = self.store._rights(policy, row, 'read')
            if '*' not in allowed and set(patch) - set(allowed):
                raise Forbidden('Cannot propose changes to fields you cannot read')
            patch = self.normalise_fields(row['object_type'], patch, row['fields'])
            self.store._validate(self._definition(c, org, row['object_type']), {**row['fields'], **patch})
            self._caps(c, org, caller)
            pid = str(uuid4())
            c.execute(s.change_proposals.insert().values(
                org_id=org, id=pid, record_id=row['id'], base_revision=expected_revision, author_id=caller.subject,
                patch={'fields': patch, 'narrative': None, 'author_kind': caller.kind, 'session': caller.session,
                       'created_at': self.clock().isoformat()},
                evidence=[], reason=reason, status='pending'))
            return {'status': 'proposed', 'proposal_id': pid}

    def _may_review_create(self, policy, caller, p):
        workspace = ((p['patch'] or {}).get('create') or {}).get('workspace')
        if workspace:
            return workspace in _writable(caller) or bool(policy.get('admin'))
        return p['author_id'] == caller.subject

    def review(self, c, org, caller, policy, p, accept):
        """``store.review`` for a card proposal, inside its transaction. The caller is a person."""
        at = (p['patch'] or {}).get('created_at')
        if at and self.clock() - _at(at) >= self._ttl():
            raise NotFound('Proposal expired')
        trail = [{'proposal': p['id'], 'author': p['author_id'], 'author_kind': (p['patch'] or {}).get('author_kind', 'person')}]
        if p['record_id'] is None:
            if not self._may_review_create(policy, caller, p):
                raise Forbidden('Review not permitted')
            if not accept:
                return {'status': 'rejected', 'proposal_id': p['id']}
            spec = p['patch']['create']
            if spec.get('workspace'):
                check_workspace(spec['workspace'])
            record_id = self._insert(c, org, p['author_id'], spec['object_type'], dict(spec['fields']), None,
                                     p['reason'], trail, spec.get('workspace'), [p['author_id'], caller.subject])
            return {'status': 'saved', 'record_id': record_id, 'revision': 1, 'proposal_id': p['id']}
        row = self.store._row(c, org, p['record_id'])
        self.store._rights(policy, row, 'write')
        if not accept:
            return {'status': 'rejected', 'proposal_id': p['id']}
        out = self.store._edit(c, org, caller.subject, policy, p['record_id'], p['base_revision'],
                               p['patch']['fields'], p['patch'].get('narrative'), p['reason'],
                               list(p['evidence'] or []) + trail)
        return {**out, 'proposal_id': p['id']}

    def _proposal_view(self, c, org, policy, p):
        create = (p['patch'] or {}).get('create')
        base = {'id': p['id'], 'reason': p['reason'], 'author_kind': (p['patch'] or {}).get('author_kind', 'person'),
                'author': p['author_id'], 'at': (p['patch'] or {}).get('created_at')}
        if create:
            f = create.get('fields') or {}
            return {**base, 'kind': 'create', 'card': None, 'title': f.get('Title') or 'A card',
                    'workspace': create.get('workspace'), 'fields': {k: v for k, v in f.items() if k != 'Face'}}
        row = self._card_row(c, org, p['record_id'])
        readable = self.store._rights(policy, row, 'read')
        title = self.store._visible(policy, row)['fields'].get('Title') or 'A card'
        fields = {k: v for k, v in (p['patch'].get('fields') or {}).items() if ('*' in readable or k in readable) and k != 'Face'}
        return {**base, 'kind': 'change', 'card': {'id': row['id'], 'title': title}, 'title': title,
                'workspace': None, 'fields': fields}

    def _reviewable(self, c, org, caller, policy, workspace=None):
        """Pending card proposals this person may answer, newest first. None to a room with people
        from outside the org: a proposal's reason and fields are the org's own."""
        if caller.internal or getattr(caller, 'cross_org', False):
            return []
        out = []
        for p in self._pending(c, org, self._ttl()):
            try:
                if p['record_id'] is None:
                    if not self._may_review_create(policy, caller, p):
                        continue
                    if workspace and ((p['patch'] or {}).get('create') or {}).get('workspace') != workspace:
                        continue
                else:
                    row = self._card_row(c, org, p['record_id'])
                    self.store._rights(policy, row, 'write')
                    if workspace and workspace not in self.store._bindings(c, org, row['id']):
                        continue
                out.append(self._proposal_view(c, org, policy, p))
            except (NotFound, SharedRefused):
                continue
        return sorted(out, key=lambda x: x['at'] or '', reverse=True)

    # ── lists ─────────────────────────────────────────────────────────────────────────────────
    def _views(self, c, org, policy, ids):
        out = []
        for record_id in ids:
            try:
                out.append(self.view(c, org, policy, self._card_row(c, org, record_id)))
            except (NotFound, SharedRefused):
                continue
        return out

    def list(self, org, actor, workspace=None, about=None, state=None, mine=False, limit=LIST_MAX):
        caller = as_caller(actor)
        limit = max(1, min(int(limit or LIST_MAX), LIST_MAX))
        with self.engine.connect() as c:
            policy = self.store._policy(c, org, caller)
            members = {k for k, v in policy.get('_members', {}).items() if v == 'member'}
            if workspace:
                workspace = check_workspace(workspace)
                ids = set(self.store._bindings_of(c, org, workspace)) \
                    if workspace in set(caller.workspaces) or (policy.get('admin') and not caller.delegated) else set()
            elif mine:
                ids = members
            else:
                ids = members | set(policy.get('_bound', {}))
            views = self._views(c, org, policy, sorted(ids))
            if about:
                views = [v for v in views if v['about'] and v['about']['ref'] == about]
            if state:
                views = [v for v in views if v['state'] == state]
            views.sort(key=lambda v: (v['movement'] or {}).get('at') or '', reverse=True)
            return {'cards': views[:limit], 'proposals': self._reviewable(c, org, caller, policy, workspace)}

    def desk(self, org, actor, placements, layout):
        """The desk's card part: cards I am a member of or placed (minus member cards I took off),
        cards I was invited to, and proposals I may answer."""
        caller = as_caller(actor)
        hidden = set((layout or {}).get('hidden_cards') or [])
        placed = [card_id(p['ref']) for p in placements if str(p.get('ref', '')).startswith('card:')]
        with self.engine.connect() as c:
            try:
                policy = self.store._policy(c, org, caller)
            except Forbidden:
                # no CRM membership in this organisation: the desk is still theirs, with no cards
                return {'cards': [], 'card_invites': [], 'card_proposals': []}
            members = policy.get('_members', {})
            joined = [k for k, v in members.items() if v == 'member' and 'card:' + k not in hidden]
            invited = sorted(k for k, v in members.items() if v == 'invited')
            ids = list(dict.fromkeys(placed + sorted(joined)))
            views = self._views(c, org, policy, ids)
            order = [card_id(r) for r in ((layout or {}).get('order') or {}).get('cards') or []]
            rank = {i: n for n, i in enumerate(order)}
            views.sort(key=lambda v: (v['movement'] or {}).get('at') or '', reverse=True)
            views.sort(key=lambda v: rank.get(v['id'], len(rank)))       # the person's order first (stable)
            return {'cards': views, 'card_invites': self._views(c, org, policy, invited),
                    'card_proposals': self._reviewable(c, org, caller, policy)}
