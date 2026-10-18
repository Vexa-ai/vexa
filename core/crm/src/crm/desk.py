"""The desk (desktop phase D1): one person's placements, arrangement versions and desk proposals.

A desk is the person's own state, not shared organisation state, so it never touches a record:
- **placements** (`crm_desk`) — which agents and files are on this person's desk (cards from D2);
- **arrangement versions** (`crm_desk_layouts`, append-only) — every applied change is a new version
  carrying the layout AND the placement set after it, so Undo restores both. Version 0 is the default
  (Workspaces, Agents, Files, Cards; stacked, shown, expanded) and has no row;
- **desk proposals** (`crm_desk_proposals`) — what an agent may only suggest; the person applies or
  dismisses it with their own credential; pending for at most seven days, or
  fewer where the company's `_global/POLICIES.md` narrows it (`policies.py`, as every desk limit).

**Who arranges directly** is decided here from the caller identity resolved, never from what an agent
says: the person themselves, or a worker in a turn the person typed in their own one-person chat
(`regime == "human"`, not `shared`). Anything else — a routine, an event agent, a shared chat — is
refused with ``propose_instead`` and may call the propose route. A person can only ever arrange their
own desk: the person is the caller's subject, never an argument.

Operations are declarative data (no code, no templates), validated one by one; a bad one refuses the
whole list with its index, so nothing half-applies.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from . import features
from . import schema as s
from .caller import Caller, as_caller
from .policies import DEFAULTS as LIMIT_DEFAULTS
from .store import CRMError, Conflict, NotFound, Refused

DEFAULT_SECTIONS = ('workspaces', 'agents', 'files', 'cards')
TITLES = {'workspaces': 'Workspaces', 'agents': 'Agents', 'files': 'Files', 'cards': 'Cards'}
SECTION_OF_KIND = {'workspace': 'workspaces', 'agent': 'agents', 'chat': 'agents', 'file': 'files', 'card': 'cards'}

#: Limits (ADR desktop-cards §2.7): the defaults. A company narrows them in `_global/POLICIES.md`
#: (`policies.py`), read per call through the ``limits`` the Desk is given.
PLACEMENTS_MAX = LIMIT_DEFAULTS['desk_placements_max']                    # 50
PENDING_MAX = LIMIT_DEFAULTS['card_proposals_pending_max']                # 5, per person
PER_SESSION_PER_DAY = LIMIT_DEFAULTS['card_proposals_per_agent_per_day']  # 20, per agent session
PROPOSAL_TTL = timedelta(days=LIMIT_DEFAULTS['card_proposal_ttl_days'])   # 7 days
OPS_MAX = 50
WHERE_MAX = 300
NAME_MAX = 80
IDS_MAX = 200

GROUP_BY = ('workspace', 'type', 'state', 'about')
SIZES = ('compact', 'normal', 'tall')

_SLUG = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$')
_ID = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,127}$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f\\]')


def _refusal(reason, instruction, **extra):
    return {'status': 'refused', 'reason': reason, 'instruction': instruction, **extra}


PROPOSE_INSTEAD = _refusal(
    'propose_instead',
    'Only the person, or you in a turn they typed in their own one-person chat, may change their '
    'desktop directly. Call desktop_arrange_propose with the same ops and a one-line reason; the person '
    'applies it from their desktop. Do not retry it another way.')
CONFIRM = _refusal(
    'person_confirmation_required',
    'The person applies or dismisses a desktop suggestion themselves, on their desktop. Tell them it is '
    'waiting; do not retry it another way.')
NOT_READABLE = _refusal(
    'not_readable',
    'That file is in a workspace this person cannot read here, so it cannot go on their desktop. Say so; '
    'do not retry it another way.')
CARD_NOT_READABLE = _refusal(
    'not_readable',
    'This person cannot read that card, so it cannot go on their desktop. Say so; do not retry it another way.')
REF_KIND = _refusal(
    'ref_kind_not_enabled',
    'Cards are not on the desktop yet. Place an agent or a file instead.')
def placements_full(n=PLACEMENTS_MAX):
    return _refusal('desk_placements_max',
                    f'The desktop holds at most {n} placed items. Ask the person what to remove first.')


def pending_full(n=PENDING_MAX):
    return _refusal('proposals_pending_max',
                    f'This person already has {n} desktop suggestions waiting. Do not add another; mention '
                    'the waiting ones instead.')


def daily_full(n=PER_SESSION_PER_DAY):
    return _refusal('proposals_daily_max',
                    f'This session has made its {n} desktop suggestions for today. Stop suggesting.')


PLACEMENTS_FULL, PENDING_FULL, DAILY_FULL = placements_full(), pending_full(), daily_full()


def desk_changed(version):
    return _refusal('desk_changed', 'The desktop changed since you read it. Call desktop_read and apply '
                                    'your change to the version it returns.', version=version)


class DeskRefused(Refused):
    """A typed refusal with its own status (409 for a conflict, 422 for a ref kind not enabled)."""
    def __init__(self, body, status=403):
        super().__init__(body)
        self.status = status


class Invalid(CRMError):
    status = 422


# ── refs ──────────────────────────────────────────────────────────────────────────────────────
def parse_ref(ref, where):
    """``(kind, ref)`` for a well-formed ref; 422 naming ``where`` otherwise. ``card:<id>`` only while
    cards are served (``desktop_cards``, desktop D2); ``ref_kind_not_enabled`` otherwise."""
    if not isinstance(ref, str) or ':' not in ref or len(ref) > 600:
        raise Invalid(f'{where}: not a ref (agent:<id>, chat:<session id>, file:<workspace>/<path>, workspace:<slug>, card:<id>)')
    kind, _, rest = ref.partition(':')
    if kind == 'card':
        if not features.cards_served():
            raise DeskRefused(REF_KIND, 422)
        if _ID.fullmatch(rest):
            return kind, ref
    if kind in ('agent', 'chat') and _ID.fullmatch(rest):
        return kind, ref
    if kind == 'workspace' and _SLUG.fullmatch(rest) and '..' not in rest:
        return kind, ref
    if kind == 'file':
        slug, _, path = rest.partition('/')
        parts = path.split('/')
        if (_SLUG.fullmatch(slug) and '..' not in slug and path and len(path) <= 500
                and not _CONTROL.search(path) and all(p not in ('', '.', '..') for p in parts)):
            return kind, ref
    raise Invalid(f'{where}: not a ref (agent:<id>, chat:<session id>, file:<workspace>/<path>, workspace:<slug>, card:<id>)')


def readable_file(caller: Caller, ref):
    """A file ref is placeable only from a workspace the caller reads now, or their own desk."""
    slug = ref.split(':', 1)[1].split('/', 1)[0]
    return slug in caller.workspaces or (caller.subject and slug == caller.subject)


def label(ref):
    kind, _, rest = ref.partition(':')
    if kind == 'file':
        base = rest.rsplit('/', 1)[-1]
        base = base.rsplit('.', 1)[0] if '.' in base[1:] else base
        words = ' '.join(re.split(r'[-_\s]+', base)).strip()
        return (words[:1].upper() + words[1:]) if words else 'a file'
    if kind == 'agent':
        return 'an agent'
    if kind == 'chat':
        return 'a chat'
    if kind == 'workspace':
        return 'a workspace'
    if kind == 'card':
        return 'a card'
    return 'an item'


# ── the layout document ─────────────────────────────────────────────────────────────────────────
def default_section(sid):
    return {'id': sid, 'hidden': False, 'collapsed': False, 'column': 1, 'span': 1, 'size': 'normal'}


def normalise(raw):
    """A stored layout with every default filled in. A section type that appears later is appended at
    the end, never above what the person arranged."""
    raw = copy.deepcopy(raw or {})
    sections, seen = [], set()
    for sec in raw.get('sections') or []:
        sid = sec.get('id') if isinstance(sec, dict) else None
        if sid in DEFAULT_SECTIONS and sid not in seen:
            seen.add(sid)
            sections.append({**default_section(sid), **{k: v for k, v in sec.items() if k in default_section(sid)}})
    sections += [default_section(sid) for sid in DEFAULT_SECTIONS if sid not in seen]
    return {'sections': sections, 'order': dict(raw.get('order') or {}), 'groups': dict(raw.get('groups') or {}),
            'filters': dict(raw.get('filters') or {}),
            # member cards the person took off their desk (D2): their own view, the card is untouched
            'hidden_cards': [r for r in (raw.get('hidden_cards') or []) if isinstance(r, str)][:500],
            'columns': {'phone': 1, 'desktop': 1, **dict(raw.get('columns') or {})}}


# ── op validation (shape only; effects are applied below) ──────────────────────────────────────
_KEYS = {
    'order': ({'section', 'ids'}, set()),
    'move': ({'id'}, {'before', 'after'}),
    'group': ({'section'}, {'by', 'named'}),
    'show': ({'section'}, set()), 'hide': ({'section'}, set()), 'collapse': ({'section'}, set()),
    'expand': ({'section'}, set()), 'section_top': ({'section'}, set()),
    'section_order': ({'sections'}, set()),
    'section_place': ({'section', 'column'}, {'span', 'side_by_side_with'}),
    'section_size': ({'section', 'size'}, set()),
    'place': ({'ref'}, set()), 'remove': ({'ref'}, set()),
    'filter': ({'section', 'where'}, {'until'}),
    'save': ({'name'}, set()), 'restore': ({'name'}, set()),
}


def _section(op, where, key='section'):
    sid = op.get(key)
    if sid not in DEFAULT_SECTIONS:
        raise Invalid(f'{where}: {key} must be one of {", ".join(DEFAULT_SECTIONS)}')
    return sid


def _int(op, key, where, lo, hi):
    v = op.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise Invalid(f'{where}: {key} must be a whole number {lo}..{hi}')
    return v


def _name(op, where):
    name = op.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > NAME_MAX:
        raise Invalid(f'{where}: name must be 1..{NAME_MAX} characters')
    return name.strip()


def _ids(value, where, section=None):
    if not isinstance(value, list) or len(value) > IDS_MAX:
        raise Invalid(f'{where}: ids must be a list of at most {IDS_MAX} refs')
    out = []
    for n, ref in enumerate(value):
        kind, ref = parse_ref(ref, f'{where}.ids[{n}]')
        if section and SECTION_OF_KIND[kind] != section:
            raise Invalid(f'{where}.ids[{n}]: a {kind} ref is not in {TITLES[section]}')
        if ref not in out:
            out.append(ref)
    return out


def check_op(op, where):
    """The op's shape: a known op, its own keys only, valid values. Returns it unchanged."""
    if not isinstance(op, dict) or op.get('op') not in _KEYS:
        raise Invalid(f'{where}: unknown op {op.get("op") if isinstance(op, dict) else op!r}; one of {", ".join(_KEYS)}')
    required, optional = _KEYS[op['op']]
    keys = set(op) - {'op'}
    if not required <= keys or keys - required - optional:
        raise Invalid(f'{where}: {op["op"]} takes {", ".join(sorted(required))}'
                      + (f' and optionally {", ".join(sorted(optional))}' if optional else ''))
    kind = op['op']
    if kind == 'order':
        _ids(op['ids'], where, _section(op, where))
    elif kind == 'move':
        if ('before' in op) == ('after' in op):
            raise Invalid(f'{where}: move takes exactly one of before, after')
        other = op.get('before', op.get('after'))
        if op['id'] in DEFAULT_SECTIONS or other in DEFAULT_SECTIONS:
            _section(op, where, 'id')
            _section(op, where, 'before' if 'before' in op else 'after')
        else:
            a, _ = parse_ref(op['id'], f'{where}.id')
            b, _ = parse_ref(other, f'{where}.{"before" if "before" in op else "after"}')
            if SECTION_OF_KIND[a] != SECTION_OF_KIND[b]:
                raise Invalid(f'{where}: move within one section only')
    elif kind == 'group':
        sid = _section(op, where)
        if ('by' in op) == ('named' in op):
            raise Invalid(f'{where}: group takes exactly one of by, named')
        if 'by' in op and op['by'] not in GROUP_BY:
            raise Invalid(f'{where}: by must be one of {", ".join(GROUP_BY)}')
        if 'named' in op:
            named = op['named']
            if not isinstance(named, list) or not 1 <= len(named) <= 20:
                raise Invalid(f'{where}: named must be a list of 1..20 groups')
            for n, g in enumerate(named):
                if not isinstance(g, dict) or set(g) != {'title', 'ids'} or not isinstance(g['title'], str) \
                        or not g['title'].strip() or len(g['title']) > NAME_MAX:
                    raise Invalid(f'{where}.named[{n}]: a group is {{title, ids}}')
                _ids(g['ids'], f'{where}.named[{n}]', sid)
    elif kind in ('show', 'hide', 'collapse', 'expand', 'section_top', 'section_size', 'filter'):
        _section(op, where)
        if kind == 'section_size' and op['size'] not in SIZES:
            raise Invalid(f'{where}: size must be one of {", ".join(SIZES)}')
        if kind == 'filter':
            w = op['where']
            if w is not None and (not isinstance(w, str) or not w.strip() or len(w) > WHERE_MAX):
                raise Invalid(f'{where}: where is a predicate of at most {WHERE_MAX} characters, or null')
            if 'until' in op:
                try:
                    datetime.fromisoformat(str(op['until']).replace('Z', '+00:00'))
                except ValueError:
                    raise Invalid(f'{where}: until must be an ISO time') from None
    elif kind == 'section_order':
        secs = op['sections']
        if not isinstance(secs, list) or not secs or len(set(map(str, secs))) != len(secs) \
                or any(x not in DEFAULT_SECTIONS for x in secs):
            raise Invalid(f'{where}: sections must list distinct sections of {", ".join(DEFAULT_SECTIONS)}')
    elif kind == 'section_place':
        _section(op, where)
        _int(op, 'column', where, 1, 3)
        if 'span' in op:
            _int(op, 'span', where, 1, 3)
        if 'side_by_side_with' in op:
            if _section(op, where, 'side_by_side_with') == op['section']:
                raise Invalid(f'{where}: a section is not beside itself')
    elif kind in ('place', 'remove'):
        k, _ = parse_ref(op['ref'], f'{where}.ref')
        if k == 'workspace':
            raise Invalid(f'{where}: workspaces are navigation and are never placed')
    elif kind in ('save', 'restore'):
        _name(op, where)
    return op


def check_ops(ops):
    if not isinstance(ops, list) or not 1 <= len(ops) <= OPS_MAX:
        raise Invalid(f'ops must be a list of 1..{OPS_MAX} operations')
    return [check_op(op, f'ops[{i}]') for i, op in enumerate(ops)]


# ── applying ops to a state ─────────────────────────────────────────────────────────────────────
def _moved(before, after):
    """'Moved Agents above Workspaces' when exactly one section moved; else a generic line."""
    if before == after:
        return 'Kept the section order'
    single = [x for x in after if [y for y in before if y != x] == [y for y in after if y != x]]
    if not single:
        return 'Reordered the sections'
    x = min(single, key=lambda v: after.index(v) - before.index(v))
    i = after.index(x)
    if i + 1 < len(after):
        return f'Moved {TITLES[x]} above {TITLES[after[i + 1]]}'
    return f'Moved {TITLES[x]} below {TITLES[after[i - 1]]}'


def _reposition(lst, x, other, before):
    lst = [v for v in lst if v != x]
    if other not in lst:
        lst.append(other)
    i = lst.index(other)
    lst.insert(i if before else i + 1, x)
    return lst


class State:
    def __init__(self, layout, placements, now, caller, saved, card_readable=None):
        self.layout = normalise(layout)
        self.placements = [dict(p) for p in placements]
        self.now, self.caller, self.saved = now, caller, saved
        self.card_readable = card_readable
        self.name = None
        self.phrases = []

    def sec(self, sid):
        return next(x for x in self.layout['sections'] if x['id'] == sid)

    def order(self):
        return [x['id'] for x in self.layout['sections']]

    def set_order(self, ids):
        by = {x['id']: x for x in self.layout['sections']}
        self.layout['sections'] = [by[i] for i in ids]

    def apply(self, op, where):
        kind = op['op']
        L = self.layout
        if kind in ('show', 'hide', 'collapse', 'expand'):
            key, value = {'show': ('hidden', False), 'hide': ('hidden', True),
                          'collapse': ('collapsed', True), 'expand': ('collapsed', False)}[kind]
            self.sec(op['section'])[key] = value
            verb = {'show': 'Showed', 'hide': 'Hid', 'collapse': 'Collapsed', 'expand': 'Expanded'}[kind]
            return self.phrases.append(f'{verb} {TITLES[op["section"]]}')
        if kind in ('section_top', 'section_order') or (kind == 'move' and op['id'] in DEFAULT_SECTIONS):
            before = self.order()
            if kind == 'section_top':
                after = [op['section']] + [x for x in before if x != op['section']]
            elif kind == 'section_order':
                after = list(op['sections']) + [x for x in before if x not in op['sections']]
            else:
                after = _reposition(before, op['id'], op.get('before', op.get('after')), 'before' in op)
            self.set_order(after)
            return self.phrases.append(_moved(before, after))
        if kind == 'section_place':
            sec = self.sec(op['section'])
            sec['column'] = op['column']
            sec['span'] = op.get('span', sec.get('span', 1))
            other = op.get('side_by_side_with')
            if other:
                self.set_order(_reposition(self.order(), op['section'], other, False))
                return self.phrases.append(f'Put {TITLES[op["section"]]} beside {TITLES[other]}')
            return self.phrases.append(f'Put {TITLES[op["section"]]} in column {op["column"]}')
        if kind == 'section_size':
            self.sec(op['section'])['size'] = op['size']
            return self.phrases.append(f'Made {TITLES[op["section"]]} {op["size"]}')
        if kind == 'order':
            L['order'][op['section']] = _ids(op['ids'], where, op['section'])
            return self.phrases.append(f'Reordered {TITLES[op["section"]]}')
        if kind == 'move':
            sid = SECTION_OF_KIND[op['id'].split(':', 1)[0]]
            other = op.get('before', op.get('after'))
            L['order'][sid] = _reposition(list(L['order'].get(sid) or []), op['id'], other, 'before' in op)
            return self.phrases.append(f'Reordered {TITLES[sid]}')
        if kind == 'group':
            sid = op['section']
            if 'by' in op:
                L['groups'][sid] = {'by': op['by']}
                return self.phrases.append(f'Grouped {TITLES[sid]} by {op["by"]}')
            L['groups'][sid] = {'named': [{'title': g['title'].strip(), 'ids': _ids(g['ids'], where, sid)}
                                          for g in op['named']]}
            n = len(op['named'])
            return self.phrases.append(f'Grouped {TITLES[sid]} into {n} group{"s" if n != 1 else ""}')
        if kind == 'filter':
            sid = op['section']
            if op['where'] is None:
                L['filters'].pop(sid, None)
                return self.phrases.append(f'Cleared the {TITLES[sid]} filter')
            L['filters'][sid] = {'where': op['where'], **({'until': op['until']} if 'until' in op else {})}
            return self.phrases.append(f'Filtered {TITLES[sid]}')
        if kind == 'place':
            ref = op['ref']
            if ref.startswith('file:') and not readable_file(self.caller, ref):
                raise DeskRefused(NOT_READABLE, 403)
            if ref.startswith('card:'):
                if self.card_readable is None:
                    raise DeskRefused(REF_KIND, 422)
                if not self.card_readable(ref):
                    raise DeskRefused(CARD_NOT_READABLE, 403)
                L['hidden_cards'] = [r for r in L['hidden_cards'] if r != ref]
            if all(p['ref'] != ref for p in self.placements):
                self.placements.append({'ref': ref, 'placed_at': self.now})
            return self.phrases.append(f'Placed {label(ref)}')
        if kind == 'remove':
            ref = op['ref']
            self.placements = [p for p in self.placements if p['ref'] != ref]
            if ref.startswith('card:') and ref not in L['hidden_cards']:
                L['hidden_cards'].append(ref)       # a member card leaves my desk; I stay its member
            sid = SECTION_OF_KIND[ref.split(':', 1)[0]]
            if ref in (L['order'].get(sid) or []):
                L['order'][sid] = [v for v in L['order'][sid] if v != ref]
            return self.phrases.append(f'Removed {label(ref)}')
        if kind == 'save':
            self.name = _name(op, where)
            return self.phrases.append(f'Saved as "{self.name}"')
        if kind == 'restore':
            name = _name(op, where)
            found = self.saved(name)
            if found is None:
                raise Invalid(f'{where}: no saved arrangement named "{name}"')
            self.layout = normalise(found['layout'])
            self.placements = [dict(p) for p in found['placements']]
            return self.phrases.append(f'Restored "{name}"')
        raise Invalid(f'{where}: unknown op')  # pragma: no cover — check_op admits only the above

    def summary(self):
        phrases = []
        for p in self.phrases:
            if not phrases or phrases[-1] != p:
                phrases.append(p)
        if not phrases:
            return 'No change'
        rest = [p[:1].lower() + p[1:] for p in phrases[1:]]
        if not rest:
            return phrases[0]
        if len(rest) == 1:
            return f'{phrases[0]} and {rest[0]}'
        more = len(rest) - 1
        return f'{phrases[0]}, {rest[0]} and {more} more change{"s" if more != 1 else ""}'


def may_arrange(caller: Caller):
    """Direct only for the person, or a worker with the person in the loop in their own chat."""
    if caller.internal or not caller.subject:
        return False
    if not caller.delegated:
        return True
    return caller.regime == 'human' and not caller.shared


def author_of(caller: Caller):
    return 'agent:' + (caller.session or 'unknown') if caller.delegated else 'person'


class Desk:
    def __init__(self, store, clock=None, limits=None, cards=None):
        self.store = store
        #: card_ops.Cards (desktop D2): the cards on this desk, while ``desktop_cards`` is on
        self.cards = cards
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._limits = limits

    def limits(self):
        """The company's limits now (``policies.Policies``); the defaults when none were given or the
        source fails. A value is used only if it narrows its default — the source already refuses a
        wider one, and this keeps a source that does not from widening a cap."""
        out = dict(LIMIT_DEFAULTS)
        if self._limits is None:
            return out
        try:
            got = self._limits() or {}
        except Exception:  # noqa: BLE001 — a policy we cannot read is the defaults, never a failed call
            return out
        for key, default in LIMIT_DEFAULTS.items():
            v = got.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= default:
                out[key] = v
        return out

    def _ttl(self, limits=None):
        return timedelta(days=(limits or self.limits())['card_proposal_ttl_days'])

    @property
    def engine(self):
        return self.store.engine

    # ── reads ─────────────────────────────────────────────────────────────────────────────────
    def _current(self, c, org, person):
        row = c.execute(select(s.desk_layouts).where(
            s.desk_layouts.c.org_id == org, s.desk_layouts.c.person == person)
            .order_by(s.desk_layouts.c.version.desc()).limit(1)).mappings().first()
        if row is None:
            return 0, normalise({}), []
        return row['version'], normalise(row['layout']), list(row['placements'] or [])

    def _version(self, c, org, person, version):
        if version == 0:
            return {'layout': {}, 'placements': []}
        return c.execute(select(s.desk_layouts).where(
            s.desk_layouts.c.org_id == org, s.desk_layouts.c.person == person,
            s.desk_layouts.c.version == version)).mappings().first()

    def _saved_lookup(self, c, org, person):
        def find(name):
            return c.execute(select(s.desk_layouts).where(
                s.desk_layouts.c.org_id == org, s.desk_layouts.c.person == person, s.desk_layouts.c.name == name)
                .order_by(s.desk_layouts.c.version.desc()).limit(1)).mappings().first()
        return find

    def _pending(self, c, org, person, ttl=None):
        now, ttl = self.clock(), ttl or self._ttl()
        rows = c.execute(select(s.desk_proposals).where(
            s.desk_proposals.c.org_id == org, s.desk_proposals.c.person == person,
            s.desk_proposals.c.status == 'pending')).mappings().all()
        live = [r for r in rows if now - _at(r['created_at']) < ttl]
        return sorted(live, key=lambda r: r['created_at'], reverse=True)

    def _card_check(self, c, org, caller):
        """Whether a ``card:`` ref is readable by this caller, inside this transaction; None while
        cards are not served."""
        if self.cards is None or not features.cards_served():
            return None
        from .card_ops import card_id
        return lambda ref: self.cards.readable(c, org, caller, card_id(ref))

    def read(self, org, actor):
        caller = as_caller(actor)
        person = caller.subject
        with self.engine.connect() as c:
            version, layout, placements = self._current(c, org, person)
            seen = {r['ref']: r['seen_at'] for r in c.execute(select(s.desk.c.ref, s.desk.c.seen_at).where(
                s.desk.c.org_id == org, s.desk.c.person == person)).mappings()}
            proposals = []
            for p in self._pending(c, org, person):
                proposals.append({'id': p['id'], 'ops': p['ops'], 'reason': p['reason'], 'author': p['author'],
                                  'created_at': p['created_at'],
                                  'summary': self._preview(c, org, caller, layout, placements, p['ops'])})
            names = []
            for (name,) in c.execute(select(s.desk_layouts.c.name).where(
                    s.desk_layouts.c.org_id == org, s.desk_layouts.c.person == person,
                    s.desk_layouts.c.name.is_not(None)).order_by(s.desk_layouts.c.version.desc())):
                if name not in names:
                    names.append(name)
        out = {'version': version, 'layout': layout,
               'placements': [{'ref': p['ref'], 'placed_at': p['placed_at'], 'seen_at': seen.get(p['ref'])}
                              for p in placements],
               'proposals': proposals, 'saved': names, 'undo_version': version - 1 if version > 0 else None}
        if self.cards is not None and features.cards_served():
            out.update(self.cards.desk(org, caller, placements, layout))
        return out

    def _preview(self, c, org, caller, layout, placements, ops):
        state = State(layout, placements, self.clock().isoformat(), caller,
                      self._saved_lookup(c, org, caller.subject), self._card_check(c, org, caller))
        try:
            for i, op in enumerate(ops):
                state.apply(op, f'ops[{i}]')
        except CRMError:
            return 'Suggested a new arrangement'
        return state.summary()

    # ── writes ────────────────────────────────────────────────────────────────────────────────
    def _apply(self, c, org, caller, ops, base_version, name, author, trace):
        person = caller.subject
        version, layout, placements = self._current(c, org, person)
        if base_version != version:
            raise DeskRefused(desk_changed(version), 409)
        state = State(layout, placements, self.clock().isoformat(), caller, self._saved_lookup(c, org, person),
                      self._card_check(c, org, caller))
        for i, op in enumerate(ops):
            state.apply(op, f'ops[{i}]')
        if name:
            state.name = name
            state.phrases.append(f'Saved as "{name}"')
        cap = self.limits()['desk_placements_max']
        if over(state.placements, placements, cap):
            raise DeskRefused(placements_full(cap), 409)
        return self._commit(c, org, person, version, state.layout, state.placements, ops, state.name,
                            author, trace, state.summary())

    def _commit(self, c, org, person, version, layout, placements, ops, name, author, trace, summary):
        new = version + 1
        try:
            c.execute(s.desk_layouts.insert().values(
                org_id=org, person=person, version=new, name=name, layout=layout, placements=placements,
                ops=ops, author=author, trace=trace or '', created_at=self.clock().isoformat()))
        except IntegrityError:
            raise DeskRefused(desk_changed(version), 409) from None
        refs = {p['ref'] for p in placements}
        have = {r for (r,) in c.execute(select(s.desk.c.ref).where(s.desk.c.org_id == org, s.desk.c.person == person))}
        gone = have - refs
        if gone:
            c.execute(delete(s.desk).where(s.desk.c.org_id == org, s.desk.c.person == person, s.desk.c.ref.in_(gone)))
        for p in placements:
            if p['ref'] not in have:
                c.execute(s.desk.insert().values(org_id=org, person=person, ref=p['ref'],
                                                 placed_at=p['placed_at'], seen_at=None))
        return {'status': 'applied', 'version': new, 'undo_version': version, 'summary': summary}

    def arrange(self, org, actor, ops, base_version, name=None):
        caller = as_caller(actor)
        ops = check_ops(ops)
        if not may_arrange(caller):
            raise DeskRefused(PROPOSE_INSTEAD, 403)
        name = _name({'name': name}, 'name') if name else None
        with self.engine.begin() as c:
            return self._apply(c, org, caller, ops, base_version, name, author_of(caller), caller.trace)

    def undo(self, org, actor, version, base_version):
        caller = as_caller(actor)
        if not may_arrange(caller):
            raise DeskRefused(PROPOSE_INSTEAD, 403)
        person = caller.subject
        with self.engine.begin() as c:
            current, _, _ = self._current(c, org, person)
            if base_version != current:
                raise DeskRefused(desk_changed(current), 409)
            if not isinstance(version, int) or version < 0 or version > current:
                raise NotFound('No such desktop version')
            row = self._version(c, org, person, version)
            if row is None:
                raise NotFound('No such desktop version')
            summary = ('Restored the default desktop' if version == 0 else
                       'Undid the last change' if version == current - 1 else 'Restored an earlier arrangement')
            return self._commit(c, org, person, current, normalise(row['layout']), list(row['placements'] or []),
                                [{'op': 'undo', 'version': version}], None, author_of(caller), caller.trace, summary)

    def propose(self, org, actor, ops, reason):
        caller = as_caller(actor)
        ops = check_ops(ops)
        reason = str(reason or '').strip()
        if not reason or len(reason) > 500:
            raise Invalid('reason must be 1..500 characters')
        if caller.internal or not caller.subject:
            raise DeskRefused(PROPOSE_INSTEAD, 403)
        person, author, now = caller.subject, author_of(caller), self.clock()
        limits = self.limits()
        with self.engine.begin() as c:
            version, layout, placements = self._current(c, org, person)
            state = State(layout, placements, now.isoformat(), caller, self._saved_lookup(c, org, person),
                          self._card_check(c, org, caller))
            for i, op in enumerate(ops):
                state.apply(op, f'ops[{i}]')          # refs the proposer cannot read are refused now
            if over(state.placements, placements, limits['desk_placements_max']):
                raise DeskRefused(placements_full(limits['desk_placements_max']), 409)
            if len(self._pending(c, org, person, self._ttl(limits))) >= limits['card_proposals_pending_max']:
                raise DeskRefused(pending_full(limits['card_proposals_pending_max']), 409)
            day = [r for (r,) in c.execute(select(s.desk_proposals.c.created_at).where(
                s.desk_proposals.c.org_id == org, s.desk_proposals.c.author == author,
                s.desk_proposals.c.person == person)) if now - _at(r) < timedelta(days=1)]
            if caller.delegated and len(day) >= limits['card_proposals_per_agent_per_day']:
                raise DeskRefused(daily_full(limits['card_proposals_per_agent_per_day']), 409)
            pid = str(uuid4())
            c.execute(s.desk_proposals.insert().values(
                org_id=org, id=pid, person=person, ops=ops, reason=reason, author=author,
                trace=caller.trace or '', status='pending', created_at=now.isoformat()))
        return {'status': 'proposed', 'proposal_id': pid}

    def review(self, org, actor, proposal_id, apply, base_version):
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise DeskRefused(CONFIRM, 403)
        with self.engine.begin() as c:
            p = c.execute(select(s.desk_proposals).where(
                s.desk_proposals.c.org_id == org, s.desk_proposals.c.id == str(proposal_id),
                s.desk_proposals.c.person == caller.subject)).mappings().first()
            if p is None:
                raise NotFound('Proposal not found')
            if p['status'] != 'pending':
                raise Conflict('Proposal already ' + p['status'])
            if self.clock() - _at(p['created_at']) >= self._ttl():
                raise NotFound('Proposal expired')
            result = {'status': 'dismissed'}
            if apply:
                result = self._apply(c, org, caller, check_ops(p['ops']), base_version, None, 'person', caller.trace)
            c.execute(update(s.desk_proposals).where(s.desk_proposals.c.org_id == org,
                                                     s.desk_proposals.c.id == p['id'])
                      .values(status='applied' if apply else 'dismissed'))
            return result


def over(after, before, cap):
    """A change that leaves more placed items than ``cap`` AND more than before is refused. A desk
    already above a cap the company has since narrowed may still be rearranged and thinned."""
    return len(after) > cap and len(after) > len(before)


def _at(iso):
    at = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
