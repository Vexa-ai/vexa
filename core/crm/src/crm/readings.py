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

DELIVERY FIELDS (1 Nov 2026, the software card's stage rule, ``flows_defs/card_stage.py``): the
per-card delivery reading (``github/delivery``) adds what the stage is computed from —
``draft`` and ``merge_commit_sha`` of a pull request, ``state_reason`` of an issue, ``pr`` (the pull
request a card's issue is delivered by, or the card's own), ``issue``, ``checks`` (the check runs of
the head commit: ``none|pending|success|failure`` with counts), ``prod`` (whether the commit the
prod pin's chart was built from contains the card's commit) and ``release`` (the release tag that
contains it, or null when none of the newest does). Each object is checked to its declared shape.

ONE ENTRY PER SOURCE: ``Reading.sources[<connection>/<reading>]`` keeps each source's latest
reading whole, with its evidence, so the delivery reading and the list sensors' readings never
overwrite each other. ``Reading.fields`` is their merge (a later read wins per field) and
``Reading.evidence`` the latest one's, so a card read by one source looks exactly as before.

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
FIELDS = ('title', 'state', 'merged_at', 'head_sha', 'labels', 'url', 'updated_at',
          'draft', 'merge_commit_sha', 'state_reason', 'pr', 'issue', 'checks', 'prod', 'release')
STATE_REASONS = ('completed', 'not_planned', 'reopened', 'duplicate')
CHECKS = ('none', 'pending', 'success', 'failure')
#: At most this many sources kept on one card.
SOURCES_MAX = 8
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
    if 'draft' in fields:
        out['draft'] = _bool('draft', fields['draft'])
    if 'merge_commit_sha' in fields:
        out['merge_commit_sha'] = _text('merge_commit_sha', fields['merge_commit_sha'], 64, _SHA)
    if 'state_reason' in fields:
        out['state_reason'] = _enum('state_reason', fields['state_reason'], STATE_REASONS)
    if 'pr' in fields:
        out['pr'] = _object('pr', fields['pr'], _pr)
    if 'issue' in fields:
        out['issue'] = _object('issue', fields['issue'], _issue)
    if 'checks' in fields:
        out['checks'] = _object('checks', fields['checks'], _checks)
    if 'prod' in fields:
        out['prod'] = _object('prod', fields['prod'], _prod)
    if 'release' in fields:
        out['release'] = _object('release', fields['release'], _release)
    return out


def _bool(name, value):
    if value is None or isinstance(value, bool):
        return value
    raise CRMError(f'reading field {name} is true or false')


def _enum(name, value, allowed):
    if value is None or value in allowed:
        return value
    raise CRMError(f'reading field {name} is one of {", ".join(allowed)}')


def _int(name, value, low=0, high=10**9):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CRMError(f'reading field {name} is a whole number')
    return value


def _object(name, value, shape):
    """A delivery object in its declared shape (``shape`` names each key's check), or None."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CRMError(f'reading field {name} is an object')
    unknown = sorted(set(value) - set(shape))
    if unknown:
        raise CRMError(f'reading field {name} does not take ' + ', '.join(unknown[:5]))
    return {k: check(f'{name}.{k}', value[k]) for k, check in shape.items() if k in value}


def _url(name, value):
    url = _text(name, value, SOURCE_MAX)
    if url is not None and not url.startswith('https://'):
        raise CRMError(f'reading field {name} is an https URL')
    return url


def _sha(name, value):
    return _text(name, value, 64, _SHA)


def _iso(name, value):
    return _text(name, value, 40, _ISO)


def _state(name, value):
    return _enum(name, value, STATES)


_pr = {'url': _url, 'number': _int, 'state': _state, 'draft': _bool, 'design_doc': _bool, 'head_sha': _sha,
       'merge_commit_sha': _sha, 'merged_at': _iso, 'updated_at': _iso}
_issue = {'url': _url, 'state': _state, 'state_reason': lambda n, v: _enum(n, v, STATE_REASONS),
          'closed_at': _iso}
_checks = {'status': lambda n, v: _enum(n, v, CHECKS), 'sha': _sha, 'total': _int, 'running': _int,
           'failed': _int, 'passed': _int}
_prod = {'contains': _bool, 'chart': lambda n, v: _text(n, v, 100), 'entry_seq': _int, 'source_sha': _sha,
         'via': lambda n, v: _enum(n, v, ('head', 'merge')), 'pin_ref': lambda n, v: _text(n, v, 300),
         'commit': _sha}
_release = {'tag': lambda n, v: _text(n, v, 100), 'checked': lambda n, v: _int(n, v, 0, 50), 'commit': _sha}


def merged(sources) -> tuple:
    """``(fields, evidence)`` of a card's readings: the per-source fields merged, the one read
    later winning a field both carry; the evidence the latest reading's."""
    entries = sorted((v for v in (sources or {}).values() if isinstance(v, dict)),
                     key=lambda e: float((e.get('evidence') or {}).get('read_at') or 0))
    fields = {}
    for e in entries:
        fields.update(e.get('fields') or {})
    return fields, (entries[-1].get('evidence') if entries else {})


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
