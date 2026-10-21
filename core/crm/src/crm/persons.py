"""One person, many addresses (PRD 0.13.4 D35; decision I10; ADR-0047 d3).

There is no persons table. **The human is the CRM's Person record**, and its match identifiers say
which addresses are theirs. An account is a login: it resolves to its Person by the address identity
verified for it (``email:<verified address>``), and only when no Person holds that address by the
record its account is attached to (``account_id``).

One Person may therefore answer for several accounts — the founder's record of a colleague lists
her work and her personal address, and she has signed in with both. The accounts linked to a Person
are its attached account (``account_id``) plus one ``account:<id>`` match identifier per further
account that resolved to it. ``account:`` identifiers are written here only, never through
``crm_change``'s ``match``, and they are match identifiers, so the ``$match_ids`` field class keeps
them private to the record's owner and administrators like every other identifier.

**Grants go only to CONFIRMED accounts** (coordinator ruling 2026-10-17): before the person confirms
the merge card from each login's own session, the picker may hint "2 accounts?", but a grant reaches
only the account picked. After it, ``confirmed()`` is every account on the confirmed card; crm-api's
card membership reads it, and agent-api reads it through ``POST /internal/persons`` (``confirmed``).

**Merging is the person's act, never automatic.** When a Person has two or more linked accounts that
the person has not confirmed together, a *merge card* is offered: a ``crm_change_proposals`` row with
status ``merge_offered`` (statuses no other reader treats as pending, so it never shows up as a record
change, and ``crm_review`` cannot accept it). Every linked account confirms it from its own signed-in
session (a sign-in is the only proof that an address is yours), and the person chooses the primary
address there. Invites and nudges go to that address (``primary()``). Declining splits the Person:
the accounts that are not the record's own go back to records of their own, so grants stop following
them. A person may also start a merge themselves for an address that resolves to another Person
(``offer_for_address``); it completes only when that address's account confirms too.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update, delete

from . import schema as s
from .caller import CONFIRM_REFUSAL, as_caller
from .store import CRMError

ACCOUNT = 'account:'
EMAIL = 'email:'
MERGE_KIND = 'person_merge'
OFFERED, CONFIRMED, DECLINED = 'merge_offered', 'merge_confirmed', 'merge_declined'
MERGE_STATUSES = (OFFERED, CONFIRMED, DECLINED)
SYSTEM = 'system:person-resolution'
MAX_ACCOUNTS = 500
_MERGE_REASON = 'One person, many addresses (D35): the person confirms which accounts are theirs'


class PersonError(CRMError):
    status = 400


class PersonRefused(PersonError):
    status = 403

    def __init__(self, body=None):
        super().__init__('person_confirmation_required')
        self.body = body or CONFIRM_REFUSAL


class PersonNotFound(PersonError):
    status = 404


def mask(address: str) -> str:
    """``v•••@example.org``: enough for the person to recognise their own address on a card, not
    enough to read someone else's off it."""
    local, _, domain = str(address or '').partition('@')
    if not domain:
        return '•••'
    return (local[:1] or '') + '•••@' + domain


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Persons:
    """Resolution, linking and the merge card, over the Store's engine and tables."""

    def __init__(self, store):
        self.store = store
        self.engine = store.engine

    # ── resolution ───────────────────────────────────────────────────────────────────────────
    def _follow(self, c, org, record_id):
        """The live Person a record id answers to (merged records answer their survivor), or None."""
        for _ in range(8):
            row = c.execute(select(s.records).where(s.records.c.org_id == org,
                                                    s.records.c.id == record_id)).mappings().first()
            if row is None:
                return None
            if row['merged_into']:
                record_id = row['merged_into']
                continue
            if row['deleted'] or row['object_type'] != 'Person':
                return None
            return dict(row)
        return None

    def _held(self, c, org, identifier):
        return c.execute(select(s.match_ids.c.record_id).where(
            s.match_ids.c.org_id == org, s.match_ids.c.identifier == identifier)).scalar_one_or_none()

    def resolve(self, c, org, subject, email=''):
        """The Person an account answers to: by ``email`` first (I10), then by an ``account:`` link,
        then by the record the account is attached to. None when there is none. ``email`` is an
        address a sign-in PROVED (``Caller.proven_email``) or ""; an account that once signed in with
        a proven one is already attached or linked, so its subject alone finds it."""
        email = str(email or '').strip().lower()
        if email:
            held = self._held(c, org, EMAIL + email)
            row = self._follow(c, org, held) if held else None
            if row is not None:
                return row
        subject = str(subject or '')
        if not subject:
            return None
        held = self._held(c, org, ACCOUNT + subject)
        row = self._follow(c, org, held) if held else None
        if row is not None:
            return row
        attached = c.execute(select(s.records.c.id).where(s.records.c.org_id == org,
                                                          s.records.c.account_id == subject)).scalar_one_or_none()
        return self._follow(c, org, attached) if attached else None

    def linked(self, c, org, row):
        """Every account linked to this Person: the attached one, then the ``account:`` links."""
        out = [row['account_id']] if row.get('account_id') else []
        for (identifier,) in c.execute(select(s.match_ids.c.identifier).where(
                s.match_ids.c.org_id == org, s.match_ids.c.record_id == row['id'],
                s.match_ids.c.identifier.like(ACCOUNT + '%'))).all():
            who = identifier[len(ACCOUNT):]
            if who and who not in out:
                out.append(who)
        return out

    def confirmed(self, c, org, row, subject):
        """The accounts a grant to ``subject`` may reach: ``subject`` and every account the person
        confirmed together with it on a merge card, each from its own session. An unconfirmed link —
        an address someone put in the record's match identifiers — reaches nothing (coordinator
        ruling 2026-10-17: a wrong address never receives the person's grants)."""
        subject = str(subject)
        out = [subject]
        for card in self._cards(c, org, row['id'], (CONFIRMED,)):
            accounts = (card['patch'] or {}).get('accounts') or []
            if subject in accounts:
                for who in accounts:
                    if who not in out:
                        out.append(str(who))
        return out

    def addresses(self, c, org, record_id):
        return sorted(i[len(EMAIL):] for (i,) in c.execute(select(s.match_ids.c.identifier).where(
            s.match_ids.c.org_id == org, s.match_ids.c.record_id == record_id,
            s.match_ids.c.identifier.like(EMAIL + '%'))).all())

    def _cards(self, c, org, record_id, statuses=MERGE_STATUSES):
        p = s.change_proposals
        rows = c.execute(select(p).where(p.c.org_id == org, p.c.record_id == record_id,
                                         p.c.status.in_(statuses))).mappings().all()
        return sorted((dict(r) for r in rows if (r['patch'] or {}).get('kind') == MERGE_KIND),
                      key=lambda r: str((r['patch'] or {}).get('created_at') or ''))

    def _options(self, c, org, card):
        """The addresses the person may choose as primary on this card: every address of every Person
        on it, and the address each account signed in with. Keyed per card (``_option``), so a key is
        stable while the list grows and says nothing outside this card."""
        patch = card['patch'] or {}
        out = {a for a in (patch.get('addresses') or {}).values() if a}
        for record_id in (patch.get('records') or [card['record_id']]):
            row = self._follow(c, org, record_id)
            if row is not None:
                out |= set(self.addresses(c, org, row['id']))
        return sorted(out)

    @staticmethod
    def _option(card_id, address):
        return hashlib.sha256(f'{card_id}\n{address}'.encode()).hexdigest()[:12]

    def _chosen(self, c, org, card, option):
        for address in self._options(c, org, card):
            if self._option(card['id'], address) == str(option):
                return address
        raise PersonError('Choose the primary address from the card')

    def _waiting_for(self, c, org, subject):
        """Open merge cards that name this account — on its own Person, or on the Person of someone
        who said this account's address is theirs too (``offer_for_address``)."""
        p = s.change_proposals
        rows = c.execute(select(p).where(p.c.org_id == org, p.c.status == OFFERED)).mappings().all()
        return sorted((dict(r) for r in rows if (r['patch'] or {}).get('kind') == MERGE_KIND
                       and subject in ((r['patch'] or {}).get('accounts') or [])),
                      key=lambda r: str((r['patch'] or {}).get('created_at') or ''))

    def primary(self, c, org, row, fallback=''):
        """Where invites and nudges go: the address the person chose on their last confirmed merge
        card, else the record's ``Email`` when it is one of the Person's addresses, else ``fallback``
        (the address the account signed in with)."""
        mine = set(self.addresses(c, org, row['id']))
        for card in reversed(self._cards(c, org, row['id'], (CONFIRMED,))):
            chosen = str((card['patch'] or {}).get('primary') or '')
            if chosen and chosen in mine:
                return chosen
        email = str((row.get('fields') or {}).get('Email') or '').strip().lower()
        if email and email in mine:
            return email
        return str(fallback or '').strip().lower() or None

    # ── linking (sign-in) ────────────────────────────────────────────────────────────────────
    def link(self, c, org, row, subject, email=''):
        """Record that ``subject`` resolved to this Person, and offer the merge card if it is now
        one of several accounts the person has not confirmed together."""
        subject = str(subject)
        if row.get('account_id') != subject:
            identifier = ACCOUNT + subject
            held = self._held(c, org, identifier)
            if held is None:
                c.execute(s.match_ids.insert().values(org_id=org, identifier=identifier, record_id=row['id']))
            elif held != row['id']:
                # the verified address decides (I10): the link follows the address to this Person
                c.execute(update(s.match_ids).where(s.match_ids.c.org_id == org,
                                                    s.match_ids.c.identifier == identifier).values(record_id=row['id']))
        self.offer(c, org, row, {subject: str(email or '').strip().lower()})

    def offer(self, c, org, row, seen=None):
        """The open merge card for this Person, created or refreshed; None when there is nothing for
        the person to confirm (one account, or every linked account already confirmed together)."""
        accounts = sorted(self.linked(c, org, row))
        seen = {k: v for k, v in (seen or {}).items() if v}
        cards = self._cards(c, org, row['id'])
        open_ = [k for k in cards if k['status'] == OFFERED]
        if open_:
            card = open_[-1]
            patch = dict(card['patch'] or {})
            changed = False
            if sorted(set(patch.get('accounts') or []) | set(accounts)) != sorted(patch.get('accounts') or []):
                patch['accounts'] = sorted(set(patch.get('accounts') or []) | set(accounts))
                changed = True
            addresses = dict(patch.get('addresses') or {})
            for who, address in seen.items():
                if who in patch['accounts'] and addresses.get(who) != address:
                    addresses[who] = address
                    changed = True
            if changed:
                patch['addresses'] = addresses
                c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id == org,
                                                           s.change_proposals.c.id == card['id']).values(patch=patch))
                card['patch'] = patch
            return card
        if len(accounts) < 2:
            return None
        for card in cards:
            if card['status'] == CONFIRMED and set(accounts) <= set((card['patch'] or {}).get('accounts') or []):
                return None
        return self._insert(c, org, row, accounts, seen, records=[row['id']], author=SYSTEM)

    def _insert(self, c, org, row, accounts, addresses, *, records, author):
        patch = {'kind': MERGE_KIND, 'fields': {}, 'narrative': None, 'accounts': sorted(accounts),
                 'addresses': {k: v for k, v in (addresses or {}).items() if k in accounts and v},
                 'records': list(records), 'confirmed': {}, 'primary': None, 'created_at': _now()}
        card = {'org_id': org, 'id': str(uuid4()), 'record_id': row['id'], 'base_revision': row['revision'],
                'author_id': author, 'patch': patch, 'evidence': [], 'reason': _MERGE_REASON, 'status': OFFERED}
        c.execute(s.change_proposals.insert().values(**card))
        return card

    # ── the person's own view and acts ───────────────────────────────────────────────────────
    def _person(self, caller):
        caller = as_caller(caller)
        if caller.delegated or caller.internal or not caller.subject:
            # an agent never confirms who a person is, and never reads their addresses for them
            raise PersonRefused()
        return caller

    def _card_view(self, c, org, card, caller):
        patch = card['patch'] or {}
        accounts = list(patch.get('accounts') or [])
        addresses = dict(patch.get('addresses') or {})
        own = addresses.get(caller.subject) or caller.email
        options = self._options(c, org, card)
        confirmed = dict(patch.get('confirmed') or {})
        return {'id': card['id'], 'status': card['status'], 'accounts': len(accounts),
                'confirmed_by_you': caller.subject in confirmed,
                'waiting_on': len([a for a in accounts if a not in confirmed]),
                # your own address in full, every other one masked: a wrongly linked account learns
                # nothing it did not already know
                'addresses': [{'option': self._option(card['id'], a), 'address': a if a == own else mask(a),
                               'yours': a == own} for a in options],
                'primary': (self._option(card['id'], patch['primary']) if patch.get('primary') in options else None)}

    def me(self, org, caller):
        """The signed-in person's own Person: how many accounts answer for it, their addresses (theirs
        in full), the primary, and the merge card waiting for them, if any."""
        caller = self._person(caller)
        with self.engine.begin() as c:
            row = self.resolve(c, org, caller.subject, caller.proven_email)
            if row is None:
                return {'person': None, 'accounts': 1, 'addresses': [caller.email] if caller.email else [],
                        'primary': caller.email or None, 'merge': None}
            card = self.offer(c, org, row, {caller.subject: caller.proven_email})
            accounts = self.linked(c, org, row)
            if caller.subject not in accounts:
                accounts.append(caller.subject)
            mine = self.addresses(c, org, row['id'])
            primary = self.primary(c, org, row, caller.proven_email)
            pending = self._waiting_for(c, org, caller.subject)
            card = pending[-1] if pending else None
            return {'person': row['id'], 'accounts': len(accounts),
                    'addresses': [a if a == caller.email else mask(a) for a in mine],
                    'primary': primary if primary == caller.email else (mask(primary) if primary else None),
                    'merge': self._card_view(c, org, card, caller) if card else None}

    def _open_card(self, c, org, caller, card_id):
        p = s.change_proposals
        card = c.execute(select(p).where(p.c.org_id == org, p.c.id == str(card_id or ''))
                         .with_for_update()).mappings().first()
        if card is None or (card['patch'] or {}).get('kind') != MERGE_KIND \
                or caller.subject not in ((card['patch'] or {}).get('accounts') or []):
            # not yours (or not a merge card): the answer an unknown id gets
            raise PersonNotFound('No merge card for you with that id')
        return dict(card)

    def confirm(self, org, caller, card_id, primary=None):
        """This account confirms the card. The merge applies once every account on it has confirmed;
        ``primary`` (an option from the card) is the person's choice of address."""
        caller = self._person(caller)
        with self.engine.begin() as c:
            card = self._open_card(c, org, caller, card_id)
            if card['status'] != OFFERED:
                raise PersonError('This merge card was already decided')
            patch = dict(card['patch'] or {})
            if primary not in (None, ''):
                patch['primary'] = self._chosen(c, org, card, primary)
            confirmed = dict(patch.get('confirmed') or {})
            confirmed[caller.subject] = _now()
            patch['confirmed'] = confirmed
            addresses = dict(patch.get('addresses') or {})
            if caller.proven_email:
                addresses[caller.subject] = caller.proven_email
            patch['addresses'] = addresses
            done = all(a in confirmed for a in patch.get('accounts') or [])
            status = OFFERED
            if done:
                status = CONFIRMED
                if not patch.get('primary'):
                    options = self._options(c, org, {**card, 'patch': patch})
                    patch['primary'] = caller.proven_email or (options[0] if options else None)
                self._apply(c, org, card, patch, caller.subject)
            c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id == org,
                                                       s.change_proposals.c.id == card['id'])
                      .values(patch=patch, status=status))
            card.update(patch=patch, status=status)
            return {'status': 'merged' if done else 'confirmed', 'merge': self._card_view(c, org, card, caller)}

    def decline(self, org, caller, card_id):
        """"These are not all me": the card closes and the Person keeps only its own account. Every
        other account goes back to a record of its own, so grants to this Person stop reaching it."""
        caller = self._person(caller)
        with self.engine.begin() as c:
            card = self._open_card(c, org, caller, card_id)
            if card['status'] != OFFERED:
                raise PersonError('This merge card was already decided')
            patch = dict(card['patch'] or {})
            row = self._follow(c, org, card['record_id'])
            split = []
            if row is not None and len(patch.get('records') or []) <= 1:
                keep = row.get('account_id') or caller.subject
                for who in self.linked(c, org, row):
                    if who != keep:
                        self._detach(c, org, row, who, (patch.get('addresses') or {}).get(who) or '', caller.subject)
                        split.append(who)
            c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id == org,
                                                       s.change_proposals.c.id == card['id']).values(status=DECLINED))
            return {'status': 'declined', 'split': len(split)}

    def set_primary(self, org, caller, option):
        """Change the primary address later, on the person's last confirmed card."""
        caller = self._person(caller)
        with self.engine.begin() as c:
            row = self.resolve(c, org, caller.subject, caller.proven_email)
            cards = self._cards(c, org, row['id'], (CONFIRMED,)) if row else []
            if not cards or caller.subject not in ((cards[-1]['patch'] or {}).get('accounts') or []):
                raise PersonError('Confirm your accounts first; the primary address is chosen on the merge card')
            card = cards[-1]
            patch = dict(card['patch'] or {})
            patch['primary'] = self._chosen(c, org, card, option)
            c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id == org,
                                                       s.change_proposals.c.id == card['id']).values(patch=patch))
            return {'status': 'primary_set'}

    def offer_for_address(self, org, caller, address):
        """The person says another address is theirs too. A card opens between their Person and the
        Person that address resolves to; it completes only when that address's account confirms, so
        naming someone else's address grants nothing. The answer is the same whether or not the
        address has an account here."""
        caller = self._person(caller)
        address = str(address or '').strip().lower()
        if '@' not in address or len(address) > 300:
            raise PersonError('An address is name@domain')
        answer = {'status': 'offered', 'instruction': 'Sign in with that address and confirm the card there.'}
        with self.engine.begin() as c:
            mine = self.resolve(c, org, caller.subject, caller.proven_email)
            other = self.resolve(c, org, '', address)
            if mine is None or other is None or other['id'] == mine['id']:
                return answer
            theirs = self.linked(c, org, other)
            if not theirs:
                return answer           # an address nobody signs in with: nothing to confirm from
            accounts = sorted(set(self.linked(c, org, mine)) | {caller.subject} | set(theirs))
            for card in self._cards(c, org, mine['id'], (OFFERED,)):
                if set(accounts) <= set((card['patch'] or {}).get('accounts') or []):
                    return answer
            card = self._insert(c, org, mine, accounts, {caller.subject: caller.proven_email},
                                records=[mine['id'], other['id']], author=caller.subject)
            patch = dict(card['patch'])
            patch['confirmed'] = {caller.subject: _now()}
            c.execute(update(s.change_proposals).where(s.change_proposals.c.org_id == org,
                                                       s.change_proposals.c.id == card['id']).values(patch=patch))
            return answer

    # ── applying and undoing ─────────────────────────────────────────────────────────────────
    def _apply(self, c, org, card, patch, actor):
        """Every account confirmed: the other Persons on the card merge into this one (their
        identifiers, attached accounts and bindings move to it; their ids answer it from now on)."""
        winner = self._follow(c, org, card['record_id'])
        if winner is None:
            raise PersonNotFound('The person on this card no longer exists')
        for other_id in (patch.get('records') or [])[1:]:
            loser = self._follow(c, org, other_id)
            if loser is None or loser['id'] == winner['id']:
                continue
            after = {**loser, 'merged_into': winner['id'], 'revision': loser['revision'] + 1, 'account_id': None}
            c.execute(update(s.records).where(s.records.c.org_id == org, s.records.c.id == loser['id'])
                      .values(merged_into=winner['id'], revision=after['revision'], account_id=None))
            c.execute(update(s.match_ids).where(s.match_ids.c.org_id == org, s.match_ids.c.record_id == loser['id'])
                      .values(record_id=winner['id']))
            if loser.get('account_id') and loser['account_id'] != winner.get('account_id'):
                identifier = ACCOUNT + loser['account_id']
                if self._held(c, org, identifier) is None:
                    c.execute(s.match_ids.insert().values(org_id=org, identifier=identifier, record_id=winner['id']))
            b = s.workspace_bindings
            have = {w for (w,) in c.execute(select(b.c.workspace).where(b.c.org_id == org, b.c.record_id == winner['id'])).all()}
            for (workspace,) in c.execute(select(b.c.workspace).where(b.c.org_id == org, b.c.record_id == loser['id'])).all():
                if workspace not in have:
                    c.execute(b.insert().values(org_id=org, workspace=workspace, record_id=winner['id'],
                                                created_by=actor, created_at=_now()))
            c.execute(delete(b).where(b.c.org_id == org, b.c.record_id == loser['id']))
            # one profile per human: the merged-away record's claim ends; the survivor's stands
            c.execute(update(s.profiles).where(s.profiles.c.org_id == org, s.profiles.c.record_id == loser['id'])
                      .values(claimed_by=None, published=False, handle=None, updated_at=_now()))
            self.store._revision(c, org, actor, loser, after, _MERGE_REASON, [{'merged_into': winner['id'], 'card': card['id']}])

    def _detach(self, c, org, row, who, address, actor):
        """Give account ``who`` a Person of its own again, with its address."""
        own = c.execute(select(s.records.c.id).where(s.records.c.org_id == org,
                                                     s.records.c.account_id == who)).scalar_one_or_none()
        target = self._follow(c, org, own) if own else None
        if target is None:
            record_id = str(uuid5(NAMESPACE_URL, json.dumps(['crm-account', org, who])))
            existing = c.execute(select(s.records.c.id).where(s.records.c.org_id == org,
                                                              s.records.c.id == record_id)).scalar_one_or_none()
            if existing is None:
                fields = {'Name': address or who}
                if address:
                    fields['Email'] = address
                new = dict(org_id=org, id=record_id, object_type='Person', owner_id=who, revision=1, fields=fields,
                           narrative='', quarantined=False, deleted=False, account_id=who, merged_into=None)
                c.execute(s.records.insert().values(**new))
                self.store._revision(c, org, actor, None, new, 'Split from a person on their own merge card (D35)', [])
            target = {'id': record_id}
        for identifier in [ACCOUNT + who] + ([EMAIL + address] if address else []):
            if self._held(c, org, identifier) == row['id']:
                c.execute(update(s.match_ids).where(s.match_ids.c.org_id == org, s.match_ids.c.identifier == identifier)
                          .values(record_id=target['id']))

    # ── for the platform (agent-api through /internal/persons, crm's own card membership) ────
    def group(self, org, accounts):
        """``accounts``: ``[{subject, email, email_verified}]``. For each, the Person it resolves to
        and every account linked to that Person (accounts given together that resolve to one Person
        count once), and the primary address. Accounts with no Person answer as a Person of one."""
        out = {}
        with self.engine.connect() as c:
            for a in list(accounts or [])[:MAX_ACCOUNTS]:
                subject = str((a or {}).get('subject') or '').strip()
                email = str((a or {}).get('email') or '').strip().lower()
                if not subject:
                    continue
                # an address finds a Person only when the producer vouches a sign-in proved it;
                # otherwise the account alone does, and the address is only its fallback primary
                row = self.resolve(c, org, subject, email if (a or {}).get('email_verified') is True else '')
                if row is None:
                    out[subject] = {'person': None, 'accounts': [subject], 'confirmed': [subject],
                                    'primary': email or None, 'merged': False}
                    continue
                linked = self.linked(c, org, row)
                if subject not in linked:
                    linked.append(subject)
                merged = any(set(linked) <= set((k['patch'] or {}).get('accounts') or [])
                             for k in self._cards(c, org, row['id'], (CONFIRMED,)))
                out[subject] = {'person': row['id'], 'accounts': linked,
                                'confirmed': self.confirmed(c, org, row, subject),
                                'primary': self.primary(c, org, row, email), 'merged': merged}
            # accounts given together that resolve to one Person: each answers the union
            by_person = {}
            for subject, v in out.items():
                if v['person']:
                    by_person.setdefault(v['person'], []).append(subject)
            for person, subjects in by_person.items():
                union = []
                for subject in subjects:
                    for who in out[subject]['accounts']:
                        if who not in union:
                            union.append(who)
                for subject in subjects:
                    out[subject]['accounts'] = list(union)
        return out

    def linked_accounts(self, c, org, subject):
        """The accounts a grant to ``subject`` reaches: ``subject`` and the accounts the person
        CONFIRMED together with it (``confirmed``); never an unconfirmed link."""
        row = self.resolve(c, org, subject)
        if row is None:
            return [str(subject)]
        return self.confirmed(c, org, row, subject)
