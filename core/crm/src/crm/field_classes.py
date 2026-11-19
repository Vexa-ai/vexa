"""Per-field classification and the field view one caller gets of one record (PRD 0.13.4 D4, D20;
converged model §15).

THREE CLASSES, per object type and field (``crm_field_classes``):

- ``private`` — the record's owner only (and an instance administrator acting in person). Absent for
  every other reader, and never reaches a shared (room) caller.
- ``org`` — everyone the CRM policy lets read it (ADR-0047 d6's two gates, unchanged).
- ``ws`` (workspace-shareable) — as ``org``, and explicitly shareable to the workspaces listed in
  ``workspaces``: a room of one of them reads it.

Two pseudo-fields carry free text and identifiers: ``$narrative`` (the record's notes) and
``$match_ids`` (email/domain/calendar identifiers).

DEFAULTS ARE SEEDED POLICY (founder rule: behaviours are policy with common-sense defaults).
``bootstrap.bootstrap_org`` writes ``DEFAULT_CLASSES`` for the stable entity types as rows an
administrator may change; a field with no row (an imported type, a new field) is classified by
``default_class`` with the same rules, so the seed and the fallback never disagree:

- private: contact history and email-derived data — fields named like ``LastContact``,
  ``ContactHistory``, ``LastEmail``…, any property marked ``x-source: email|mail|calendar``, and
  ``$match_ids`` (they are email addresses);
- org: everything else, company basics included (``Name``, ``Domain``…), and ``$narrative``;
- a property's own ``x-class`` in its definition wins over the name rule.

THE ROOM CEILING (a shared caller: a room turn, a shared chat, later the ``ws:`` principal). A
shared caller is never an administrator (``caller.from_validate``, ``Store._policy``). What it reads
of a record is decided by the organisation's ``room_fields`` policy key:

- ``intersection`` (the default, interim until a classification UI exists): the fields EVERY
  current member of the turn's workspaces may read on that record, minus private fields, minus
  ``ws`` fields shared only with other workspaces, plus ``ws`` fields shared with this one.
- ``classified``: only fields classified ``ws`` for one of the turn's workspaces.

Free text never reaches a room through the intersection: ``$narrative`` and ``$match_ids`` only
when explicitly classified ``ws`` for the room's workspace; revision and proposal reasons and
evidence never. Restricted fields are ABSENT from answers (never nulled), so their absence is
indistinguishable from an empty field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CLASSES = ('private', 'org', 'ws')
NARRATIVE = '$narrative'
MATCH_IDS = '$match_ids'
PSEUDO = (NARRATIVE, MATCH_IDS)

#: The organisation default policy key that picks the room rule, and its default.
ROOM_RULE_KEY = 'room_fields'
ROOM_RULES = ('intersection', 'classified')
DEFAULT_ROOM_RULE = 'intersection'

#: Contact history and email-derived data, by name (case and punctuation ignored).
_PRIVATE_NAME = re.compile(r'(lastcontact|contacthistory|lastemail|lastmessage|lastmeeting|lastinteraction'
                           r'|lastreply|emailthread|mailthread|interactions|timeline|emailsnippet|emailbody'
                           r'|emailsubject|threadsummary)')
_PRIVATE_SOURCES = ('email', 'mail', 'calendar')


def _norm(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(name or '').lower())


def default_class(name: str, prop: dict | None = None) -> tuple[str, tuple]:
    """``(class, workspaces)`` for a field with no classification row."""
    prop = prop if isinstance(prop, dict) else {}
    declared = prop.get('x-class')
    if declared in ('private', 'org'):
        return declared, ()
    if isinstance(declared, dict) and isinstance(declared.get('ws'), list):
        return 'ws', tuple(str(w) for w in declared['ws'] if str(w))
    if name == MATCH_IDS:
        return 'private', ()
    if name == NARRATIVE:
        return 'org', ()
    if str(prop.get('x-source') or '').lower() in _PRIVATE_SOURCES or _PRIVATE_NAME.search(_norm(name)):
        return 'private', ()
    return 'org', ()


def default_classes(definitions: dict) -> list[dict]:
    """The seed rows for ``definitions`` (``{type: json-schema}``): every property and both pseudo-fields."""
    rows = []
    for kind, definition in definitions.items():
        props = (definition or {}).get('properties', {}) or {}
        for name in list(props) + list(PSEUDO):
            cls, workspaces = default_class(name, props.get(name))
            rows.append({'object_type': kind, 'field': name, 'class': cls, 'workspaces': list(workspaces)})
    return rows


class Classes:
    """Every field's class for one organisation: rows first, then ``default_class``."""

    def __init__(self, rows, definitions):
        self.rows = {(r['object_type'], r['field']): (r['class'] if r['class'] in CLASSES else 'private',
                                                      tuple(r.get('workspaces') or ()))
                     for r in rows}
        self.definitions = definitions or {}
        self._cache: dict = {}

    def props(self, kind) -> dict:
        return (self.definitions.get(kind) or {}).get('properties', {}) or {}

    def of(self, kind, name) -> tuple[str, tuple]:
        key = (kind, name)
        if key not in self._cache:
            self._cache[key] = self.rows.get(key) or default_class(name, self.props(kind).get(name))
        return self._cache[key]

    def names(self, kind):
        """Every classifiable name of a type: its properties, any classified extra, the pseudo-fields."""
        extra = {f for (k, f) in self.rows if k == kind}
        return set(self.props(kind)) | extra | set(PSEUDO)

    def private(self, kind) -> set:
        return {n for n in self.names(kind) if self.of(kind, n)[0] == 'private'}

    def shared_with(self, kind, workspaces) -> set:
        """Fields classified ``ws`` for one of ``workspaces``."""
        ws = set(workspaces)
        return {n for n in self.names(kind) if self.of(kind, n)[0] == 'ws' and ws & set(self.of(kind, n)[1])}

    def shared_elsewhere(self, kind, workspaces) -> set:
        """Fields classified ``ws`` for other workspaces only."""
        ws = set(workspaces)
        return {n for n in self.names(kind) if self.of(kind, n)[0] == 'ws' and not ws & set(self.of(kind, n)[1])}


ALL = None      # ``FieldView.allowed`` is None for "every field" and a set otherwise


@dataclass
class FieldView:
    """What one caller reads of one record. ``allowed`` None = every field, minus ``excluded``."""
    allowed: set | None
    excluded: set = field(default_factory=set)
    #: Free text a partial reader may not see: revision/proposal reasons and evidence.
    full: bool = False
    narrative: bool = False
    match_ids: bool = False
    props: tuple = ()

    def has(self, name) -> bool:
        if name in self.excluded:
            return False
        return self.allowed is None or name in self.allowed

    def filter(self, values: dict) -> dict:
        return {k: v for k, v in (values or {}).items() if self.has(k)}

    @property
    def readable(self) -> set:
        """Field names for consumers that take a set with ``'*'`` for all (card faces, layouts)."""
        if self.allowed is None and not self.excluded:
            return {'*'}
        base = set(self.props) if self.allowed is None else set(self.allowed)
        return {n for n in base if n not in self.excluded and n not in PSEUDO}

    @property
    def partial(self) -> bool:
        return not (self.allowed is None and not self.excluded)


def as_set(fields) -> set | None:
    """A policy's field list (``['*']`` for all) as ``FieldView.allowed``."""
    fields = list(fields or [])
    return ALL if '*' in fields else set(fields)


def intersect(a: set | None, b: set | None) -> set | None:
    if a is None:
        return None if b is None else set(b)
    return set(a) if b is None else set(a) & set(b)
