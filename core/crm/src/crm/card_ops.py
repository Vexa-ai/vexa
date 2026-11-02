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

**Who writes which field** is the organisation's ``_global/POLICIES.md`` (``card_field_writers.<Field>``,
``FIELD_WRITERS_KEY``), checked on every write path in ``normalise_fields``, an administrator's too.
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
from . import readings as rd
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
            'Kind', 'NextActor', 'Requester', 'NextCheckAt', 'Chain', 'Origin',
            # 1 Nov 2026: the typed link to what the card tracks (readings.py).
            'Source')
#: Written only by a gate decision (D12), never by an edit or a proposal.
GATE_ONLY = ('Handoff', 'HandoffSince', 'Answer', 'Evidence', 'Receipt')
#: Written only by a sensor reading (``write_reading``, ``/internal/records/reading``), never by an
#: edit, a proposal or a gate: what the system read at the card's Source.
SENSOR_ONLY = ('Reading',)
#: Written only by the stage rule (``set_stage``, ``/internal/cards/stage``): the facts that set a
#: software card's stage, and the correction a re-check made.
RULE_ONLY = ('StageEvidence',)
#: Written only by the claim verbs (``claim`` / ``release``, founder 1 Nov 2026 «A»: a claim is a card fact,
#: not a Flows row): who works on the card now, and until when. Expiry is checked on read.
CLAIM_ONLY = ('Holder', 'HoldUntil')
#: A claim's lease: default and longest.
HOLD_DEFAULT_S, HOLD_MAX_S = 2 * 3600, 24 * 3600
#: The actor every stage-rule revision is recorded under.
STAGE_RULE_ACTOR = 'rule:card-stage'
STAGE_MODES = ('event', 'recheck')
#: WHO WRITES WHICH CARD FIELD (founder, 1 Nov 2026: cards are made through the API, their stage moves
#: only by the rule from facts, nothing is written over a card). ``_global/POLICIES.md`` front matter:
#: ``card_field_writers.<Field>: <writer>, <writer>`` per field, ``card_field_writers.*`` for every field
#: not named. A writer is ``anyone`` (whoever the CRM policy lets write it), ``create`` (whoever makes
#: the card, in the create only: the field is set once), ``rule`` (the stage rule,
#: ``/internal/cards/stage``), ``gate`` (a gate decision, ``decide``) or a person id (their own write,
#: or their acceptance of a proposal). A field with no line is as the CRM policy has it. ``*`` never
#: covers the gate-, sensor- and rule-only fields: they keep their own single writer.
FIELD_WRITERS_KEY = 'card_field_writers.'
FIELD_WRITER_TOKENS = ('anyone', 'create', 'rule', 'gate')
#: Facts that come from reading the card's Source. A stage the rule set with none of them (Idea or
#: Design from «no pull request» alone) rested on an absence, not a fact.
READ_FACTS = ('pr', 'issue', 'checks', 'prod', 'release')
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


STAGE_FROM_FACTS = _refusal(
    'stage_from_facts',
    "A software card's stage is set from facts (its pull request, the prod pin, the release tag, a Flows "
    'claim) by the stage rule only. Nobody moves it by hand. Change the facts instead; do not retry it.')
STAGE_BY_RULE = _refusal(
    'stage_from_facts',
    "A card's stage is set from facts by the stage rule only. Nobody moves it by hand. Change the facts "
    'instead; do not retry it.')
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


def field_locked(field, writers):
    return _refusal('field_writer',
                    f"A card's {field} is written by {', '.join(writers) or 'nobody'} only. Do not retry it "
                    'another way.', field=field, writers=list(writers))


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


def _merge_unproven(fields) -> bool:
    """A Done software card whose stage fact is a merged pull request not shown to be merged into the default branch."""
    if fields.get('State') != 'Done':
        return False
    ev = fields.get('StageEvidence')
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            return False
    for f in (ev or {}).get('facts') or [] if isinstance(ev, dict) else []:
        v = f.get('value') if isinstance(f, dict) else None
        if f.get('fact') == 'pr' and isinstance(v, dict) and v.get('state') == 'merged':
            return not v.get('default_branch') or v.get('base') != v.get('default_branch')
    return False


class PolicyText:
    """``_global/POLICIES.md`` as text, through the desk's policy door (``policies.Policies.fetch``),
    kept 30 seconds; None when there is no door or it has never answered (the defaults then apply). A
    door that stops answering keeps the last policy it gave: the field writers are a lock, and a lock
    does not open because a read failed."""

    def __init__(self, policies, ttl=30.0, now=time.monotonic):
        self.policies, self.ttl, self.now = policies, ttl, now
        self._at, self._text, self._lock = None, None, threading.Lock()

    def __call__(self):
        with self._lock:
            if self._at is None or self.now() - self._at >= self.ttl:
                try:
                    text = self.policies.fetch() if self.policies is not None else None
                except Exception:  # noqa: BLE001 — a policy we cannot read keeps the last one read
                    text = None
                if text is not None:
                    self._text = text
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

    def field_writers(self, field):
        """The writers ``_global/POLICIES.md`` names for a card field (``FIELD_WRITERS_KEY``), or None
        when it names none: the field is then as the CRM policy has it."""
        attrs = self._attrs()
        line = attrs.get(FIELD_WRITERS_KEY + str(field).lower())
        if line is None and field not in (*GATE_ONLY, *SENSOR_ONLY, *RULE_ONLY):
            line = attrs.get(FIELD_WRITERS_KEY + '*')
        if line is None:
            return None
        return tuple(w for w in (x.strip() for x in str(line).split(',')) if w)

    def _check_writers(self, asked, prior, creating, writer, gate_write, rule_write, first_stage):
        """Refuse a field the policy does not let this writer write (``FIELD_WRITERS_KEY``). A value the
        card already holds is no write; neither is a new card's first stage."""
        for field in sorted(asked):
            writers = self.field_writers(field)
            if writers is None or 'anyone' in writers:
                continue
            value = asked[field]
            if (value == first_stage if field == 'State' and creating else
                    not creating and value == prior.get(field)):
                continue
            if (rule_write and 'rule' in writers) or (gate_write and 'gate' in writers) \
                    or (creating and 'create' in writers) or (writer is not None and str(writer) in writers):
                continue
            raise Refused(STAGE_BY_RULE if field == 'State' and 'rule' in writers else field_locked(field, writers))

    def _ttl(self, limits=None):
        return timedelta(days=(limits or self.limits())['card_proposal_ttl_days'])

    # ── the write-path hook (store._card_fields) ───────────────────────────────────────────────
    #: Set while a gate decision writes (``decide``): the only writer of a kind card's stage and of
    #: the gate-only fields. Thread-local, because one Cards object serves concurrent requests.
    _gate = threading.local()
    #: Set while a sensor reading writes (``write_reading``): the only writer of ``SENSOR_ONLY``.
    _reading = threading.local()
    #: Set while a claim verb writes (``claim`` / ``release``): the only writer of ``CLAIM_ONLY``.
    _claiming = threading.local()
    #: Set while the stage rule writes (``set_stage``): the only writer of a software card's stage
    #: and of ``RULE_ONLY``.
    _rule = threading.local()

    def normalise_fields(self, object_type, fields, before, actor=None, writer=None):
        """A card's fields as every write path stores them. ``actor`` is the person whose write is
        being APPLIED (``store._edit``: an edit, or the reviewer accepting a proposal; ``store.create``:
        the person making the record); None while a proposal is only filed, which changes nothing until
        a person accepts it, and on the paths of this class that set the requester themselves.
        ``writer`` is whom the field writers (``FIELD_WRITERS_KEY``) are checked against when it is not
        ``actor`` (a card made by ``_insert``); a change only being filed as a proposal is checked when a
        person accepts it."""
        fields = dict(fields or {})
        asked = set(fields)
        writer = actor if writer is None else writer
        prior = (before or {}) if isinstance(before, dict) else {}
        gate_write = bool(getattr(self._gate, 'on', False))
        if 'Face' in fields:
            fields['Face'] = card_face.sanitize(fields['Face'])
        if fields.get('About'):
            check_about(fields['About'])
        if not gate_write and set(fields) & set(GATE_ONLY):
            raise CRMError('A card\'s handoff, answer, evidence and receipt change only through its gates (decide)')
        reading_write = bool(getattr(self._reading, 'on', False))
        if set(fields) & set(SENSOR_ONLY) and not reading_write:
            raise CRMError("A card's Reading is what the system read at its Source; only a sensor reading writes it")
        if reading_write and set(fields) - set(SENSOR_ONLY):
            raise CRMError('A sensor reading writes the Reading field only')
        claim_write = bool(getattr(self._claiming, 'on', False))
        if set(fields) & set(CLAIM_ONLY) and not claim_write:
            raise CRMError("Who holds a card is written by claim and release only")
        if claim_write and set(fields) - set(CLAIM_ONLY):
            raise CRMError('A claim writes Holder and HoldUntil only')
        rule_write = bool(getattr(self._rule, 'on', False))
        if set(fields) & set(RULE_ONLY) and not rule_write:
            raise CRMError("A software card's stage evidence is written by the stage rule only")
        if rule_write and set(fields) - {'State', *RULE_ONLY}:
            raise CRMError('The stage rule writes State and StageEvidence only')
        if 'Source' in fields:
            fields['Source'] = rd.check_source(fields['Source'])
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
            if tpl.moved_by_rule(template):
                if not rule_write and fields['State'] != (prior.get('State') if before is not None
                                                          else life['stages'][0]):
                    raise Refused(STAGE_FROM_FACTS)
            elif template is not None and before is not None and not gate_write \
                    and fields['State'] != prior.get('State'):
                raise CRMError('A card of this kind moves only through its gates (decide), by the person whose move it is')
        elif before is None:
            fields['State'] = life['stages'][0]
        elif tpl.moved_by_rule(template) and prior.get('State') not in states:
            # a plain card becoming a software card starts at the first stage; the rule moves it on
            fields['State'] = life['stages'][0]
        if rule_write and not tpl.moved_by_rule(template) and 'rule' not in (self.field_writers('State') or ()):
            raise CRMError('The stage rule moves software cards only')
        for key in ('NextActor', 'Requester'):
            if fields.get(key) is not None:
                value = str(fields[key]).strip()
                if not _PERSON.fullmatch(value):
                    raise CRMError(f'{key} is a person id')
                fields[key] = value
        if fields.get('Origin') is not None and not _ORIGIN.fullmatch(str(fields['Origin'])):
            raise CRMError('Origin is session:<id>, meeting:<id> or message:<session>/<message id>')
        if 'Chain' in fields:
            # one stored edge (founder, 1 Nov 2026): a card says what it waits on; what it blocks is computed
            chain = fields['Chain'] if isinstance(fields['Chain'], dict) else {}
            if chain.get('blocks'):
                raise CRMError('Chain stores blocked_by only: write the wait on the waiting card; blocks is computed')
            fields['Chain'] = {'blocked_by': [str(x) for x in (chain.get('blocked_by') or [])
                                              if _ID.fullmatch(str(x))][:20]}
        creating = before is None
        if creating or writer is not None or gate_write or rule_write:
            self._check_writers({k: fields[k] for k in asked if k in fields}, prior, creating, writer,
                                gate_write, rule_write, life['stages'][0])
        if tpl.moved_by_rule(template):
            # no gates and no next actor: whose move it is follows from the facts, not from a field
            pass
        elif template is not None:
            merged = {**prior, **fields}
            if before is None:
                if not merged.get('NextActor'):
                    raise CRMError('A card of a kind names its next actor (NextActor: a person id)')
                if actor is not None:
                    self._made_by(fields, str(actor))
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
    def _made_by(fields, actor):
        """A card of a kind made through the generic record create waits on the person who makes it,
        as one made by ``create`` does: a writer does not make a card someone else is waiting on."""
        if fields.get('Requester') is None:
            fields['Requester'] = actor
        elif fields['Requester'] != actor:
            raise Forbidden('A card of a kind waits on the person who makes it; Requester is you')

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
            # the typed link and what the system read there (readings.py § RENDERER CONTRACT)
            'source': f.get('Source') if ('*' in readable or 'Source' in readable) else None,
            'reading': f.get('Reading') if ('*' in readable or 'Reading' in readable) else None,
            # a software card: the facts that set its stage, and a re-check's correction (set_stage)
            'stage_evidence': f.get('StageEvidence') if ('*' in readable or 'StageEvidence' in readable) else None,
            'workspaces': bindings, 'parent': parent,
            'children': {'done': sum(1 for i in items if i['done']), 'total': len(items), 'items': items[:20]},
            'members': [] if cross_org else self._members(c, org, row['id']),
            'face': card_face.resolve(face_source, f, readable, formats),
            'movement': movement, 'stalled': stalled, 'new': new,
            'can': {'edit': can_write, 'transition': can_write and not tpl.moved_by_rule(template),
                    'review': can_write, 'members': can_write},
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

    def hold_of(self, raw):
        """Who holds the card now: {holder, until} while ``HoldUntil`` is in the future, else None (a lapsed
        claim reads as no claim; nothing has to clear it)."""
        holder, until = (raw or {}).get('Holder'), (raw or {}).get('HoldUntil')
        if not holder or not until:
            return None
        try:
            if _at(until) <= self.clock():
                return None
        except (TypeError, ValueError):
            return None
        return {'holder': holder, 'until': until}

    def claim(self, org, actor, card, holder, lease_s=HOLD_DEFAULT_S, reason=''):
        """A worker takes a card: ``Holder`` = who (``<role>@<host>:<dir>``), ``HoldUntil`` = now + lease (at most
        24 h). The same holder claiming again renews. Refused while someone else's claim is live. One revision,
        recorded under the caller, with the reason."""
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        holder = ' '.join(str(holder or '').split())
        if not holder or len(holder) > 200:
            raise CRMError('A claim names its holder: <role>@<host>:<dir>, at most 200 characters')
        lease = max(60, min(int(lease_s or HOLD_DEFAULT_S), HOLD_MAX_S))
        with self.store.begin() as c:
            row = self._card_row(c, org, card)
            live = self.hold_of(row['fields'])
            if live and live['holder'] != holder:
                raise Conflict(f"Held by {live['holder']} until {live['until']}")
            until = (self.clock() + timedelta(seconds=lease)).isoformat()
            why = f'Claimed by {holder}' + (f': {" ".join(str(reason).split())[:200]}' if reason else '')
            self._claiming.on = True
            try:
                self._apply(c, org, caller.subject, row, {'Holder': holder, 'HoldUntil': until}, why, [], None)
            finally:
                self._claiming.on = False
            return {'claimed': True, 'card': row['id'], 'holder': holder, 'until': until, 'renewed': bool(live)}

    def release(self, org, actor, card, receipt='', holder=None):
        """The holder gives the card back with a receipt (what was done). Refused when nobody holds it, or when
        ``holder`` is named and someone else holds it."""
        caller = as_caller(actor)
        if caller.delegated or caller.internal or not caller.subject:
            raise Refused(PROPOSE_CARD)
        with self.store.begin() as c:
            row = self._card_row(c, org, card)
            live = self.hold_of(row['fields'])
            if not live:
                raise Conflict('Nobody holds this card')
            if holder and ' '.join(str(holder).split()) != live['holder']:
                raise Conflict(f"Held by {live['holder']}, not {holder}")
            why = ' '.join(str(receipt or '').split())[:300] or f"Released by {live['holder']}"
            self._claiming.on = True
            try:
                self._apply(c, org, caller.subject, row, {'Holder': None, 'HoldUntil': None}, why, [], None)
            finally:
                self._claiming.on = False
            return {'released': True, 'card': row['id'], 'holder': live['holder']}

    def _blocks(self, c, org, record_id):
        """The cards that wait on this one: computed from their ``Chain.blocked_by``, never stored."""
        out = []
        for rid, fields in c.execute(select(s.records.c.id, s.records.c.fields).where(
                s.records.c.org_id == org, s.records.c.deleted == False)):  # noqa: E712
            ch = (fields or {}).get('Chain') if isinstance(fields, dict) else None
            if isinstance(ch, dict) and record_id in (ch.get('blocked_by') or []):
                out.append(rid)
        return sorted(out)

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
            'chain': {'blocks': self._blocks(c, org, row['id']), 'blocked_by': list(chain.get('blocked_by') or [])},
            'hold': self.hold_of(raw),
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

    #: How many cards one plan may hold (a guard against a runaway graph, not a business rule).
    PLAN_MAX = 500

    def plan(self, org, actor, destination):
        """A plan (founder, 1 Nov 2026): a DESTINATION card (``Destination`` set by the founder) and every card it
        waits on, transitively through ``Chain.blocked_by``. Not stored: computed on every read. Each node carries
        its revision, so a reader can trace exactly what it saw. A link to a card that does not exist (deleted, or
        never written) is a node with ``missing: true``. A card that is not a destination is refused."""
        caller = as_caller(actor)
        with self.engine.connect() as c:
            policy = self.store._policy(c, org, caller)
            head = self._card_row(c, org, destination)
            if not (head['fields'] or {}).get('Destination'):
                raise CRMError('A plan starts at a destination card (Destination is set by the founder)')

            def node(row):
                f = self.store._visible(policy, row)['fields']
                return {'id': row['id'], 'title': f.get('Title'), 'state': f.get('State'), 'kind': f.get('Kind'),
                        'source': f.get('Source'), 'revision': row['revision']}

            nodes, edges, seen, todo = [], [], {head['id']}, [head]
            while todo:
                row = todo.pop(0)
                chain = (row['fields'] or {}).get('Chain')
                for up in sorted((chain or {}).get('blocked_by') or []) if isinstance(chain, dict) else []:
                    edges.append({'card': row['id'], 'waits_on': up})
                    if up in seen:
                        continue
                    seen.add(up)
                    if len(seen) > self.PLAN_MAX:
                        raise CRMError(f'A plan holds at most {self.PLAN_MAX} cards')
                    try:
                        other = self._card_row(c, org, up)
                    except NotFound:
                        nodes.append({'id': up, 'missing': True})
                        continue
                    nodes.append(node(other))
                    todo.append(other)
            return {'plan': {'destination': node(head), 'nodes': nodes, 'edges': edges,
                             'nodes_total': len(nodes), 'edges_total': len(edges)}}

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
        fields = self.normalise_fields(object_type, fields, None, writer=owner)
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

    # ── sensor readings (readings.py; flows' ``write_reading_to_crm``) ─────────────────────────────
    def by_source(self, org, link, limit=50):
        """The cards that track ``link``: their typed ``Source`` names the same subject
        (``readings.subject``), or, for a card with no ``Source``, its ``Summary`` carries the legacy
        «GitHub: owner/repo#N». Internal tier only: it answers ids, never a field."""
        mail = rd.email_of(link)
        if mail:                       # a person (and their company's domain) by match identifier (L2.7)
            with self.store.engine.connect() as c:
                m = s.match_ids
                ids = [rid for (rid,) in c.execute(select(m.c.record_id).where(
                    m.c.org_id == org, m.c.identifier.in_([f'email:{mail}', f"domain:{mail.split('@', 1)[1]}"]))
                    .order_by(m.c.record_id)).all()]
            return {'subject': f'email:{mail}', 'cards': sorted(set(ids))[:limit]}
        want = rd.subject(link)
        if not want:
            raise CRMError('Not a link a reading can name')
        with self.store.engine.connect() as c:
            types = [n for n, d in c.execute(select(s.object_types.c.name, s.object_types.c.definition)
                                             .where(s.object_types.c.org_id == org)).all()
                     if isinstance(d, dict) and d.get('x-card')]
            r = s.records
            rows = c.execute(select(r.c.id, r.c.fields).where(
                r.c.org_id == org, r.c.object_type.in_(types), r.c.deleted.is_(False),
                r.c.merged_into.is_(None)).order_by(r.c.id)).all()
        ids = []
        for rid, fields in rows:
            fields = fields or {}
            have = rd.subject(fields.get('Source')) if fields.get('Source') else rd.summary_subject(fields.get('Summary'))
            if have == want:
                ids.append(rid)
                if len(ids) >= limit:
                    break
        return {'subject': want, 'cards': ids}

    def write_reading(self, org, record, source, fields, evidence):
        """Write one sensor reading onto a card as its ``Reading``: the declared fields only, with
        the reading's evidence. Idempotent by (record, source, read_at): the same reading again is
        ``unchanged``; one older than the card's is ``stale``; one whose values equal the card's is
        ``unchanged`` (no revision). Otherwise it replaces the earlier reading as one revision by
        ``reading:<source>``, whose evidence is the reading's. No other field is touched."""
        values = rd.normalise_fields(fields)
        ev = rd.normalise_evidence(source, evidence)
        with self.store.begin() as c:
            row = self.store._row(c, org, str(record or ''))
            definition = self.store._definition(c, org, row['object_type'])
            is_card = bool(definition.get('x-card'))
            if not is_card and 'Reading' not in (definition.get('properties') or {}):
                raise NotFound('This record keeps no readings')
            raw = row['fields'] or {}
            reading = raw.get('Reading') if isinstance(raw.get('Reading'), dict) else {}
            sources = dict(reading.get('sources') or {})
            if reading.get('fields') is not None and not sources:
                # a reading written before readings were kept per source: it is its source's entry
                old = reading.get('evidence') or {}
                sources[str(old.get('source') or ev['source'])] = {'fields': reading['fields'], 'evidence': old}
            prior = sources.get(ev['source'])
            prior_ev = (prior or {}).get('evidence') or {}
            base = {'record': row['id'], 'revision': row['revision'], 'source': ev['source'], 'read_at': ev['read_at']}
            if prior is not None:
                if prior_ev.get('read_at') == ev['read_at']:
                    return {**base, 'status': 'unchanged'}
                if float(prior_ev.get('read_at') or 0) > ev['read_at']:
                    return {**base, 'status': 'stale'}
                if prior.get('fields') == values:
                    return {**base, 'status': 'unchanged'}
            if prior is None and len(sources) >= rd.SOURCES_MAX:
                raise CRMError(f'A card keeps the readings of at most {rd.SOURCES_MAX} sources')
            sources[ev['source']] = {'fields': values, 'evidence': ev}
            merged_fields, latest = rd.merged(sources)
            patch = {'Reading': {'fields': merged_fields, 'evidence': latest, 'sources': sources}}
            self._reading.on = True
            tok = rd.WRITING.set(True)
            try:
                if is_card:
                    self._apply(c, org, 'reading:' + ev['source'], row, patch, f'Read at {ev["source"]}',
                                [{'reading': ev}], None)
                else:                  # a person or a company (L2.7): the same write, no card lifecycle
                    store = self.store
                    after = {**row, 'fields': {**(row['fields'] or {}), **patch}, 'revision': row['revision'] + 1}
                    store._validate(definition, after['fields'])
                    if c.execute(update(s.records).where(s.records.c.org_id == org, s.records.c.id == row['id'],
                                                         s.records.c.revision == row['revision']).values(**after)).rowcount != 1:
                        raise Conflict('The record changed since it was read; read it again')
                    store._revision(c, org, 'reading:' + ev['source'], row, after, f'Read at {ev["source"]}',
                                    [{'reading': ev}])
            finally:
                rd.WRITING.reset(tok)
                self._reading.on = False
            return {**base, 'revision': row['revision'] + 1, 'status': 'applied'}

    # ── the software card's stage (card_templates 'software'; flows' card_stage rule) ─────────────
    def software_cards(self, org, record=None, limit=200):
        """The software cards the stage rule computes: one (``record``) or every open one, each with
        what the rule reads — its State, Source and Reading. Internal tier only; it acts for nobody.
        A Done card whose stage fact is a merge not shown to be into main (no ``default_branch`` read, or another
        ``base``) is read again too: Done is merged into main (founder Q3 «A»; seq61 on 2 Nov read Done from codex)."""
        template = self.template('software')
        with self.store.engine.connect() as c:
            types = [n for n, d in c.execute(select(s.object_types.c.name, s.object_types.c.definition)
                                             .where(s.object_types.c.org_id == org)).all()
                     if isinstance(d, dict) and d.get('x-card')]
            r = s.records
            q = select(r.c.id, r.c.fields, r.c.revision).where(
                r.c.org_id == org, r.c.object_type.in_(types), r.c.deleted.is_(False), r.c.merged_into.is_(None))
            if record:
                q = q.where(r.c.id == str(record))
            rows = c.execute(q.order_by(r.c.id)).all()
        out = []
        for rid, fields, revision in rows:
            fields = fields or {}
            if fields.get('Kind') != 'software':
                continue
            if not record and not tpl.is_open(template, fields.get('State')) and not _merge_unproven(fields):
                continue
            out.append({'id': rid, 'revision': revision, 'state': fields.get('State'),
                        'source': fields.get('Source'), 'reading': fields.get('Reading'),
                        'stage_evidence': fields.get('StageEvidence'), 'hold': self.hold_of(fields)})
            if len(out) >= max(1, min(int(limit or 200), 500)):
                break
        return {'stages': template['stages'], 'side': template['side'], 'cards': out}

    def due_cards(self, org, record=None, limit=500):
        """The cards a due alarm reads: one (``record``, whatever it holds, so a cleared Due or a closed
        card cancels its alarm) or every open card with a ``Due``. Each answers its id, ``due`` (the date we
        told someone), ``owner`` (its ``Waiting``: whose stream the alarm goes to) and ``open``. Internal
        tier only; it acts for nobody."""
        with self.store.engine.connect() as c:
            types = [n for n, d in c.execute(select(s.object_types.c.name, s.object_types.c.definition)
                                             .where(s.object_types.c.org_id == org)).all()
                     if isinstance(d, dict) and d.get('x-card')]
            r = s.records
            q = select(r.c.id, r.c.object_type, r.c.fields, r.c.revision).where(
                r.c.org_id == org, r.c.object_type.in_(types), r.c.deleted.is_(False), r.c.merged_into.is_(None))
            if record:
                q = q.where(r.c.id == str(record))
            rows = c.execute(q.order_by(r.c.id)).all()
        out = []
        for rid, object_type, fields, revision in rows:
            fields = fields or {}
            life = self.lifecycle(object_type, fields.get('Kind')) or {}
            stages, side = list(life.get('stages') or []), list(life.get('side') or [])
            state = fields.get('State')
            is_open = not (state in side or (stages and state == stages[-1]))
            if not record and not (is_open and fields.get('Due')):
                continue
            out.append({'id': rid, 'revision': revision, 'due': fields.get('Due') or None,
                        'owner': fields.get('Waiting') or None, 'state': state, 'open': is_open})
            if len(out) >= max(1, min(int(limit or 500), 2000)):
                break
        return {'cards': out}

    def beat_cards(self, org, record=None, limit=2000):
        """What the card heartbeat reads (founder 2 Nov ~09:30Z): one card (``record``, whatever it holds) or every
        open card, each with its owner (``Waiting``), whether it is open, when it is next checked (``NextCheckAt``,
        else the check interval or its Due when sooner), the check interval, and ``mark``: a digest of its newest
        fact (the latest reading's time), its ``Next`` and its State — a beat with the same mark saw nothing new.
        Internal tier only; it acts for nobody."""
        import hashlib
        with self.store.engine.connect() as c:
            types = [n for n, d in c.execute(select(s.object_types.c.name, s.object_types.c.definition)
                                             .where(s.object_types.c.org_id == org)).all()
                     if isinstance(d, dict) and d.get('x-card')]
            r = s.records
            q = select(r.c.id, r.c.object_type, r.c.fields).where(
                r.c.org_id == org, r.c.object_type.in_(types), r.c.deleted.is_(False), r.c.merged_into.is_(None))
            if record:
                q = q.where(r.c.id == str(record))
            rows = c.execute(q.order_by(r.c.id)).all()
        out, every = [], int(self.check_after().total_seconds())
        for rid, object_type, fields in rows:
            f = fields or {}
            life = self.lifecycle(object_type, f.get('Kind')) or {}
            stages, side = list(life.get('stages') or []), list(life.get('side') or [])
            state = f.get('State')
            is_open = not (state in side or (stages and state == stages[-1]))
            if not record and not is_open:
                continue
            ev = ((f.get('Reading') or {}).get('evidence') or {}) if isinstance(f.get('Reading'), dict) else {}
            mark = hashlib.sha256(json.dumps([ev.get('read_at'), f.get('Next'), state], default=str).encode()).hexdigest()[:16]
            out.append({'id': rid, 'owner': f.get('Waiting') or None, 'open': is_open, 'state': state,
                        'next_check_at': f.get('NextCheckAt') or self.next_check(f.get('Due')), 'every_s': every,
                        'mark': mark})
            if len(out) >= limit:
                break
        return {'cards': out}

    # ── support cards (founder 2 Nov ~09:00Z «суппорт тикеты должны автоматически создавать карточки с SLA») ──
    #: the one actor a support card is made and moved under; the one owner it waits on
    SUPPORT_ACTOR, SUPPORT_WAITING = 'flows:support', 'support'

    def _support_card(self, c, org, source):
        """The support card that tracks ``source`` (its thread's root link): no Kind, Waiting=support."""
        want = rd.subject(source)
        r = s.records
        rows = c.execute(select(r.c.id, r.c.fields, r.c.revision, r.c.object_type, r.c.owner_id).where(
            r.c.org_id == org, r.c.object_type == DEFAULT_TYPE, r.c.deleted.is_(False),
            r.c.merged_into.is_(None))).all()
        for row in rows:
            f = row.fields or {}
            if f.get('Source') and rd.subject(f['Source']) == want and not f.get('Kind') \
                    and f.get('Waiting') == self.SUPPORT_WAITING:
                return {'id': row.id, 'fields': f, 'revision': row.revision, 'object_type': row.object_type,
                        'owner_id': row.owner_id}
        return None

    def _support_sender(self, c, org, sender):
        """The CRM Person a sender's email names, and whether their Company is a customer (Stage «Customer»)."""
        mail = rd.email_of(sender)
        if not mail:
            return None, False
        m, r = s.match_ids, s.records
        ids = [rid for (rid,) in c.execute(select(m.c.record_id).where(m.c.org_id == org,
                                                                     m.c.identifier == f'email:{mail}')).all()]
        for rid in ids:
            row = c.execute(select(r.c.object_type, r.c.fields).where(r.c.org_id == org, r.c.id == rid,
                                                                      r.c.deleted.is_(False))).first()
            if row is None or row.object_type != 'Person':
                continue
            company = (row.fields or {}).get('Company')
            stage = c.execute(select(r.c.fields).where(r.c.org_id == org, r.c.id == str(company))).scalar_one_or_none() \
                if company else None
            return rid, ((stage or {}).get('Stage') == 'Customer')
        return None, False

    def support_inbound(self, org, source, title, sender, at, due, customers_only=False):
        """An inbound support message: the ONE card of its thread (``source``, its root link), made if there is
        none (no Kind, Waiting=support, Due=``due``, About = the sender's Person when the CRM knows them),
        reopened to the first stage with the new Due when it was Done and the message is newer than that, else
        left as it is. ``customers_only``: a sender who is not a Person on a customer Company makes no card.
        The actor is ``flows:support``; nothing else is written. Internal tier only."""
        check = rd.check_source(source)
        if not check:
            raise CRMError('A support card tracks a link')
        with self.store.begin() as c:
            person, customer = self._support_sender(c, org, sender)
            if customers_only and not customer:
                return {'card': None, 'status': 'not_a_customer'}
            life = self.lifecycle(DEFAULT_TYPE, None)
            first, last = life['stages'][0], life['stages'][-1]
            card = self._support_card(c, org, check)
            if card is None:
                fields = {'Title': _clip(title or check, TITLE_MAX), 'Source': check, 'Waiting': self.SUPPORT_WAITING,
                          'State': first, **({'Due': due} if due else {}),
                          **({'About': 'crm:' + person} if person else {})}
                rid = self._insert(c, org, self.SUPPORT_ACTOR, DEFAULT_TYPE, fields, '', 'A support message', [], None,
                                   [self.SUPPORT_ACTOR])
                return {'card': rid, 'status': 'created', 'customer': customer, 'revision': 1}
            state = card['fields'].get('State')
            if state == last and str(at or '') > self._support_closed_at(c, org, card['id']):
                row = self._card_row(c, org, card['id'])
                self._apply(c, org, self.SUPPORT_ACTOR, row, {'State': first, **({'Due': due} if due else {})},
                            'A new support message', [], None)
                return {'card': card['id'], 'status': 'reopened', 'customer': customer, 'revision': row['revision'] + 1}
            return {'card': card['id'], 'status': 'open' if state != last else 'unchanged', 'customer': customer,
                    'revision': card['revision']}

    def _support_closed_at(self, c, org, record):
        rv = s.revisions
        at = c.execute(select(rv.c.created_at).where(rv.c.org_id == org, rv.c.record_id == record)
                       .order_by(rv.c.revision.desc()).limit(1)).scalar_one_or_none()
        return str(at or '')

    def support_reply(self, org, source, at):
        """Our reply seen in a support thread: its card moves to the last stage (Done). A card already Done, or
        no card, is left as it is. The actor is ``flows:support``. Internal tier only."""
        check = rd.check_source(source)
        with self.store.begin() as c:
            card = self._support_card(c, org, check) if check else None
            if card is None:
                return {'card': None, 'status': 'none'}
            last = self.lifecycle(DEFAULT_TYPE, None)['stages'][-1]
            if card['fields'].get('State') == last:
                return {'card': card['id'], 'status': 'unchanged'}
            row = self._card_row(c, org, card['id'])
            self._apply(c, org, self.SUPPORT_ACTOR, row, {'State': last}, 'We replied', [], None)
            return {'card': card['id'], 'status': 'done', 'revision': row['revision'] + 1}

    #: the fields whose change a card's owner acts on: its stage, its claim, its Due, Started, its owner
    CHANGE_WATCHED = ('State', 'Holder', 'HoldUntil', 'Due', 'Started', 'Waiting')

    def card_change(self, org, record, revision):
        """What one revision of a card changed that its owner acts on: the names of the watched fields that
        differ (``CHANGE_WATCHED``; all that are set on a new card), its State, its owner after and before
        (``Waiting``), who wrote it and whether the card is open. Field names only: no value but State and the
        owners. Not a card, or no such revision: ``{'card': None}``. Internal tier only; it acts for nobody."""
        rv = s.revisions
        with self.store.engine.connect() as c:
            row = c.execute(select(rv.c.actor_id, rv.c.before, rv.c.after).where(
                rv.c.org_id == org, rv.c.record_id == str(record or ''), rv.c.revision == int(revision))).first()
            if row is None or not isinstance(row.after, dict):
                return {'card': None}
            after, before = row.after, row.before if isinstance(row.before, dict) else None
            if not self.store._definition(c, org, after.get('object_type')).get('x-card'):
                return {'card': None}
        fa, fb = after.get('fields') or {}, (before or {}).get('fields') or {}
        life = self.lifecycle(after.get('object_type'), fa.get('Kind')) or {}
        stages, side = list(life.get('stages') or []), list(life.get('side') or [])
        state = fa.get('State')
        return {'card': after.get('id'), 'revision': after.get('revision'), 'created': before is None,
                'changed': [k for k in self.CHANGE_WATCHED if fa.get(k) != fb.get(k)], 'state': state,
                'owner': fa.get('Waiting') or None, 'was_owner': fb.get('Waiting') or None,
                'actor': row.actor_id, 'open': not (state in side or (stages and state == stages[-1]))}

    def set_stage(self, org, record, stage, because, facts=None, mode='event'):
        """The stage rule's write (flows ``card_stage``): a software card's ``State`` and the facts that
        set it (``StageEvidence``). Written only when the stage differs, as one revision by
        ``rule:card-stage`` whose reason names the fact that moved it. A ``recheck`` that moves a card
        is a CORRECTION: the card said one stage and the facts another, so the evidence keeps
        ``correction: "was X, now Y, because …"``, readable on the card. Nothing else may set this
        card's stage (``normalise_fields``).

        ``stage=None`` is the rule saying it has NO stage for the card: its Source was never read, so
        there is no fact to move it by. The State is kept and ``StageEvidence`` records it
        (``stage: null``, ``no_reading: true``, ``because`` «no reading yet for <source>»), once: the
        same evidence again writes nothing. If the card's current stage was itself set by the rule
        with no read fact (``READ_FACTS``), it is reverted to the State it had before (``restored``,
        «was X, back to Y: X was set with no reading»), since that stage rested on an absence."""
        if mode not in STAGE_MODES:
            raise CRMError('mode is event or recheck')
        because = _clip(because, 300)
        if not because:
            raise CRMError('A stage move names the fact that moved it')
        facts = [f for f in (facts or []) if isinstance(f, dict)][:12]
        try:
            json.dumps(facts)
        except (TypeError, ValueError):
            raise CRMError('Stage facts are JSON') from None
        if len(json.dumps(facts)) > 6000:
            raise CRMError('Stage facts are at most 6000 characters')
        with self.store.begin() as c:
            row = self._card_row(c, org, record)
            raw = row['fields'] or {}
            template = self.template(raw.get('Kind'))
            if not tpl.moved_by_rule(template):
                if 'rule' not in (self.field_writers('State') or ()):
                    raise CRMError('Only a software card has a stage the rule sets')
                # the policy gives every card's State to the rule: its stages are its own lifecycle
                template = self.lifecycle(row['object_type'], raw.get('Kind'))
            if stage is None:
                return self._no_reading(c, org, row, raw, template, because, facts, mode)
            if stage not in template['stages'] + template['side']:
                raise CRMError(f'"{_clip(stage, 60)}" is not a stage of this card')
            was = raw.get('State')
            base = {'record': row['id'], 'revision': row['revision'], 'stage': stage, 'was': was}
            if was == stage:
                return {**base, 'status': 'unchanged'}
            at = self.clock().isoformat()
            evidence = {'stage': stage, 'because': because, 'facts': facts, 'at': at, 'by': STAGE_RULE_ACTOR,
                        'mode': mode, 'was': was}
            corrected = mode == 'recheck' and self._facts_changed(raw.get('StageEvidence'), was, facts)
            if corrected:
                evidence['correction'] = f'was {was}, now {stage}, because {because}'
            reason = (f'Corrected: {evidence["correction"]}' if corrected
                      else f'Moved to {stage}: {because}')
            self._rule.on = True
            try:
                self._apply(c, org, STAGE_RULE_ACTOR, row, {'State': stage, 'StageEvidence': evidence},
                            _clip(reason, REASON_MAX), [{'stage': evidence}], None)
            finally:
                self._rule.on = False
            return {**base, 'revision': row['revision'] + 1, 'status': 'corrected' if corrected else 'moved'}

    @staticmethod
    def _read_facts(facts):
        return sorted((f.get('fact'), json.dumps(f.get('value'), sort_keys=True, default=str))
                      for f in facts or [] if isinstance(f, dict) and f.get('fact') in READ_FACTS)

    def _facts_changed(self, prior, was, facts):
        """A CORRECTION is the facts read at the card's Source changing under a stage the rule set
        from them (a pull request closed or reverted, a commit gone from the pin). A stage that rested
        on no read fact (an absence, a hold, a person's or a backfill's move) is not corrected by the
        first facts: that is a plain move."""
        if not isinstance(prior, dict) or prior.get('by') != STAGE_RULE_ACTOR or prior.get('stage') != was:
            return False
        before = self._read_facts(prior.get('facts'))
        return bool(before) and before != self._read_facts(facts)

    def _no_reading(self, c, org, row, raw, template, because, facts, mode):
        """``set_stage(stage=None)``: keep the State (or revert one the rule set on an absence)."""
        was = raw.get('State')
        cur = raw.get('StageEvidence') if isinstance(raw.get('StageEvidence'), dict) else {}
        base = {'record': row['id'], 'revision': row['revision'], 'stage': was, 'was': was}
        on_absence = (cur.get('by') == STAGE_RULE_ACTOR and cur.get('stage') == was and was in ('Idea', 'Design')
                      and not any(isinstance(f, dict) and f.get('fact') in READ_FACTS for f in cur.get('facts') or [])
                      and cur.get('was') in template['stages'] + template['side'] and cur.get('was') != was)
        restore = cur.get('was') if on_absence else None
        if not restore and cur.get('no_reading') and cur.get('because') == because:
            return {**base, 'status': 'unchanged'}
        at = self.clock().isoformat()
        evidence = {'stage': None, 'no_reading': True, 'kept': restore or was, 'because': because, 'facts': facts,
                    'at': at, 'by': STAGE_RULE_ACTOR, 'mode': mode, 'was': was}
        changes = {'StageEvidence': evidence}
        if restore:
            evidence['restored'] = f'was {was}, back to {restore}: {was} was set with no reading ({_clip(cur.get("because"), 120)})'
            changes['State'] = restore
            reason = f'Restored: {evidence["restored"]}; {because}'
        else:
            reason = f'Kept {was}: {because}'
        self._rule.on = True
        try:
            self._apply(c, org, STAGE_RULE_ACTOR, row, changes, _clip(reason, REASON_MAX), [{'stage': evidence}], None)
        finally:
            self._rule.on = False
        return {**base, 'stage': restore or was, 'revision': row['revision'] + 1,
                'status': 'restored' if restore else 'kept'}

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
