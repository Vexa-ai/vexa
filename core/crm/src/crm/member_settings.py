"""Workspace settings: one person's settings for one workspace, on the CRM `member_of` relation.

Pinned, hidden from home, muted, the notification level, the room order, a level override and the
collapsed sections (`crm_member_settings`, schema.py says why it is its own table). The rule they
follow (architecture addendum, 2026-10-16): per-person state about a workspace lives on the CRM
relation, crm-api is its only writer, and the rows live in the person's own boundary's CRM. Never in
browser storage, never in an agent-api table.

- **The person is the caller's subject**, never an argument: a read answers only the caller's own
  rows, a write changes only the caller's own row.
- **A shared turn reads none of them**: other people read its answer, so a read there is refused
  with ``person_only`` like a write.
- **Who writes** is decided as the desk decides it (`desk.may_arrange`): the person themselves, or a
  worker in a turn the person typed in their own one-person chat. A routine, an event agent or a
  shared chat is refused with ``person_only`` and nothing changes.
- **Settings carry no access.** A slug is checked for shape only (`personal` is the person's own);
  a row for a workspace the person cannot open grants nothing, and the answer says nothing about the
  workspace — it echoes the caller's own row.
- **A set is partial**: the keys given merge onto what is there, or onto the defaults. An unknown key,
  or one bad value, refuses the whole set (422).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import schema as s
from .caller import Caller, as_caller
from .desk import _ID, _SLUG, DeskRefused, Invalid, _refusal, may_arrange

NOTIFY = ('all', 'mentions', 'none')
ROOM_ORDER_MAX = 100
COLLAPSED_MAX = 20
LEVELS = (1, 5)

#: What a slug with no row reads as. Never returned for a slug the person has not set.
DEFAULTS = {'pinned': False, 'hidden': False, 'muted': False, 'room_order': [],
            'level': None, 'collapsed': [], 'updated_at': None}
#: RETIRED (PRD 0.13.4 S6, one writer): the notification level per workspace lives in the person's
#: identity notification policy (`/user/notifications`, `rooms: ws:<slug>`), which the delivery gate
#: reads. The `notify` column stays for the rows already there and is filled with this constant on a
#: new row; nothing writes it, nothing answers it, and a set that names it is refused with the door.
NOTIFY_RETIRED_FILL = 'all'
NOTIFY_MOVED = ('settings.notify moved to the identity notification policy: Settings → Notifications '
                '(PUT /user/notifications with rooms {"ws:<slug>": "all" | "mentions" | "none"})')
#: The keys a set may name; `updated_at` is the server's.
KEYS = tuple(k for k in DEFAULTS if k != 'updated_at')

PERSON_ONLY = _refusal(
    'person_only',
    "Only the person, or you in a turn they typed in their own one-person chat, may change their "
    "workspace settings. Tell them in one line how to do it themselves; do not retry it another way.")


class PersonOnly(DeskRefused):
    def __init__(self):
        super().__init__(PERSON_ONLY, 403)


def check_slug(workspace):
    if not isinstance(workspace, str) or not _SLUG.fullmatch(workspace) or '..' in workspace:
        raise Invalid('workspace: not a workspace slug')
    return workspace


def _flag(value, key):
    if not isinstance(value, bool):
        raise Invalid(f'settings.{key} must be true or false')
    return value


def _list(value, key, cap):
    if not isinstance(value, list) or len(value) > cap:
        raise Invalid(f'settings.{key} must be a list of at most {cap} ids')
    out = []
    for n, item in enumerate(value):
        if not isinstance(item, str) or not _ID.fullmatch(item):
            raise Invalid(f'settings.{key}[{n}]: not an id')
        if item not in out:
            out.append(item)
    return out


def check_settings(settings):
    """The validated partial: only known keys, each a valid value. Empty is refused."""
    if not isinstance(settings, dict) or not settings:
        raise Invalid(f'settings must name at least one of {", ".join(KEYS)}')
    if 'notify' in settings:
        raise Invalid(NOTIFY_MOVED)
    unknown = sorted(set(settings) - set(KEYS))
    if unknown:
        raise Invalid(f'settings: unknown key {unknown[0]}; one of {", ".join(KEYS)}')
    out = {}
    for key, value in settings.items():
        if key in ('pinned', 'hidden', 'muted'):
            out[key] = _flag(value, key)
        elif key == 'level':
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)
                                      or not LEVELS[0] <= value <= LEVELS[1]):
                raise Invalid(f'settings.level must be a whole number {LEVELS[0]}..{LEVELS[1]}, or null')
            out[key] = value
        elif key == 'room_order':
            out[key] = _list(value, key, ROOM_ORDER_MAX)
        else:
            out[key] = _list(value, key, COLLAPSED_MAX)
    return out


def _view(row):
    return {'pinned': bool(row['pinned']), 'hidden': bool(row['hidden']), 'muted': bool(row['muted']),
            'room_order': list(row['room_order'] or []), 'level': row['level'],
            'collapsed': list(row['collapsed'] or []), 'updated_at': row['updated_at']}


class MemberSettings:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _person(self, caller: Caller):
        if caller.internal or not caller.subject:
            raise PersonOnly()
        return caller.subject

    def read(self, org, actor):
        caller = as_caller(actor)
        person = self._person(caller)
        if caller.shared:
            raise PersonOnly()          # other people read a shared turn: none of this is theirs
        t = s.member_settings
        with self.store.engine.connect() as c:
            rows = c.execute(select(t).where(t.c.org_id == org, t.c.person == person)
                             .order_by(t.c.workspace)).mappings().all()
        return {'workspaces': {r['workspace']: _view(r) for r in rows}}

    def set(self, org, actor, workspace, settings):
        caller = as_caller(actor)
        workspace = check_slug(workspace)
        patch = check_settings(settings)
        if not may_arrange(caller):
            raise PersonOnly()
        person, t = caller.subject, s.member_settings
        where = (t.c.org_id == org, t.c.person == person, t.c.workspace == workspace)
        for attempt in (0, 1):
            try:
                with self.store.engine.begin() as c:
                    row = c.execute(select(t).where(*where)).mappings().first()
                    merged = {**(_view(row) if row else DEFAULTS), **patch,
                              'updated_at': self.clock().isoformat()}
                    values = {k: merged[k] for k in DEFAULTS}
                    if row is None:
                        c.execute(t.insert().values(org_id=org, person=person, workspace=workspace,
                                                    notify=NOTIFY_RETIRED_FILL, **values))
                    else:
                        c.execute(update(t).where(*where).values(**values))
                return {'workspace': workspace, 'settings': merged}
            except IntegrityError:
                if attempt:                     # a concurrent first write won twice: give up loudly
                    raise
        raise AssertionError('unreachable')  # pragma: no cover
