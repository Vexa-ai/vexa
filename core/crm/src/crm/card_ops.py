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

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import delete, select, update

from . import card_face
from . import card_templates as tpl
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
EDITABLE = ('Title', 'About', 'Next', 'Waiting', 'Summary', 'Due', 'Progress', 'Checklist', 'Parent', 'Face',
            # D11: the lifecycle coordination fields a person (or a proposal) may set. The handoff phase,
            # an answer, evidence and a receipt are written only by a gate (``decide``).
            'Kind', 'NextActor', 'Requester', 'NextCheckAt', 'Chain', 'Origin')
#: Written only by a gate decision (D12), never by an edit or a proposal.
GATE_ONLY = ('Handoff', 'HandoffSince', 'Answer', 'Evidence', 'Receipt')
#: How long a decision nonce lives (D13): long enough to read the card, short enough not to linger.
NONCE_TTL = timedelta(minutes=15)
#: An open card of a kind is checked again this long after its last move unless its due date is
#: sooner (the D11 invariant: no open card floats without a ``NextCheckAt``). `_global` policy
#: `card_check_hours` (1..720) tunes it.
CHECK_AFTER = timedelta(hours=24)
#: "Due soon" in the inbox counts: due within this many days.
DUE_SOON = timedelta(days=3)
_PERSON = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.:@\-]{0,127}$')
_ORIGIN = re.compile(r'^(session:[A-Za-z0-9._:\-]{1,128}|meeting:[A-Za-z0-9._:\-]{1,128}|'
                     r'message:[A-Za-z0-9._:\-]{1,128}/[A-Za-z0-9._:\-]{1,128})$')
_REF = re.compile(r'^[a-z]+:[^\x00-\x1f\x7f]{1,290}$')

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
SESSION_REQUIRED = _refusal(
    'session_required',
    "A decision on a card is made by the person at their own terminal, signed in — never with an API key "
    "or an agent's token. Tell them it is waiting on them and give the card's link.")
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
    def _attrs(self):
        if self.policy_text is None:
            return {}
        try:
            return front_matter(self.policy_text() or '') or {}
        except Exception:  # noqa: BLE001 — an unreadable policy is the defaults
            return {}

    def lifecycle(self, object_type, kind=None):
        """The stages a card moves through: its KIND's seeded template (D11) when it has one, else its
        type's ``lifecycles.<type>`` line."""
        template = self.template(kind)
        if template is not None:
            return {'stages': list(template['stages']), 'side': list(template['side'])}
        attrs = self._attrs()
        return parse_lifecycle(attrs.get('lifecycles.' + str(object_type).lower()) or attrs.get('lifecycles.card'))

    def template(self, kind):
        """A card kind's lifecycle template (``card_templates``), with the org's renamed stages if
        ``_global/POLICIES.md`` restates them (``card_templates.<kind>``); None for no kind."""
        if not kind or kind not in tpl.KINDS:
            return None
        line = self._attrs().get('card_templates.' + str(kind)) or self._attrs().get(
            'card_templates.' + tpl.KIND_TEMPLATE[kind])
        override = parse_lifecycle(line) if line else None
        return tpl.template_for(kind, override)

    def check_after(self):
        value = str(self._attrs().get('card_check_hours', '')).strip()
        return timedelta(hours=int(value)) if value.isdigit() and 1 <= int(value) <= 720 else CHECK_AFTER

    def seen_visible_to_sender(self):
        """D14/N-7: whether a card's sender sees that its next actor has seen it — a policy line,
        default off (``card_seen_visible_to_sender: true`` in ``_global/POLICIES.md``)."""
        return str(self._attrs().get('card_seen_visible_to_sender', '')).strip().lower() == 'true'

    def next_check(self, due=None, now=None):
        """The next check of an open card: its due date when that is sooner, else the check interval."""
        now = now or self.clock()
        at = now + self.check_after()
        if isinstance(due, str) and re.match(r'^\d{4}-\d{2}-\d{2}', due):
            try:
                d = datetime.fromisoformat(due[:10]).replace(tzinfo=timezone.utc)
                if now < d < at:
                    at = d
            except ValueError:
                pass
        return at.isoformat()

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
    #: Set while a gate decision writes (``decide``): the only writer of a kind card's stage and of
    #: the gate-only fields. Thread-local, because one Cards object serves concurrent requests.
    _gate = threading.local()

    def normalise_fields(self, object_type, fields, before, actor=None):
        """A card's fields as every write path stores them. ``actor`` is the person whose write is
        being APPLIED (``store._edit``: an edit, or the reviewer accepting a proposal); None while a
        proposal is only filed, which changes nothing until a person accepts it."""
        fields = dict(fields or {})
        prior = (before or {}) if isinstance(before, dict) else {}
        gate_write = bool(getattr(self._gate, 'on', False))
        if 'Face' in fields:
            fields['Face'] = card_face.sanitize(fields['Face'])
        if fields.get('About'):
            check_about(fields['About'])
        if not gate_write and set(fields) & set(GATE_ONLY):
            raise CRMError('A card\'s handoff, answer, evidence and receipt change only through its gates (decide)')
        kind = fields.get('Kind', prior.get('Kind'))
        if prior.get('Kind') and 'Kind' in fields and fields['Kind'] != prior.get('Kind'):
            raise CRMError("A card's kind does not change; make a new card")
        if kind is not None and kind not in tpl.KINDS:
            raise CRMError(f'Kind is one of {", ".join(tpl.KINDS)}')
        template = self.template(kind)
        life = self.lifecycle(object_type, kind)
        states = life['stages'] + life['side']
        if 'State' in fields:
            if fields['State'] not in states:
                raise CRMError(f'"{_clip(fields["State"], 60)}" is not a stage of this card type; '
                               f'its stages are {", ".join(states)}')
            if template is not None and before is not None and not gate_write \
                    and fields['State'] != prior.get('State'):
                raise CRMError('A card of this kind moves only through its gates (decide), by the person whose move it is')
        elif before is None:
            fields['State'] = life['stages'][0]
        for key in ('NextActor', 'Requester'):
            if fields.get(key) is not None:
                value = str(fields[key]).strip()
                if not _PERSON.fullmatch(value):
                    raise CRMError(f'{key} is a person id')
                fields[key] = value
        if fields.get('Origin') is not None and not _ORIGIN.fullmatch(str(fields['Origin'])):
            raise CRMError('Origin is session:<id>, meeting:<id> or message:<session>/<message id>')
        if 'Chain' in fields:
            chain = fields['Chain'] if isinstance(fields['Chain'], dict) else {}
            fields['Chain'] = {k: [str(x) for x in (chain.get(k) or []) if _ID.fullmatch(str(x))][:20]
                               for k in ('blocks', 'blocked_by')}
        if template is not None:
            merged = {**prior, **fields}
            if before is None:
                if not merged.get('NextActor'):
                    raise CRMError('A card of a kind names its next actor (NextActor: a person id)')
                fields.setdefault('Handoff', 'offered')
                fields.setdefault('HandoffSince', self.clock().isoformat())
            elif not gate_write:
                if actor is not None:
                    self._may_reassign(prior, fields, str(actor))
                if 'NextActor' in fields and fields['NextActor'] != prior.get('NextActor'):
                    # reassigned: a new offer to the new next actor, never custody by assignment (D12)
                    fields['Handoff'] = 'offered'
                    fields['HandoffSince'] = self.clock().isoformat()
            state = merged.get('State') or fields.get('State')
            if tpl.is_open(template, state) and not merged.get('NextCheckAt'):
                fields['NextCheckAt'] = self.next_check(merged.get('Due'))
        return fields

    @staticmethod
    def _may_reassign(prior, fields, actor):
        """WHOSE MOVE IT IS IS NOT A FIELD ANY WRITER SETS (D12). On a card of a kind the person waiting
        (``Requester``) is fixed when the card is made, and the next actor changes by an edit only
        when the person waiting or the current next actor hands it on — to someone else, never to
        themselves. Otherwise a writer of the card could make themselves the one who decides it."""
        if 'Requester' in fields and fields['Requester'] != prior.get('Requester'):
            raise Forbidden("A card's requester does not change; make a new card")
        if 'NextActor' not in fields or fields['NextActor'] == prior.get('NextActor'):
            return
        if actor not in {str(prior.get('Requester') or ''), str(prior.get('NextActor') or '')} - {''}:
            raise Forbidden('Only the person waiting on this card, or its next actor, hands it to someone else')
        if str(fields['NextActor'] or '') == actor:
            raise Forbidden('A card is handed to you by someone else; you do not make yourself its next actor')

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
        readable = store._view(policy, row).readable      # D4: the face locks what the field view omits
        if cross_org:
            readable = public_fields(policy, row['object_type'])
        f = visible['fields']
        raw = row['fields'] or {}
        kind = raw.get('Kind') if raw.get('Kind') in tpl.KINDS else None
        template = self.template(kind)
        life = self.lifecycle(row['object_type'], kind)
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
        out.update(self._coordination(c, org, row, raw, kind, template, caller, cross_org, state))
        if activity:
            out['activity'] = [self._movement(rev, readable) for rev in revs]
        return out

    # ── D11–D14: the coordination a viewer reads, whatever their field rights ───────────────────
    def _decisions(self, c, org, record_id):
        """The card's decision records, newest first (D13): each revision a gate decision wrote."""
        r = s.revisions
        rows = c.execute(select(r.c.revision, r.c.decision, r.c.actor_id, r.c.created_at, r.c.credential_class)
                         .where(r.c.org_id == org, r.c.record_id == record_id, r.c.decision != '')
                         .order_by(r.c.revision.desc()).limit(50)).all()
        return [{'revision': rv, 'action': d, 'by': by, 'at': at, 'credential_class': cc} for rv, d, by, at, cc in rows]

    def _seen_at(self, c, org, person, record_id):
        if not person:
            return None
        return c.execute(select(s.card_seen.c.seen_at).where(
            s.card_seen.c.org_id == org, s.card_seen.c.person == person,
            s.card_seen.c.record_id == record_id)).scalar_one_or_none()

    def _coordination(self, c, org, row, raw, kind, template, caller, cross_org, state):
        """The lifecycle coordination fields (D8's enumerated class: kind, state, next actor, next
        check, handoff) and the viewer's own position on the card. They are what a card fact carries
        and what routing needs, so they are read whoever may read the card — never its content. A room
        with people from outside the org reads none of them."""
        blank = {'kind': kind, 'next_actor': None, 'requester': None, 'next_check_at': None,
                 'chain': {'blocks': [], 'blocked_by': []}, 'origin': None, 'open': True,
                 'handoff': {'phase': None, 'since': None}, 'gates': [], 'decisions': [], 'last_decision': None,
                 'viewer': {'role': 'watcher', 'seen': False, 'actions': []}}
        if cross_org:
            return blank
        me = '' if (caller.delegated or caller.internal) else str(caller.subject or '')
        next_actor = raw.get('NextActor') or None
        requester = raw.get('Requester') or (row['owner_id'] if template is not None else None)
        decisions = self._decisions(c, org, row['id'])
        chain = raw.get('Chain') if isinstance(raw.get('Chain'), dict) else {}
        out = {
            'kind': kind, 'next_actor': next_actor, 'requester': requester,
            'next_check_at': raw.get('NextCheckAt') or None,
            'chain': {'blocks': list(chain.get('blocks') or []), 'blocked_by': list(chain.get('blocked_by') or [])},
            'origin': raw.get('Origin') or None,
            'handoff': {'phase': raw.get('Handoff') or None, 'since': raw.get('HandoffSince') or None},
            'decisions': decisions,
            'last_decision': ({'action': decisions[0]['action'], 'by': decisions[0]['by'], 'at': decisions[0]['at']}
                              if decisions else None),
        }
        if template is None:
            out.update({'open': True, 'gates': [], 'viewer': {
                'role': 'member' if me and me in {m['subject'] for m in self._members(c, org, row['id'])} else 'watcher',
                'seen': bool(self._seen_at(c, org, me, row['id'])), 'actions': []}})
            return out
        out['open'] = tpl.is_open(template, state)
        out['gates'] = tpl.gate_strip(template, state, decisions)
        if me and me == next_actor:
            role = 'next_actor'
        elif me and me == requester:
            role = 'requester'
        elif me and me in {m['subject'] for m in self._members(c, org, row['id'])}:
            role = 'member'
        else:
            role = 'watcher'
        seen_at = self._seen_at(c, org, me, row['id'])
        since = raw.get('HandoffSince') or ''
        out['viewer'] = {'role': role, 'seen': bool(seen_at) and (not since or str(seen_at) >= since),
                         'actions': tpl.actions_for(template, state, raw.get('Handoff'), me, next_actor, requester)}
        # whether the next actor has seen it: theirs to know; the sender's only by policy (N-7)
        if role == 'next_actor' or (role == 'requester' and self.seen_visible_to_sender()):
            na_seen = self._seen_at(c, org, next_actor, row['id'])
            out['handoff']['seen'] = bool(na_seen) and (not since or str(na_seen) >= since)
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
        """One card with its activity. Reading never marks it seen (D14, P21): only the terminal's
        render in view does, through ``seen``."""
        caller = as_caller(actor)
        with self.engine.connect() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            return self.view(c, org, policy, row, activity=True)

    @staticmethod
    def _require_session(caller):
        """D13: decisions and the seen relation take the terminal's session credential — never an API
        key (a browser-scoped one included), never a delegation token, never the internal tier."""
        if caller.delegated or caller.internal or not caller.subject \
                or getattr(caller, 'credential_class', '') != 'session':
            raise Refused(SESSION_REQUIRED)

    def seen(self, org, actor, card):
        """D14: the person's terminal rendered this card in view. The ONE writer of ``crm_card_seen``;
        written on render, never on delivery or a fetch (P21)."""
        caller = as_caller(actor)
        self._require_session(caller)
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            self.store._visible(policy, row)
            t, now = s.card_seen, self.clock().isoformat()
            where = (t.c.org_id == org, t.c.person == caller.subject, t.c.record_id == row['id'])
            if c.execute(select(t.c.seen_at).where(*where)).first() is None:
                c.execute(t.insert().values(org_id=org, person=caller.subject, record_id=row['id'], seen_at=now))
            else:
                c.execute(update(t).where(*where).values(seen_at=now))
            return {'card': row['id'], 'seen_at': now}

    # ── D13: the decision record ──────────────────────────────────────────────────────────────
    @staticmethod
    def canonical(view):
        """The card as its signer saw it, canonically: what the decision binds to. Everything on the
        face this viewer reads, plus the coordination that says what they are deciding."""
        keep = ('id', 'revision', 'type', 'kind', 'title', 'state', 'next_actor', 'requester', 'due', 'summary',
                'next', 'waiting', 'about', 'face', 'chain')
        return {k: view.get(k) for k in keep}

    @classmethod
    def content_hash(cls, view):
        blob = json.dumps(cls.canonical(view), sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(blob.encode('utf-8')).hexdigest()

    @staticmethod
    def _locked(view):
        return [b.get('label') or 'a field' for b in view.get('face') or [] if b.get('type') == 'locked']

    def nonce(self, org, actor, card):
        """A server nonce bound to this person, this card at this revision, and the content hash of
        the face they are shown now. Spent by exactly one decision."""
        caller = as_caller(actor)
        self._require_session(caller)
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            view = self.view(c, org, policy, row)
            now = self.clock()
            value = secrets.token_urlsafe(24)
            digest = self.content_hash(view)
            c.execute(s.card_nonces.insert().values(
                org_id=org, nonce=value, record_id=row['id'], person=caller.subject, revision=row['revision'],
                content_hash=digest, created_at=now.isoformat(), expires_at=(now + NONCE_TTL).isoformat(), used_at=None))
            return {'card': row['id'], 'nonce': value, 'content_hash': digest, 'revision': row['revision'],
                    'expires_at': (now + NONCE_TTL).isoformat(), 'actions': view['viewer']['actions']}

    def decide(self, org, actor, card, action, nonce, content_hash, evidence=None, receipt=None, answer=None,
               reason=None):
        """A gate decision (D12) as a decision record (D13): one append-only revision carrying the
        action, ``credential_class=session``, the nonce and the content hash.

        Refused unless: the caller holds the session credential; the action is one this viewer may
        take now (they are the next actor, the gate accepts the card's stage); no field on their face
        is locked (deny test 17: you sign only what you can read); the nonce was minted for them, for
        this card at this revision, is unspent and unexpired; and the content hash equals both the
        nonce's and the face they read now. A gate that needs evidence, a receipt or an answer gets
        it or is refused. The authority is the template, so field write rights do not apply."""
        caller = as_caller(actor)
        self._require_session(caller)
        action = str(action or '')
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            raw = row['fields'] or {}
            template = self.template(raw.get('Kind'))
            if template is None:
                raise CRMError('This card has no lifecycle template; it moves with update, not decide')
            view = self.view(c, org, policy, row)
            if action not in view['viewer']['actions']:
                raise Forbidden('This decision is not yours to make on this card now')
            locked = self._locked(view)
            if locked:
                raise Forbidden('You cannot sign a card with a field hidden from you: ' + ', '.join(locked[:3]))
            n = s.card_nonces
            got = c.execute(select(n).where(n.c.org_id == org, n.c.nonce == str(nonce or '')).with_for_update()
                            ).mappings().first()
            now = self.clock()
            if got is None or got['person'] != caller.subject or got['record_id'] != row['id'] \
                    or got['used_at'] is not None or _at(got['expires_at']) <= now:
                raise Conflict('This decision is not bound to a live pending item; read the card again')
            if got['revision'] != row['revision']:
                raise Conflict('The card changed since you read it; read it again')
            digest = self.content_hash(view)
            if not (secrets.compare_digest(str(content_hash or ''), digest)
                    and secrets.compare_digest(got['content_hash'], digest)):
                raise Conflict('The card you saw is not the card now; read it again')
            spent = c.execute(update(n).where(n.c.org_id == org, n.c.nonce == got['nonce'], n.c.used_at.is_(None))
                              .values(used_at=now.isoformat())).rowcount
            if spent != 1:
                raise Conflict('This decision was already made')
            gate = template['gates'][action]
            patch = {}
            if gate.get('needs') == 'evidence':
                refs = [str(e).strip() for e in (evidence if isinstance(evidence, list) else [evidence]) if str(e or '').strip()]
                if not refs or not all(_REF.fullmatch(r) for r in refs):
                    raise CRMError('Delivering cites evidence: one or more references (page:…, meeting:…, crm:…)')
                patch['Evidence'] = refs[:10]
            if gate.get('needs') == 'receipt':
                if not receipt or not _REF.fullmatch(str(receipt)):
                    raise CRMError('Executed needs the effect receipt reference')
                patch['Receipt'] = str(receipt)
            if gate.get('needs') == 'answer':
                text = _clip(answer, 500)
                if not text:
                    raise CRMError('An answer is needed')
                patch['Answer'] = text
            if gate.get('to'):
                patch['State'] = gate['to']
            stamp = now.isoformat()
            then = gate.get('then')
            if then == tpl.REQUESTER:
                patch['NextActor'] = view['requester']
                patch['Handoff'] = 'offered'
                patch['HandoffSince'] = stamp
            elif then == tpl.NOBODY:
                patch['NextActor'] = None
                patch['Handoff'] = None
                patch['HandoffSince'] = stamp
                patch['NextCheckAt'] = None
            else:                       # SAME: the next actor keeps it, now acknowledged
                patch['Handoff'] = 'acknowledged'
                patch['HandoffSince'] = stamp
            if then != tpl.NOBODY:
                patch['NextCheckAt'] = self.next_check(raw.get('Due'), now)
            words = tpl.DONE_WORDS[action]
            record = {'decision': action, 'credential_class': 'session', 'nonce': got['nonce'], 'content_hash': digest}
            evidence_trail = [{'decision': action, 'reason': _clip(reason, REASON_MAX)}] if reason else []
            self._gate.on = True
            try:
                self._apply(c, org, caller.subject, row, patch, words, evidence_trail, record)
            finally:
                self._gate.on = False
            if patch.get('NextActor'):
                self._ensure_member(c, org, row['id'], patch['NextActor'], caller.subject)
            policy = self.store._policy(c, org, caller)
            fresh = self.view(c, org, policy, self._card_row(c, org, row['id']), activity=True)
            return {'status': 'saved', 'card': fresh,
                    'decision': {'action': action, 'by': caller.subject, 'at': stamp, 'revision': fresh['revision'],
                                 'credential_class': 'session', 'content_hash': digest}}

    def _apply(self, c, org, actor, row, patch, reason, evidence, decision):
        """Write a gate's patch as one revision. The template authorised it, so field write rights
        are not consulted; the schema and the lifecycle still are."""
        store = self.store
        fields = self.normalise_fields(row['object_type'], patch, row['fields'])
        after = {**row, 'fields': {**row['fields'], **fields}, 'revision': row['revision'] + 1}
        store._validate(store._definition(c, org, row['object_type']), after['fields'])
        changed = c.execute(update(s.records).where(s.records.c.org_id == org, s.records.c.id == row['id'],
                                                    s.records.c.revision == row['revision']).values(**after)).rowcount
        if changed != 1:
            raise Conflict('The card changed since you read it; read it again')
        store._sync_relationships(c, after)
        store._revision(c, org, actor, row, after, reason, evidence, decision=decision)

    def _ensure_member(self, c, org, record_id, subject, by):
        m = s.card_members
        have = c.execute(select(m.c.status).where(m.c.org_id == org, m.c.record_id == record_id,
                                                  m.c.subject == subject)).scalar_one_or_none()
        if have is None:
            c.execute(m.insert().values(org_id=org, record_id=record_id, subject=subject, status='member',
                                        added_by=by, added_at=self.clock().isoformat()))

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
        now = self.clock().isoformat()
        # D11: a card of a kind is shared with its next actor and the person waiting on it, so both
        # read it; membership comes BEFORE the revision so the fact's audience names them (D8).
        parties = [fields.get('NextActor'), fields.get('Requester')] if fields.get('Kind') else []
        for subject in dict.fromkeys([*members, *[p for p in parties if p]]):
            c.execute(s.card_members.insert().values(org_id=org, record_id=record_id, subject=subject,
                                                     status='member', added_by=owner, added_at=now))
        if workspace:
            c.execute(s.workspace_bindings.insert().values(org_id=org, workspace=workspace, record_id=record_id,
                                                           created_by=owner, created_at=now))
        store.audit_names(record_id)
        store._revision(c, org, owner, None, row, reason, evidence)
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
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            if workspace:
                workspace = check_workspace(workspace)
                if workspace not in _writable(caller) and not policy.get('admin'):
                    raise Forbidden('A card goes on the board of a workspace you can write')
            self._check_parent(c, org, policy, parent)
            data = self._new_fields(title, about, parent, fields, face)
            if data.get('Kind'):
                data.setdefault('Requester', caller.subject)
            record_id = self._insert(c, org, caller.subject, type or DEFAULT_TYPE, data, narrative,
                                     'Made the card', [], workspace, [caller.subject])
            policy = self.store._policy(c, org, caller)
            return {'status': 'saved', 'card': self.view(c, org, policy, self._card_row(c, org, record_id))}

    def update(self, org, actor, card, expected_revision, fields=None, face=None, transition=None):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        patch = self._patch(fields, face, transition)
        with self.store.begin() as c:
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
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            row = self._card_row(c, org, card)
            self.store._visible(policy, row)
            leaving = not add and subject == caller.subject
            if not leaving:
                self.store._rights(policy, row, 'write')
            # D35: a card member is a Person — adding or removing one reaches every account linked
            # to them (persons.py), until the person merges them. Leaving is the one account's own.
            who = [subject] if leaving else self.store.persons.linked_accounts(c, org, subject)
            for one in who:
                have = c.execute(select(m.c.status).where(m.c.org_id == org, m.c.record_id == row['id'],
                                                          m.c.subject == one)).scalar_one_or_none()
                if add and have is None and (one == subject or one != caller.subject):
                    c.execute(m.insert().values(org_id=org, record_id=row['id'], subject=one, status='invited',
                                                added_by=caller.subject, added_at=self.clock().isoformat()))
                elif not add and have is not None:
                    c.execute(delete(m).where(m.c.org_id == org, m.c.record_id == row['id'], m.c.subject == one))
            return {'members': self._members(c, org, row['id'])}

    def membership(self, org, actor, card, accept):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(MEMBERS_PERSON_ONLY)
        m = s.card_members
        with self.store.begin() as c:
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
        if (fields or {}).get('Kind'):
            return self._offer(org, caller, title, workspace, about, type, parent, fields, face, reason)
        with self.store.begin() as c:
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

    def _offered_today(self, c, org, caller):
        r = s.revisions
        since = (self.clock() - timedelta(days=1)).isoformat()
        n = 0
        for (evidence,) in c.execute(select(r.c.evidence).where(r.c.org_id == org, r.c.revision == 1,
                                                                 r.c.actor_id == caller.subject, r.c.created_at > since)):
            if any(isinstance(e, dict) and e.get('proposal') == 'offer' and e.get('session') == caller.session
                   for e in evidence or []):
                n += 1
        return n

    def _offer(self, org, caller, title, workspace, about, type, parent, fields, face, reason):
        """D11/R10: an agent's card OF A KIND is made in its first stage and OFFERED to its next actor
        — the card itself is the proposal, and only that person's acknowledgement (a decision record,
        D13) moves it. The agent never moves it after. Capped per session per day like proposals."""
        with self.store.begin() as c:
            policy = self.store._policy(c, org, caller)
            if workspace:
                workspace = check_workspace(workspace)
                if workspace not in set(caller.workspaces):
                    raise Forbidden('Propose a card only into a workspace you work in')
            self._check_parent(c, org, policy, parent)
            limits = self.limits()
            if caller.delegated and self._offered_today(c, org, caller) >= limits['card_proposals_per_agent_per_day']:
                raise Refused(daily_full(limits['card_proposals_per_agent_per_day']))
            data = self._new_fields(title, about, parent, fields, face)
            on_behalf = str((caller.extra or {}).get('on_behalf_of') or '').strip()
            data.setdefault('Requester', on_behalf or caller.subject)
            trail = [{'proposal': 'offer', 'author': caller.subject, 'author_kind': caller.kind, 'session': caller.session}]
            record_id = self._insert(c, org, caller.subject, type or DEFAULT_TYPE, data, None, reason, trail,
                                     workspace, [])
            policy = self.store._policy(c, org, caller)
            try:
                return {'status': 'offered', 'card': self.view(c, org, policy, self._card_row(c, org, record_id))}
            except (NotFound, SharedRefused):
                return {'status': 'offered', 'card': None, 'record_id': record_id}

    def offer_many(self, org, actor, workspace, origin, specs, reason):
        """Offer several cards of a kind from one origin (a meeting's follow-ups), idempotently: a card
        with the same origin, title and next actor already offered by this caller is returned, not made
        again — a retried post-meeting run never doubles a card. Each goes through ``_offer``, the
        path ``card_propose`` takes, so the caps and the rules are the same."""
        caller = as_caller(actor)
        out = []
        with self.engine.connect() as c:
            r = s.records
            have = {}
            for rid, f in c.execute(select(r.c.id, r.c.fields).where(r.c.org_id == org, r.c.owner_id == caller.subject,
                                                                     r.c.deleted == False)):  # noqa: E712
                f = f or {}
                if f.get('Origin') == origin and f.get('Kind'):
                    have[(f.get('Title'), f.get('NextActor'))] = rid
        for spec in specs:
            fields = {k: v for k, v in (spec.get('fields') or {}).items()}
            fields['Origin'] = origin
            title = _clip(spec.get('title'), TITLE_MAX)
            key = (title, fields.get('NextActor'))
            if key in have:
                out.append({'id': have[key], 'title': title, 'kind': fields.get('Kind'), 'existing': True})
                continue
            res = self._offer(org, caller, title, workspace, spec.get('about'), DEFAULT_TYPE, None, fields, None,
                              self._reason(spec.get('reason') or reason))
            rid = (res.get('card') or {}).get('id') or res.get('record_id')
            have[key] = rid
            out.append({'id': rid, 'title': title, 'kind': fields.get('Kind'), 'existing': False})
        return {'cards': out}

    def update_propose(self, org, actor, card, expected_revision, fields=None, face=None, transition=None, reason=''):
        caller = as_caller(actor)
        if caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        reason = self._reason(reason)
        patch = self._patch(fields, face, transition)
        with self.store.begin() as c:
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
        # deny test 17 (D13): accepting is signing — refused to a reviewer for whom a field on the
        # card's face is locked; you sign only what you can read.
        locked = self._locked(self.view(c, org, policy, self._card_row(c, org, row['id'])))
        if locked:
            raise Forbidden('You cannot sign a card with a field hidden from you: ' + ', '.join(locked[:3]))
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
            me = '' if caller.delegated else caller.subject
            return {'cards': views[:limit], 'proposals': self._reviewable(c, org, caller, policy, workspace),
                    'counts': self.counts(views, me)}

    # ── the inbox: one predicate for the Now strip, the Inbox and a room (S5 check) ────────────
    @staticmethod
    def waits_on(view, me):
        """An open card whose next actor is this person. THE predicate every count uses."""
        return bool(me) and view.get('open', True) and view.get('next_actor') == me

    def counts(self, views, me):
        now = self.clock()
        soon = (now + DUE_SOON).date().isoformat()
        def due_soon(v):
            return bool(v.get('due')) and v.get('open', True) and str(v['due'])[:10] <= soon \
                and (v.get('next_actor') == me or v.get('requester') == me)
        return {'waiting_on_me': sum(1 for v in views if self.waits_on(v, me)),
                'due_soon': sum(1 for v in views if me and due_soon(v)),
                'i_wait_on': sum(1 for v in views if me and v.get('open', True) and v.get('kind')
                                 and v.get('requester') == me and v.get('next_actor') not in (None, me))}

    def inbox(self, org, actor):
        """The person's Inbox: open cards waiting on them (next actor = them), soonest check first,
        with the counts the Now strip, the Inbox and a room badge all read. A person's own view; an
        agent reads cards through card_list."""
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(MEMBERS_PERSON_ONLY)
        with self.engine.connect() as c:
            try:
                policy = self.store._policy(c, org, caller)
            except Forbidden:
                return {'items': [], 'counts': {'waiting_on_me': 0, 'due_soon': 0, 'i_wait_on': 0}}
            ids = {k for k, v in policy.get('_members', {}).items() if v in ('member', 'invited')}
            views = self._views(c, org, policy, sorted(ids))
            items = [v for v in views if self.waits_on(v, caller.subject)]
            items.sort(key=lambda v: (v.get('next_check_at') or '9999', v.get('due') or '9999', v['title'].casefold()))
            return {'items': items[:LIST_MAX], 'counts': self.counts(views, caller.subject)}

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
