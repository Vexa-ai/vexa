"""Sensor readings on a card: what the system read at its source, kept as fields with evidence.

Founder, 1 Nov 2026: the interface shows what the system read, rendered deterministically from the
API, never prose an agent typed. A card that tracks something outside the CRM (a GitHub issue or
pull request today) names it in its typed link ``Source``; the generic ``reading`` sensor in flows
reads that source, and the CRM pack's step writes the reading here through
``/internal/records/reading``. The reading is the card's ``Reading`` field:

    {"fields":   {"title": …, "state": "open|closed|merged", "merged_at": …, "head_sha": …,
                  "labels": [...], "url": …, "updated_at": …},
     "evidence": {"source": "<connection>/<reading>", "query": "<path?params>", "read_at": <epoch s>,
                  "cursor": "<the cursor the read started from>"}}

RULES
* ``Reading`` is written by the reading route only. Every other write path (a person's edit, an
  agent's proposal, ``crm_change``, a gate) is refused on it (``card_ops.SENSOR_ONLY``). ``Source``
  is a person's or an agent's: it says what the card tracks, never what the source says.
* Only the declared fields (``FIELDS``) are written, each to its declared shape; anything else is
  refused, so a sensor cannot write a field an agent may write.
* Idempotent by (record, source, read_at): the same reading again changes nothing. A reading older
  than the one on the card is ``stale`` and changes nothing. A reading whose values equal the
  card's is ``unchanged`` (no revision: the earlier evidence still stands). Otherwise the later
  reading REPLACES the earlier one whole, as one revision whose evidence is the reading's.

RENDERER CONTRACT (the card's read answer carries ``source`` and ``reading``): a card with a
reading shows the source's title (``reading.fields.title``) and state (``reading.fields.state``),
and links ``reading.fields.url`` (else ``source``); the typed ``Title`` is shown only on a card with
no reading. Nothing is inferred from ``Summary`` or any other free text.
"""
from __future__ import annotations

import math
import re

from .store import CRMError

#: The fields a reading may carry, and nothing else.
STATES = ('open', 'closed', 'merged')
FIELDS = ('title', 'state', 'merged_at', 'head_sha', 'labels', 'url', 'updated_at')
SOURCE_MAX = 300
_SOURCE_ID = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}/[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$')
_SHA = re.compile(r'^[0-9a-f]{7,64}$')
_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
#: https://github.com/<owner>/<repo>/(issues|pull)/<n>, and the short form <owner>/<repo>#<n>.
_GITHUB_URL = re.compile(r'^https://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/(?:issues|pull|pulls)/(\d+)'
                         r'(?:[/?#].*)?$')
_GITHUB_SHORT = re.compile(r'^(?:github:)?([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)#(\d+)$')
#: The legacy form an agent typed into ``Summary`` before ``Source`` existed: «GitHub: owner/repo#N».
SUMMARY_LINK = re.compile(r'GitHub:\s*([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)#(\d+)\b')


def subject(link) -> str:
    """The one name of what a link points at, so a card's ``Source`` and a reading's item meet.
    A GitHub issue and pull request (URL either way, or ``owner/repo#N``) become
    ``github.com/<owner>/<repo>#<n>`` (owner and repo lower-cased: GitHub's are case-insensitive);
    any other https URL is itself without a trailing slash; anything else is ''."""
    text = str(link or '').strip()
    m = _GITHUB_URL.match(text) or _GITHUB_SHORT.match(text)
    if m:
        return f'github.com/{m.group(1).lower()}/{m.group(2).lower()}#{int(m.group(3))}'
    if text.startswith('https://') and len(text) <= SOURCE_MAX and not _CONTROL.search(text) and ' ' not in text:
        return text.rstrip('/')
    return ''


def summary_subject(summary) -> str:
    """The subject a legacy ``Summary`` names («GitHub: owner/repo#N»), or ''."""
    m = SUMMARY_LINK.search(str(summary or ''))
    return f'github.com/{m.group(1).lower()}/{m.group(2).lower()}#{int(m.group(3))}' if m else ''


def check_source(value):
    """``Source`` as stored: a link ``subject`` understands, at most 300 characters, or None."""
    if value is None or value == '':
        return None
    text = str(value).strip()
    if len(text) > SOURCE_MAX or not subject(text):
        raise CRMError('Source is a link to what the card tracks: an https URL, or owner/repo#N for GitHub')
    return text


def _text(name, value, limit, pattern=None):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or _CONTROL.search(value) \
            or (pattern is not None and not pattern.fullmatch(value)):
        raise CRMError(f'reading field {name} is not in its declared shape')
    return value


def normalise_fields(fields) -> dict:
    """The declared reading fields, each in its declared shape; anything else is refused.
    ``labels`` takes names or objects with a ``name`` (as GitHub answers them); ``state`` is
    ``merged`` when the source says closed and gives a ``merged_at``."""
    if not isinstance(fields, dict) or not fields:
        raise CRMError('a reading carries its fields')
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise CRMError('a reading writes only its declared fields; not ' + ', '.join(unknown[:5]))
    out = {}
    if 'title' in fields:
        title = _text('title', fields['title'], 300)
        out['title'] = title.strip() if title else title
    if 'merged_at' in fields:
        out['merged_at'] = _text('merged_at', fields['merged_at'], 40, _ISO)
    if 'updated_at' in fields:
        out['updated_at'] = _text('updated_at', fields['updated_at'], 40, _ISO)
    if 'head_sha' in fields:
        out['head_sha'] = _text('head_sha', fields['head_sha'], 64, _SHA)
    if 'url' in fields:
        url = _text('url', fields['url'], SOURCE_MAX)
        if url is not None and not url.startswith('https://'):
            raise CRMError('reading field url is an https URL')
        out['url'] = url
    if 'labels' in fields:
        raw = fields['labels']
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > 30:
            raise CRMError('reading field labels is a list of at most 30 labels')
        names = []
        for item in raw:
            name = item.get('name') if isinstance(item, dict) else item
            names.append(_text('labels', name, 100))
        out['labels'] = [n for n in names if n]
    if 'state' in fields:
        state = str(fields['state'] or '').lower()
        if state == 'closed' and out.get('merged_at'):
            state = 'merged'
        if state not in STATES:
            raise CRMError(f'reading field state is one of {", ".join(STATES)}')
        out['state'] = state
    return out


def normalise_evidence(source, evidence) -> dict:
    """Where the reading came from: ``source`` (``<connection>/<reading>``), ``read_at`` (epoch
    seconds), and the ``query`` and ``cursor`` the read used when known."""
    if not _SOURCE_ID.fullmatch(str(source or '')):
        raise CRMError('a reading names its source as <connection>/<reading>')
    ev = evidence if isinstance(evidence, dict) else {}
    read_at = ev.get('read_at')
    if isinstance(read_at, bool) or not isinstance(read_at, (int, float)) or not math.isfinite(read_at) \
            or read_at <= 0:
        raise CRMError('a reading carries read_at (epoch seconds)')
    out = {'source': str(source), 'read_at': float(read_at)}
    for key, limit in (('query', 500), ('cursor', 100)):
        if ev.get(key) not in (None, ''):
            out[key] = _text(key, str(ev[key]), limit)
    return out
