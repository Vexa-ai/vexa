"""card_face.py — the card-face profile of blocks.v1: sanitise a stored face, resolve it per viewer.

CANONICAL COPY (ADR-0044). Edit this file and copy it VERBATIM over every vendored copy (listed in
``scripts/parity.json``, fact ``card-face``); never edit a copy in place. Standard library only,
stateless.

A card face is composed from a small vocabulary (desktop ADR §2.2). Two kinds of block:

- **structural** blocks carry no value of their own — ``{"type": "stepper"}`` and its siblings draw
  from the card's own record as the viewer reads it (stage, members, latest movement, sub-cards);
- **data** blocks NAME a field — ``{"type": "metric", "field": "Amount", "label": "Amount"}`` — and
  the value is read from the record per viewer. A block whose field the viewer may not read resolves
  to ``{"type": "locked", "label": …}``, so a face never shows what the record would not.

There is no block for literal text: whatever an agent wants a face to say goes into a field (``Next``,
``Summary``) and carries that field's access. A label is plain, short, and part of the ``Face`` field
itself. Unknown blocks and unknown keys are dropped; sizes are capped.
"""
from __future__ import annotations

import re

LIMITS = {'blocks': 8, 'label': 40, 'text': 160, 'rows': 4, 'checklist': 10, 'metric': 24, 'row': 80}
STRUCTURAL = ('stepper', 'people', 'movement', 'waiting', 'next', 'subcards', 'ref', 'status')
DATA = ('metric', 'text', 'progress', 'due', 'checklist', 'list')
DEFAULT_FACE = [{'type': t} for t in ('stepper', 'next', 'waiting', 'movement', 'subcards', 'people')]

_FIELD = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]+')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}')


def plain(value, limit):
    """One line of plain text, control characters folded to a space, at most ``limit`` characters."""
    return _CONTROL.sub(' ', str(value if value is not None else '')).strip()[:limit]


def sanitize(face):
    """The stored face: known blocks only, their own keys only, at most ``LIMITS['blocks']``."""
    out = []
    if not isinstance(face, list):
        return out
    for raw in face:
        if len(out) >= LIMITS['blocks']:
            break
        if not isinstance(raw, dict):
            continue
        kind = raw.get('type')
        if kind in STRUCTURAL:
            out.append({'type': kind})
        elif kind in DATA and isinstance(raw.get('field'), str) and _FIELD.fullmatch(raw['field']):
            block = {'type': kind, 'field': raw['field']}
            label = plain(raw.get('label'), LIMITS['label'])
            if label:
                block['label'] = label
            out.append(block)
    return out


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _metric(value, fmt):
    if _number(value):
        if fmt == 'percent01':
            return f'{round(max(0.0, min(1.0, float(value))) * 100)}%'
        return f'{value:g}' if isinstance(value, float) else str(value)
    if isinstance(value, str) and value.strip():
        return plain(value, LIMITS['metric'])
    return None


def resolve_block(block, fields, readable, formats=None):
    """One stored block for one viewer: ``fields`` is what they read, ``readable`` the field names they
    may read (``'*'`` in it means all). None drops the block (no value to show)."""
    kind = block['type']
    if kind in STRUCTURAL:
        return {'type': kind}
    name = block['field']
    label = block.get('label') or ''
    if '*' not in readable and name not in readable:
        return {'type': 'locked', 'label': label or name}
    value = fields.get(name)
    if value is None:
        return None
    labelled = {'label': label} if label else {}
    if kind == 'metric':
        text = _metric(value, (formats or {}).get(name))
        return {'type': 'metric', **labelled, 'value': text} if text else None
    if kind == 'text':
        text = plain(value, LIMITS['text'])
        return {'type': 'text', **labelled, 'text': text} if text else None
    if kind == 'progress':
        return {'type': 'progress', **labelled, 'value': max(0.0, min(1.0, float(value)))} if _number(value) else None
    if kind == 'due':
        return {'type': 'due', **labelled, 'at': value[:10]} if isinstance(value, str) and _DATE.match(value) else None
    if kind == 'checklist':
        if not isinstance(value, list):
            return None
        items = [{'text': plain(i.get('text'), LIMITS['row']), 'done': i.get('done') is True}
                 for i in value[:LIMITS['checklist']] if isinstance(i, dict) and plain(i.get('text'), LIMITS['row'])]
        return {'type': 'checklist', **labelled, 'items': items} if items else None
    if kind == 'list':
        rows = value if isinstance(value, list) else str(value).splitlines()
        rows = [plain(r, LIMITS['row']) for r in rows if plain(r, LIMITS['row'])][:LIMITS['rows']]
        return {'type': 'list', **labelled, 'rows': rows} if rows else None
    return None


def resolve(face, fields, readable, formats=None):
    """The face one viewer sees: the stored face (or the default), sanitised, each block resolved."""
    stored = sanitize(face) or [dict(b) for b in DEFAULT_FACE]
    out = []
    for block in stored:
        resolved = resolve_block(block, fields or {}, set(readable or ()), formats)
        if resolved is not None:
            out.append(resolved)
    return out
