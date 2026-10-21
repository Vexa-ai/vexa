"""person_profiles.py — a profile is a CLAIMED CRM Person record (PRD 0.13.4 D35, founder addition).

**Claim.** A signed-in person claims the Person record that matches the address identity verified for
them, in their home org's CRM (``caller.org``). The claim is strictly one-to-one: ``crm_profiles`` is
keyed by the record and unique per account. A second account of the same human never claims a second
record: it resolves to the claimed Person and joins it through the merge card (``persons.py``).

- It happens on its own at sign-in when the record holding the verified address's match identifier
  (``email:<address>``) is unclaimed. The record's ``Email`` field never makes a claim: any writer
  of that field can type an address into it. A record the claimant merged another record into by
  hand is not theirs to claim either (``candidates``). With no verified address there is no claim: the address
  counts only when identity says a sign-in proved it (``Caller.email_verified``), so an address an
  API call or an identity provider merely asserted claims nothing.
- A claim moves nothing from the org to the person. The org's fields (contact history, notes, deal
  links, cadence) stay on the record under its CRM policy and field classes (S1-C); the person gets an
  empty profile of their own.

**Fields split by owner.** ``PROFILE_FIELDS`` are the person's, edited ONLY by the person who claimed
the record, from their own signed-in session (never a worker, never an administrator). Everything
else on the record is the org's, unchanged.

**Visibility.** Profile fields follow the directory rule — anyone who shares a workspace with the
person (agent-api applies it before calling ``directory``). The public page (``/p/<handle>``) is off
until the person switches it on, and shows only the fields they marked public; no org field is ever
part of a profile answer, because no profile answer reads ``crm_records.fields``.

**Unclaim** keeps the record and the profile values; ``claimed_by`` is cleared, the public page goes
off, and the fields are the org's again (editable by whoever may write the record).

**Social links** are stored as links (https, on the network's own domain). Nothing is fetched or
imported. ``IMPORTERS`` is the seam for an "import from LinkedIn" the person starts; it is empty.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select, update

from . import schema as s
from .caller import as_caller
from .persons import EMAIL, PersonError, PersonNotFound, PersonRefused

#: field → (kind, max length). ``links`` and ``contact`` are objects of their own.
PROFILE_FIELDS = {
    'display_name': ('text', 120), 'photo': ('url', 500), 'headline': ('text', 160),
    'company': ('text', 120), 'bio': ('text', 1000), 'location': ('text', 120),
    'timezone': ('tz', 64), 'links': ('links', 0), 'contact': ('contact', 0),
}
LINK_HOSTS = {'linkedin': ('linkedin.com',), 'github': ('github.com',), 'x': ('x.com', 'twitter.com'),
              'website': None}
CONTACT_CHANNELS = ('email', 'chat', 'call')
_TZ = re.compile(r'^[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){0,2}$|^UTC$')
_CTRL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')

#: "Import from LinkedIn" and the like: an importer the PERSON starts, ``name -> callable(caller) ->
#: dict of PROFILE_FIELDS``. None ships; ``import_profile`` refuses until one is registered.
IMPORTERS: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _https(value, hosts=None) -> str:
    v = str(value or '').strip()
    u = urlparse(v)
    if u.scheme != 'https' or not u.hostname or len(v) > 500:
        raise PersonError('A link is an https address')
    if hosts and not any(u.hostname == h or u.hostname.endswith('.' + h) for h in hosts):
        raise PersonError(f'That link is not on {hosts[0]}')
    return v


def clean(fields: dict) -> dict:
    """The person's profile fields, validated; an unknown field is refused, an empty one removed."""
    if not isinstance(fields, dict):
        raise PersonError('Profile fields are an object')
    out = {}
    for name, value in fields.items():
        if name not in PROFILE_FIELDS:
            raise PersonError(f'{name!r} is not a profile field')
        kind, limit = PROFILE_FIELDS[name]
        if value in (None, '', {}, []):
            continue
        if kind == 'text':
            v = _CTRL.sub(' ', str(value)).strip()
            if len(v) > limit:
                raise PersonError(f'{name} is at most {limit} characters')
            out[name] = v
        elif kind == 'url':
            out[name] = _https(value)
        elif kind == 'tz':
            v = str(value).strip()
            if not _TZ.fullmatch(v) or len(v) > limit:
                raise PersonError('A time zone is like Europe/Vienna')
            out[name] = v
        elif kind == 'links':
            if not isinstance(value, dict):
                raise PersonError('links is an object')
            links = {}
            for k, v in value.items():
                if k not in LINK_HOSTS:
                    raise PersonError(f'{k!r} is not a link this profile keeps')
                if v:
                    links[k] = _https(v, LINK_HOSTS[k])
            if links:
                out[name] = links
        elif kind == 'contact':
            if not isinstance(value, dict) or set(value) - {'prefer', 'note'}:
                raise PersonError('contact is {prefer, note}')
            c = {}
            if value.get('prefer'):
                if value['prefer'] not in CONTACT_CHANNELS:
                    raise PersonError('prefer is email, chat or call')
                c['prefer'] = value['prefer']
            if value.get('note'):
                note = _CTRL.sub(' ', str(value['note'])).strip()
                if len(note) > 200:
                    raise PersonError('The contact note is at most 200 characters')
                c['note'] = note
            if c:
                out[name] = c
    return out


#: identity's retired profile field → this profile's field
LEGACY_MAP = {'name': 'display_name', 'photo': 'photo', 'role': 'headline', 'company': 'company', 'bio': 'bio'}


def from_legacy(legacy: dict) -> tuple[dict, list]:
    """(fields, public_fields) out of identity's retired profile: the person's own entries, each kept
    only if it passes this profile's rules; its links sorted onto LinkedIn, GitHub, X or website."""
    fields, public = {}, []
    for old, f in ((legacy or {}).get('fields') or {}).items():
        value = (f or {}).get('value') if isinstance(f, dict) else f
        is_public = bool((f or {}).get('public')) if isinstance(f, dict) else False
        if old in LEGACY_MAP and value:
            try:
                fields.update(clean({LEGACY_MAP[old]: value}))
            except PersonError:
                continue
            if is_public and LEGACY_MAP[old] in fields:
                public.append(LEGACY_MAP[old])
        elif old == 'links' and value:
            links = {}
            for url in (value if isinstance(value, list) else [value]):
                host = (urlparse(str(url)).hostname or '').lower()
                key = next((k for k, hosts in LINK_HOSTS.items() if hosts and any(
                    host == h or host.endswith('.' + h) for h in hosts)), 'website')
                if key not in links:
                    try:
                        links.update(clean({'links': {key: url}})['links'])
                    except (PersonError, KeyError):
                        continue
            if links:
                fields['links'] = links
                if is_public:
                    public.append('links')
    return fields, sorted(set(public))


class Profiles:
    def __init__(self, store):
        self.store = store
        self.engine = store.engine
        self.persons = store.persons
        #: identity's retired profile, read once on a claim (``ProfileClient.legacy``); None = none
        self.legacy = None

    def carry_legacy(self, org, subject, record_id):
        """After a claim: move the person's entries from identity's retired profile into this one, if
        this one is still empty. The public page stays OFF (the old directory was for signed-in people;
        the open web is a new audience, the person's to choose), but the old handle is kept so the old
        link resolves at ``/p/<handle>`` once they switch it on."""
        if self.legacy is None or not record_id:
            return False
        legacy = self.legacy(subject)
        if not legacy:
            return False
        fields, public = from_legacy(legacy)
        handle = legacy.get('handle') if re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(legacy.get('handle') or '')) else None
        with self.engine.begin() as c:
            row = self._by_record(c, org, record_id)
            if row is None or row['claimed_by'] != str(subject) or row['fields']:
                return False
            if handle and c.execute(select(s.profiles.c.record_id).where(s.profiles.c.handle == handle)).first():
                handle = None
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == record_id)
                      .values(fields=fields, public_fields=public, handle=handle, published=False, updated_at=_now()))
        return True

    # ── reading rows ─────────────────────────────────────────────────────────────────────────
    def _by_record(self, c, org, record_id):
        row = c.execute(select(s.profiles).where(s.profiles.c.org_id == org,
                                                 s.profiles.c.record_id == record_id)).mappings().first()
        return dict(row) if row else None

    def _claimed_by(self, c, org, subject):
        row = c.execute(select(s.profiles).where(s.profiles.c.org_id == org,
                                                 s.profiles.c.claimed_by == str(subject))).mappings().first()
        return dict(row) if row else None

    def candidates(self, c, org, email, subject):
        """Person records ``subject`` may claim with the verified address ``email``: the live record
        (a merged id answers its survivor) holding the match identifier ``email:<address>``.

        The identifier is the binding, not the record's ``Email`` field: any writer of that field can
        type an address into it. An identifier is written by the sign-in that creates a Person, by
        the org when it creates a record, and by a merge carrying a duplicate's identifiers to its
        survivor. A merge is the one way a writer puts an address onto a record someone else already
        has, so a record ``subject`` merged another record into by hand (``store.merge``, not the
        person's own merge card) is not theirs to claim: the address may be the one they carried."""
        email = str(email or '').strip().lower()
        if not email:
            return []
        held = self.persons._held(c, org, EMAIL + email)
        row = self.persons._follow(c, org, held) if held else None
        if row is None or self._merged_in_by(c, org, row['id'], subject):
            return []
        return [row]

    @staticmethod
    def _merged_in_by(c, org, record_id, subject):
        """True when ``subject`` merged a record into ``record_id`` (directly or through a record
        that later merged into it) with a CRM merge."""
        r, v = s.records, s.revisions
        into, frontier = set(), {record_id}
        while frontier:
            frontier = {i for (i,) in c.execute(select(r.c.id).where(
                r.c.org_id == org, r.c.merged_into.in_(frontier))).all()} - into
            into |= frontier
        if not into:
            return False
        for (evidence,) in c.execute(select(v.c.evidence).where(
                v.c.org_id == org, v.c.record_id.in_(into), v.c.actor_id == str(subject))).all():
            for e in evidence if isinstance(evidence, list) else []:
                if isinstance(e, dict) and e.get('merged_into') and not e.get('card'):
                    return True
        return False

    def _human_claim(self, c, org, subject, email):
        """The claim any account of this human already holds: this account's own, or the claim on the
        Person this account resolves to (a second login of a claimed human). ``email`` is a proven
        address or "" (``persons.resolve``)."""
        mine = self._claimed_by(c, org, subject)
        if mine:
            return mine
        person = self.persons.resolve(c, org, subject, email)
        if person is not None:
            row = self._by_record(c, org, person['id'])
            if row and row['claimed_by']:
                return row
        return None

    # ── claim ────────────────────────────────────────────────────────────────────────────────
    def _write_claim(self, c, org, record, subject):
        now = _now()
        existing = self._by_record(c, org, record['id'])
        if existing is None:
            c.execute(s.profiles.insert().values(org_id=org, record_id=record['id'], claimed_by=subject,
                                                 unclaimed_by=None, fields={}, public_fields=[], published=False,
                                                 handle=None, claimed_at=now, updated_at=now))
        else:
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == record['id'])
                      .values(claimed_by=subject, unclaimed_by=None, claimed_at=now, updated_at=now))
        # the account is attached to the record it claimed, so it resolves there however it signs in
        if not record.get('account_id') and c.execute(select(s.records.c.id).where(
                s.records.c.org_id == org, s.records.c.account_id == subject)).scalar_one_or_none() is None:
            c.execute(update(s.records).where(s.records.c.org_id == org, s.records.c.id == record['id'])
                      .values(account_id=subject))

    def auto_claim(self, c, org, caller):
        """On sign-in: claim the one record matching the verified address, if exactly one does, it is
        unclaimed, this account did not unclaim it, and no account of this human holds a claim."""
        caller = as_caller(caller)
        if caller.delegated or caller.internal or not caller.subject or not caller.email or not caller.email_verified:
            return None
        if self._human_claim(c, org, caller.subject, caller.email):
            return None
        found = self.candidates(c, org, caller.email, caller.subject)
        if len(found) != 1:
            return None
        row = self._by_record(c, org, found[0]['id'])
        if row and (row['claimed_by'] or row['unclaimed_by'] == caller.subject):
            return None
        self._write_claim(c, org, found[0], caller.subject)
        return found[0]['id']

    def claim(self, org, caller, record_id=None):
        """The person's own claim (the claim card): the one match, or the one they pick among several."""
        caller = self.persons._person(caller)
        if not caller.email or not caller.email_verified:
            e = PersonError('A claim needs an address a sign-in verified')
            e.status = 403
            raise e
        with self.engine.begin() as c:
            held = self._human_claim(c, org, caller.subject, caller.email)
            if held:
                if held['claimed_by'] == caller.subject and (not record_id or record_id == held['record_id']):
                    return {'status': 'claimed', 'record_id': held['record_id']}
                e = PersonError('You already have a profile; a second address joins it through the merge card')
                e.status = 409
                raise e
            found = self.candidates(c, org, caller.email, caller.subject)
            if record_id:
                found = [r for r in found if r['id'] == str(record_id)]
                if not found:
                    raise PersonNotFound('No record matching your verified address has that id')
            if not found:
                raise PersonNotFound('No record matches your verified address')
            if len(found) > 1:
                return {'status': 'choose', 'candidates': [self._candidate(r) for r in found]}
            row = self._by_record(c, org, found[0]['id'])
            if row and row['claimed_by']:
                e = PersonError('Someone else has claimed that record')
                e.status = 409
                raise e
            self._write_claim(c, org, found[0], caller.subject)
            return {'status': 'claimed', 'record_id': found[0]['id']}

    @staticmethod
    def _candidate(row):
        # how the org names the record, and nothing else from it (no notes, no history)
        return {'record_id': row['id'], 'name': str((row.get('fields') or {}).get('Name') or '')}

    def unclaim(self, org, caller):
        caller = self.persons._person(caller)
        with self.engine.begin() as c:
            row = self._claimed_by(c, org, caller.subject)
            if row is None:
                raise PersonNotFound('You have not claimed a record')
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == row['record_id'])
                      .values(claimed_by=None, unclaimed_by=caller.subject, published=False, handle=None,
                              updated_at=_now()))
            return {'status': 'unclaimed', 'record_id': row['record_id']}

    # ── the person's own read and edit ───────────────────────────────────────────────────────
    def mine(self, org, caller):
        caller = self.persons._person(caller)
        with self.engine.begin() as c:
            row = self._claimed_by(c, org, caller.subject)
            if row is None:
                held = self._human_claim(c, org, caller.subject, caller.proven_email)
                if held:
                    return {'claimed': True, 'via_merge': True, 'record_id': held['record_id'],
                            'fields': dict(held['fields'] or {}), 'public_fields': list(held['public_fields'] or []),
                            'published': bool(held['published']), 'handle': None, 'candidates': []}
                found = self.candidates(c, org, caller.email, caller.subject) if caller.email_verified else []
                return {'claimed': False, 'record_id': None, 'fields': {}, 'public_fields': [], 'published': False,
                        'handle': None, 'candidates': [self._candidate(r) for r in found] if len(found) > 1 else []}
            return {'claimed': True, 'record_id': row['record_id'], 'fields': dict(row['fields'] or {}),
                    'public_fields': list(row['public_fields'] or []), 'published': bool(row['published']),
                    'handle': row['handle'] if row['published'] else None, 'candidates': []}

    def edit(self, org, caller, fields, record_id=None):
        """Profile fields are the claimant's alone. Unclaimed (``record_id``), they are the org's, and
        whoever may write every field of that record edits them."""
        caller = self.persons._person(caller)
        values = clean(fields)
        with self.engine.begin() as c:
            if record_id:
                row = self._by_record(c, org, str(record_id))
                if row is None:
                    raise PersonNotFound('No profile on that record')
                if row['claimed_by'] and row['claimed_by'] != caller.subject:
                    raise PersonRefused({'status': 'refused', 'reason': 'profile_is_the_persons',
                                         'instruction': 'Only the person who claimed this record edits its profile.'})
                if not row['claimed_by']:
                    policy = self.store._policy(c, org, caller)
                    if '*' not in self.store._rights(policy, self.store._row(c, org, row['record_id']), 'write'):
                        raise PersonRefused({'status': 'refused', 'reason': 'write_access_required',
                                             'instruction': 'Editing this unclaimed profile needs write access to the record.'})
            else:
                row = self._claimed_by(c, org, caller.subject)
                if row is None:
                    raise PersonNotFound('Claim your record first')
            merged = {**(row['fields'] or {}), **values}
            for k, v in (fields or {}).items():
                if v in (None, '', {}, []):
                    merged.pop(k, None)
            public = [f for f in (row['public_fields'] or []) if f in merged]
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == row['record_id'])
                      .values(fields=merged, public_fields=public, updated_at=_now()))
            return {'status': 'saved', 'fields': merged}

    def publish(self, org, caller, on, public_fields=None):
        """The public page: off by default, on only by the person, showing only the fields they mark."""
        caller = self.persons._person(caller)
        with self.engine.begin() as c:
            row = self._claimed_by(c, org, caller.subject)
            if row is None:
                raise PersonNotFound('Claim your record first')
            public = list(row['public_fields'] or [])
            if public_fields is not None:
                bad = [f for f in public_fields if f not in PROFILE_FIELDS]
                if bad:
                    raise PersonError(f'{bad[0]!r} is not a profile field')
                public = sorted(set(public_fields))
            handle = row['handle']
            if on and not handle:
                handle = secrets.token_urlsafe(16)
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == row['record_id'])
                      .values(published=bool(on), public_fields=public, handle=handle if on else row['handle'],
                              updated_at=_now()))
            return {'published': bool(on), 'handle': handle if on else None, 'public_fields': public}

    def import_profile(self, org, caller, source):
        caller = self.persons._person(caller)
        importer = IMPORTERS.get(str(source or ''))
        if importer is None:
            raise PersonError('Importing a profile is not available yet; add the fields yourself')
        return self.edit(org, caller, importer(caller))

    # ── for the platform ─────────────────────────────────────────────────────────────────────
    def directory(self, org, accounts):
        """``accounts``: ``[{subject, email, email_verified}]`` — the people agent-api's directory rule
        already let the viewer see. Each one's profile fields (their claimed record's, also for a
        second login), or None. Profile fields only: no org field is read. The address finds the
        Person only when ``email_verified`` vouches for it (``persons.resolve``)."""
        out = {}
        with self.engine.connect() as c:
            for a in list(accounts or [])[:500]:
                subject = str((a or {}).get('subject') or '').strip()
                if not subject:
                    continue
                email = str((a or {}).get('email') or '') if (a or {}).get('email_verified') is True else ''
                row = self._human_claim(c, org, subject, email)
                out[subject] = dict(row['fields'] or {}) if row else None
        return out

    def public(self, handle):
        """The public page: the fields the person marked public, while it is switched on; else None."""
        handle = str(handle or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', handle):
            return None
        with self.engine.connect() as c:
            row = c.execute(select(s.profiles).where(s.profiles.c.handle == handle)).mappings().first()
        if row is None or not row['published'] or not row['claimed_by']:
            return None
        fields = dict(row['fields'] or {})
        return {'handle': handle, 'fields': {k: v for k, v in fields.items() if k in (row['public_fields'] or [])}}
