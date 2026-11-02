"""The software card's stage, from facts (founder, 1 Nov 2026).

«A card's status is its CRM State, fully deterministic and true to GitHub and other facts;
deterministic checks keep it from lying.» A software card (the CRM's ``software`` kind,
``card_templates.py``) moves through ``Idea > Design > Code > Review > On prod > Done``,
side ``Dropped`` — from idea to merged into main (founder 1 Nov 2026: «релиз нам здесь не нужен»: a release is a
tag, and a version is its own destination card, not a card stage). Nobody sets that by hand: this module computes it, and the crm pack's steps write
it through the CRM's ``/internal/cards/stage`` (the only path that may).

THREE PARTS, the first one pure:

* :func:`stage` — ``stage(readings, flows_claim) -> (stage, evidence)``. No I/O, no clock unless
  given. ``readings`` is what the card's readings say (:func:`readings_from_card` builds it from the
  CRM's ``Reading``); ``flows_claim`` is the card's live claim (``claim_of``: the CRM's ``hold``), or None.
* :func:`readings_from_card` — the stored per-source readings of a card, as :func:`stage` reads them.
* :func:`read_delivery` — the scheduled re-check's fresh read of one card: GET-only calls declared in
  the connections file (``delivery``), the answer the ``github/delivery`` reading's fields.

THE RULES (first match wins; each names the fact that decided it):

1. The card's issue is closed as ``not_planned`` (or ``duplicate``) and no pull request on it is merged → **Dropped**.
2. Its pull request is merged → **Done** (merged into main; prod came first by the delivery order). Merged into another
   branch (its ``base`` is not the repository's ``default_branch``, e.g. ``codex/v0.13.4``) is not Done: **On prod** when
   the prod pin contains it, otherwise **Review** (2 Nov: seq61 showed Done while its code was only in codex).
3. Its pull request is open:
   a. it is a design document (every file it changes is under ``docs/adr/`` or ``docs/design/``,
      the delivery reading's ``design_doc``) → **Design**;
   b. the prod pin's chart was built from a commit that contains the head commit → **On prod**
      (delivery order: prod first, then main);
   c. the pull request is a draft → **Code** (code exists, not yet offered for review);
   d. otherwise → **Review**.
4. No pull request, or it was closed without a merge:
   a. the card's issue is closed as completed → **Done**;
   b. a live Flows claim holds the card → **Code** (a worker is writing code and running tests;
      founder, 1 Nov 2026: Design is only a design document under review, never «someone holds it»);
   c. otherwise → **Idea**.

ABSENCE IS NOT A FACT (founder, 1 Nov 2026: a missing source shows as «no source», never a
substitute). A card with a typed ``Source`` and no reading of it gets NO stage: the rule answers
``None`` with the evidence «no reading yet for <source>», and the CRM keeps the card's State.
Rules 4b and 4c («no pull request») need a reading that says so: a card whose Source was read but
whose readings say nothing about a pull request also gets no stage. Only a card with no Source at
all (a pure idea, nothing to read) may be an Idea, or Code under a claim, without a reading.

So every revert follows from the facts: a pull request closed after review goes back to Idea (or
Code while someone holds the card); a head commit the prod pin no longer contains (a rollback, or a
new push not yet pinned) goes back to Review. A merge is never undone: a merged card the pin rolled
back from stays Done.

«IN THE PROD PIN», precisely: ``spec.source.targetRevision`` of the prod Application file
(``DmitriyG228/vexa-stations`` ``channels/vexa-internal/apps/vexa-prod.yaml`` on ``main``) names a
chart version; the channel file (``channels/vexa-internal/channel.yaml``) names, for each publish
entry, the chart version and the ``source_sha`` its images were built from. The card's commit is in
the pin when it is an ancestor of, or equal to, that ``source_sha`` in the pinned repository
(``GET /compare/<commit>...<source_sha>`` answers ``ahead`` or ``identical``). The commit checked is
the head commit, and once merged also the merge commit (a squash merge makes a new one). A card in
another repository has no prod fact (unknown, never "not in prod").
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Callable, Optional

from flows_sensors.base import SensorFault

STAGES = ('Idea', 'Design', 'Code', 'Review', 'On prod', 'Done')
SIDE = ('Dropped',)
#: The reading the re-check writes (``<connection>/<reading>``).
DELIVERY_SOURCE = 'github/delivery'
#: How many of the newest releases a re-check compares a merged commit with.
RELEASES_CHECKED = 3
#: A pull request whose changed files all sit under these is a design document (rule 3a).
DESIGN_DIRS = ('docs/adr/', 'docs/design/')
_NAME = re.compile(r'^[A-Za-z0-9_.\-]{1,100}$')
_SHA = re.compile(r'^[0-9a-f]{7,64}$')
_SUBJECT = re.compile(r'^github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)#(\d+)$')
_GITHUB_URL = re.compile(r'^https://(?:www\.)?github\.com/[^/]+/[^/]+/(issues|pull|pulls)/\d+')
FAILED = ('failure', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale')


# ── 1. the pure rule ─────────────────────────────────────────────────────────────────────────────
def _fact(name, value, src):
    ev = (src or {}).get('evidence') or {}
    return {'fact': name, 'value': value, 'source': ev.get('source'), 'read_at': ev.get('read_at')}


def _short(sha):
    return str(sha or '')[:8]


def stage(readings: dict, flows_claim: Optional[dict] = None) -> tuple[Optional[str], dict]:
    """The stage a software card's facts put it in, and the evidence: ``because`` (the fact that
    decided, in words), ``facts`` (each fact read, with its source and read time) and ``activity``
    (what is happening now: an agent holding it, CI running). Pure: same input, same answer.

    ``None`` when the card has a ``source`` and the facts that would decide it were never read
    (``read`` false, or no ``pr_known`` where only «no pull request» would decide): the evidence then
    says «no reading yet for <source>» (with ``unread``, why, when the caller knows) and carries
    ``no_reading: true``. The caller keeps the card's State."""
    r = readings or {}
    pr, issue = r.get('pr'), r.get('issue')
    checks, prod, release = r.get('checks'), r.get('prod'), r.get('release')
    source = str(r.get('source') or '').strip()
    read = r.get('read', any(x for x in (pr, issue, checks, prod, release)))
    pr_known = r.get('pr_known', pr is not None)
    facts, activity = [], []
    if pr:
        facts.append(_fact('pr', {k: pr.get(k) for k in ('url', 'state', 'draft', 'design_doc', 'head_sha',
                                                         'merge_commit_sha', 'merged_at', 'base', 'default_branch')
                                   if pr.get(k) is not None}, pr))
    if issue:
        facts.append(_fact('issue', {k: issue.get(k) for k in ('url', 'state', 'state_reason') if issue.get(k)}, issue))
    if flows_claim:
        facts.append({'fact': 'claim', 'value': {k: flows_claim.get(k) for k in ('holder', 'host', 'since', 'lease_until')
                                                  if flows_claim.get(k) is not None},
                      'source': 'card/claim', 'read_at': flows_claim.get('read_at')})
        activity.append({'kind': 'agent', 'holder': flows_claim.get('holder'), 'host': flows_claim.get('host'),
                         'since': flows_claim.get('since')})
    head = (pr or {}).get('head_sha')
    merge = (pr or {}).get('merge_commit_sha')
    commits = {c for c in (head, merge) if c}
    if checks and (not checks.get('sha') or checks.get('sha') in commits or not commits):
        facts.append(_fact('checks', {k: checks.get(k) for k in ('status', 'sha', 'total', 'running', 'failed')
                                      if checks.get(k) is not None}, checks))
        if checks.get('status') == 'pending':
            activity.append({'kind': 'ci', 'sha': checks.get('sha'), 'running': checks.get('running')})
    # a prod or release fact counts only for the commit it was checked for
    prod_ok = bool(prod) and (not prod.get('commit') or prod.get('commit') in commits)
    rel_ok = bool(release) and (not release.get('commit') or release.get('commit') in commits)
    if prod_ok:
        facts.append(_fact('prod', {k: prod.get(k) for k in ('contains', 'chart', 'entry_seq', 'source_sha', 'commit', 'via')
                                    if prod.get(k) is not None}, prod))
    if rel_ok:
        facts.append(_fact('release', {k: release.get(k) for k in ('tag', 'commit', 'checked')
                                       if release.get(k) is not None}, release))

    def out(name, because):
        return name, {'stage': name, 'because': because, 'facts': facts, 'activity': activity}

    def unread(what):
        why = str(r.get('unread') or '').strip()
        because = f'no reading yet {what}{source}' + (f' ({why})' if why else '')
        return None, {'stage': None, 'because': because[:300], 'facts': facts, 'activity': activity,
                      'no_reading': True}

    if source and not read:
        return unread('for ')

    pr_state = (pr or {}).get('state')
    num = f"#{pr.get('number')}" if pr and pr.get('number') else 'the pull request'
    issue_closed = bool(issue) and issue.get('state') == 'closed'
    if issue_closed and issue.get('state_reason') in ('not_planned', 'duplicate') and pr_state != 'merged':
        return out('Dropped', f"the issue was closed as {issue['state_reason'].replace('_', ' ')}")
    if pr_state == 'merged':
        base, main = pr.get('base'), pr.get('default_branch')
        if base and main and base != main:      # merged, but not into main: Done is «merged into main» (founder Q3 «A»)
            if prod_ok and prod.get('contains'):
                return out('On prod', f"{num} was merged into {base}, not {main}; the prod pin {prod.get('chart')} "
                                      f"contains {_short(prod.get('commit') or merge)}")
            return out('Review', f"{num} was merged into {base}, not {main}")
        return out('Done', f"{num} was merged at {pr.get('merged_at') or 'an unknown time'}"
                   + (f" as {_short(merge)}" if merge else ''))
    if pr_state == 'open':
        if pr.get('design_doc') is True:
            return out('Design', f'{num} is an open design document (it changes only {" or ".join(DESIGN_DIRS)})')
        if prod_ok and prod.get('contains'):
            return out('On prod', f"the prod pin {prod.get('chart')} (built from {_short(prod.get('source_sha'))}) "
                                  f"contains head {_short(prod.get('commit') or head)}")
        if pr.get('draft'):
            return out('Code', f'{num} is a draft')
        return out('Review', f"{num} is open for review at {_short(head)}"
                   + (' (not in the prod pin)' if prod_ok else ''))
    if issue_closed and issue.get('state_reason') in (None, 'completed'):
        return out('Done', 'the issue was closed as completed, with no pull request on the card')
    if source and not pr_known:
        return unread('of a pull request for ')
    if flows_claim:
        return out('Code', f"held by {flows_claim.get('holder') or 'a claim'}"
                           + (f" since {flows_claim['since']}" if flows_claim.get('since') else '')
                           + ', no pull request yet')
    if pr_state == 'closed':
        return out('Idea', f'{num} was closed without a merge')
    return out('Idea', 'no pull request and nobody holds the card')


# ── 2. the card's stored readings, as the rule reads them ───────────────────────────────────────
def _kind(source, sources):
    text = str(source or '')
    m = _GITHUB_URL.match(text)
    if m:
        return 'issue' if m.group(1) == 'issues' else 'pull'
    for entry in (sources or {}).values():
        f = (entry or {}).get('fields') or {}
        if 'issue' in f:
            return 'issue'
        if f.get('head_sha') or f.get('merged_at'):
            return 'pull'
    return 'pull' if 'pull' in text else 'issue'


def _latest(cands):
    cands = [c for c in cands if c and c.get('value') is not None]
    if not cands:
        return None
    best = max(cands, key=lambda c: float((c.get('evidence') or {}).get('read_at') or 0))
    return {**best['value'], 'evidence': best.get('evidence') or {}}


def readings_from_card(card: dict) -> dict:
    """``{pr, issue, checks, prod, release}`` from a card's ``Reading`` (``sources`` per source,
    or a single reading written before readings were kept per source). Each fact is the latest any
    source read, with that source's evidence."""
    reading = (card or {}).get('reading') or {}
    sources = dict(reading.get('sources') or {})
    if not sources and reading.get('fields') is not None:
        ev = reading.get('evidence') or {}
        sources[str(ev.get('source') or 'reading')] = {'fields': reading['fields'], 'evidence': ev}
    kind = _kind((card or {}).get('source'), sources)
    pr_c, issue_c, rest = [], [], {'checks': [], 'prod': [], 'release': []}
    pr_known = False
    for entry in sources.values():
        f = (entry or {}).get('fields') or {}
        ev = (entry or {}).get('evidence') or {}
        if 'pr' in f:                     # a reading that answered «which pull request», None included
            pr_known = True
        if isinstance(f.get('pr'), dict):
            pr_c.append({'value': f['pr'], 'evidence': ev})
        if isinstance(f.get('issue'), dict):
            issue_c.append({'value': f['issue'], 'evidence': ev})
        top = {k: f[k] for k in ('state', 'state_reason', 'draft', 'head_sha', 'merge_commit_sha', 'merged_at', 'url')
               if k in f}
        if top.get('state') != 'merged':
            top.pop('merge_commit_sha', None)      # an open pull request's is GitHub's test merge
        if top.get('state'):
            (pr_c if kind == 'pull' else issue_c).append({'value': top, 'evidence': ev})
            pr_known = pr_known or kind == 'pull'
        for k in rest:
            if isinstance(f.get(k), dict):
                rest[k].append({'value': f[k], 'evidence': ev})
    out = {'pr': _latest(pr_c), 'issue': _latest(issue_c) if kind == 'issue' else None}
    for k, cands in rest.items():
        out[k] = _latest(cands)
    # what the rule needs to tell a fact from an absence: the card's Source, whether anything was
    # read for it, and whether a reading answered «which pull request» (``pr: None`` included)
    out['source'] = str((card or {}).get('source') or '').strip() or None
    out['read'] = bool(sources)
    out['pr_known'] = pr_known or out['pr'] is not None
    return out


# ── 3. the re-check's fresh read (GET only, declared in the connections file) ───────────────────
class ReadFault(Exception):
    """One card could not be read; the re-check skips it and says why."""


class PinFault(ReadFault):
    """The prod pin could not be read (its own reading, ``<connection>/prod-pin``): the card is left unread, and
    the failure is the pin's, not the card's GitHub read's."""


def _path(template: str, **values) -> str:
    for k, v in values.items():
        v = str(v)
        if not (_SHA.match(v) if k in ('sha', 'base', 'head') else _NAME.match(v)):
            raise ReadFault(f'{k} is not a plain name')
        template = template.replace('{' + k + '}', v)
    return template


_LINK_URL = re.compile(r'^https://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/(?:issues|pull|pulls)/(\d+)'
                       r'(?:[/?#].*)?$')
_LINK_SHORT = re.compile(r'^(?:github:)?([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)#(\d+)$')


def subject_of(link) -> str:
    """``github.com/<owner>/<repo>#<n>`` for a card's GitHub ``Source`` (the CRM's
    ``readings.subject``), or ''."""
    text = str(link or '').strip()
    m = _LINK_URL.match(text) or _LINK_SHORT.match(text)
    return f'github.com/{m.group(1).lower()}/{m.group(2).lower()}#{int(m.group(3))}' if m else ''


def parse_subject(subject: str):
    m = _SUBJECT.match(str(subject or ''))
    if not m:
        raise ReadFault('the card tracks no GitHub issue or pull request')
    return m.group(1), m.group(2), int(m.group(3))


def _contents_text(answer) -> str:
    if not isinstance(answer, dict) or answer.get('encoding') != 'base64':
        raise ReadFault('the pin file did not answer as file contents')
    return base64.b64decode(answer.get('content') or '').decode('utf-8', 'replace')


def pin_target(app_yaml: str) -> str:
    """The chart version the prod Application pins (``spec.source.targetRevision``)."""
    m = re.search(r'^\s*targetRevision:\s*["\']?([^\s"\']+)', app_yaml or '', re.M)
    if not m:
        raise ReadFault('the prod Application names no targetRevision')
    return m.group(1)


def channel_entries(channel_yaml: str) -> dict:
    """``{chart version: {entry_seq, source_sha}}`` of the channel file's publish ``entries``."""
    out, cur, section = {}, None, None
    for line in (channel_yaml or '').splitlines():
        if re.match(r'^[A-Za-z_]', line):
            section = line.split(':', 1)[0].strip()
            cur = None
            continue
        if section != 'entries':
            continue
        m = re.match(r'^- entry_seq:\s*(\d+)', line)
        if m:
            cur = {'entry_seq': int(m.group(1))}
            continue
        if cur is None:
            continue
        m = re.match(r'^\s+source_sha:\s*["\']?([0-9a-f]{7,64})', line)
        if m:
            cur['source_sha'] = m.group(1)
        m = re.match(r'^\s+version:\s*["\']?([^\s"\']+)', line)
        if m and cur.get('source_sha'):
            out[m.group(1)] = dict(cur)
    return out


def _contains(get, conn, spec, owner, repo, commit, tip) -> Optional[bool]:
    """Whether ``commit`` is an ancestor of (or is) ``tip`` in owner/repo; None when unanswerable."""
    try:
        ans = get(conn, _path(spec['compare'], owner=owner, repo=repo, base=commit, head=tip), {})
    except ReadFault:
        raise
    except Exception:  # noqa: BLE001 — an unknown commit (404) is "not contained", never a crash
        return False
    status = (ans or {}).get('status') if isinstance(ans, dict) else None
    return status in ('ahead', 'identical')


class PassCache:
    """What one re-check pass reads once for every card: the prod pin and the releases."""

    def __init__(self):
        self.pin = None
        self.pin_fault = None          # a pin read that failed this pass: read once, never retried per card
        self.releases = {}


def read_pin(get, conn, spec, cache: PassCache) -> dict:
    if cache.pin_fault is not None:
        raise PinFault(cache.pin_fault)
    if cache.pin is None:
        pin = spec.get('prod_pin') or {}
        params = {'ref': pin.get('ref') or 'main'}
        try:
            chart = pin_target(_contents_text(get(conn, pin['app'], params)))
            entry = channel_entries(_contents_text(get(conn, pin['channel'], params))).get(chart)
            if not entry:
                raise ReadFault(f'the channel names no publish entry for chart {chart}')
        except (ReadFault, SensorFault) as e:
            cache.pin_fault = str(e)
            raise PinFault(cache.pin_fault) from None
        cache.pin = {'chart': chart, **entry, 'repo': str(pin.get('repo') or '').lower(),
                     'pin_ref': f"{pin['app']}@{params['ref']}"}
    return cache.pin


def read_delivery(get: Callable, conn: dict, spec: dict, subject: str, source: str = '',
                  cache: Optional[PassCache] = None, pin_conn: Optional[dict] = None) -> dict:
    """The ``github/delivery`` reading's fields for one card: its issue (if it tracks one), its pull
    request (the card's own, or the newest one that references the issue), the head commit's check
    runs, the prod pin and the newest releases. GET only, through ``get(conn, path, params)``. The pin
    is read through ``pin_conn`` when the pin names its own connection (a private repo, its own key).
    A check-runs read the key may not make (403) is a missing fact: no ``checks``, never a stage."""
    cache = cache or PassCache()
    owner, repo, number = parse_subject(subject)
    fields: dict = {}
    pr_number = number
    kind = 'issue' if '/issues/' in str(source or '') else 'pull' if '/pull' in str(source or '') else ''
    if kind != 'pull':
        issue = get(conn, _path(spec['issue'], owner=owner, repo=repo, number=number), {})
        if not isinstance(issue, dict):
            raise ReadFault('the issue did not answer')
        if issue.get('pull_request'):
            kind = 'pull'
        else:
            kind = 'issue'
            fields['issue'] = {'url': issue.get('html_url'), 'state': issue.get('state'),
                               'state_reason': issue.get('state_reason'), 'closed_at': issue.get('closed_at')}
            pr_number = linked_pr(get(conn, _path(spec['timeline'], owner=owner, repo=repo, number=number),
                                      {'per_page': '100'}), owner, repo)
    if pr_number is None:
        fields['pr'] = None
        return fields
    pull = get(conn, _path(spec['pull'], owner=owner, repo=repo, number=pr_number), {})
    if not isinstance(pull, dict):
        raise ReadFault('the pull request did not answer')
    state = 'merged' if pull.get('merged_at') else pull.get('state')
    head = (pull.get('head') or {}).get('sha')
    merge = pull.get('merge_commit_sha') if pull.get('merged_at') else None
    fields['pr'] = {'url': pull.get('html_url'), 'number': pull.get('number'), 'state': state,
                    'draft': bool(pull.get('draft')), 'head_sha': head, 'merge_commit_sha': merge,
                    'merged_at': pull.get('merged_at'), 'updated_at': pull.get('updated_at'),
                    'base': (pull.get('base') or {}).get('ref'),
                    'default_branch': ((pull.get('base') or {}).get('repo') or {}).get('default_branch')}
    if state == 'open' and spec.get('files'):
        files = get(conn, _path(spec['files'], owner=owner, repo=repo, number=pr_number), {'per_page': '100'})
        if isinstance(files, list):            # anything else answers nothing about the files
            fields['pr']['design_doc'] = is_design_doc(files)
    if head:
        try:
            runs = get(conn, _path(spec['check_runs'], owner=owner, repo=repo, sha=head), {'per_page': '100'})
        except SensorFault as e:
            if e.status != 403:
                raise
        else:
            fields['checks'] = summarise_checks(runs, head)
    commits = [(c, via) for c, via in ((head, 'head'), (merge, 'merge')) if c]
    pin = read_pin(get, pin_conn or conn, spec, cache) if spec.get('prod_pin') else None
    if pin and pin['repo'] == f'{owner}/{repo}'.lower():
        found = None
        for commit, via in commits:
            if _contains(get, conn, spec, owner, repo, commit, pin['source_sha']):
                found = (commit, via)
                break
        commit, via = found or (merge or head, 'merge' if merge else 'head')
        fields['prod'] = {'contains': bool(found), 'chart': pin['chart'], 'entry_seq': pin['entry_seq'],
                          'source_sha': pin['source_sha'], 'commit': commit, 'via': via, 'pin_ref': pin['pin_ref']}
    if merge:
        key = f'{owner}/{repo}'.lower()
        if key not in cache.releases:
            rel = get(conn, _path(spec['releases'], owner=owner, repo=repo), {'per_page': '20'})
            tags = [x.get('tag_name') for x in (rel if isinstance(rel, list) else [])
                    if isinstance(x, dict) and not x.get('draft') and x.get('tag_name')]
            cache.releases[key] = tags[:RELEASES_CHECKED]
        tag, checked = None, 0
        for name in cache.releases[key]:          # newest first: walk back while it still contains it
            checked += 1
            if not _NAME.match(name) or not _contains_tag(get, conn, spec, owner, repo, merge, name):
                break
            tag = name
        fields['release'] = {'tag': tag, 'commit': merge, 'checked': checked}
    return fields


#: a bound on the result, not a filter: 50 hid every open PR past the 50th (Vexa-ai/vexa-platform#572 on 2 Nov, behind 38
#: open Vexa-ai/vexa ones), so a repository late in the list was never flagged
UNLINKED_MAX = 300
_ISSUE_REF = re.compile(r'(?:(?<![\w/])#|github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/issues/)(\d+)\b')


def _iso_ts(value) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def unlinked_prs(get: Callable, conn: dict, spec: dict, cache: PassCache, cards_for: Callable, now: float,
                 pin_conn: Optional[dict] = None) -> list:
    """Pull requests in the declared repos that no card tracks, as facts: every open one opened more than
    ``unlinked.open_after_h`` (24) hours ago, and every one merged in the last ``unlinked.merged_within_h`` (72),
    with ``in_prod`` (the prod pin contains its merge commit; None when the pin is unread or names another repo).
    A pull request is tracked when ``cards_for(link)`` answers a card for it or for an issue of its own repo
    its body names: the links the stage rule reads (a card's Source, an issue's linked pull request). The merged
    ones come first (at most ``UNLINKED_MAX`` in all), each with its author."""
    opts = spec.get('unlinked') or {}
    open_after = float(opts.get('open_after_h', 24)) * 3600
    merged_within = float(opts.get('merged_within_h', 72)) * 3600
    pulls = spec.get('pulls') or '/repos/{owner}/{repo}/pulls'
    merged_rows, open_rows = [], []
    for full in spec.get('repos') or []:
        owner, _, repo = str(full).partition('/')
        rows = []
        for params in ({'state': 'open', 'per_page': '100'},
                       {'state': 'closed', 'sort': 'updated', 'direction': 'desc', 'per_page': '50'}):
            got = get(conn, _path(pulls, owner=owner, repo=repo), params)
            rows += [p for p in (got if isinstance(got, list) else []) if isinstance(p, dict)]
        for p in rows:
            opened, merged = _iso_ts(p.get('created_at')), _iso_ts(p.get('merged_at'))
            if p.get('state') == 'open':
                if opened is None or now - opened < open_after:
                    continue
            elif merged is None or now - merged > merged_within:
                continue
            link = p.get('html_url') or ''
            refs = {int(m.group(3)) for m in _ISSUE_REF.finditer(str(p.get('body') or ''))
                    if not m.group(1) or (m.group(1).lower(), m.group(2).lower()) == (owner.lower(), repo.lower())}
            refs.discard(int(p.get('number') or 0))
            if cards_for(link) or any(cards_for(f'https://github.com/{owner}/{repo}/issues/{n}') for n in sorted(refs)):
                continue
            row = {'pr': link, 'repo': f'{owner}/{repo}'.lower(), 'number': p.get('number'),
                   'author': (p.get('user') or {}).get('login'),
                   'state': 'merged' if merged else 'open', 'opened_at': p.get('created_at'),
                   'merged_at': p.get('merged_at'), 'in_prod': None}
            if merged and spec.get('prod_pin'):
                try:
                    pin = read_pin(get, pin_conn or conn, spec, cache)
                except PinFault:
                    pin = None
                if pin and pin['repo'] == row['repo'] and p.get('merge_commit_sha'):
                    row['in_prod'] = bool(_contains(get, conn, spec, owner, repo, p['merge_commit_sha'],
                                                    pin['source_sha']))
            (merged_rows if merged else open_rows).append(row)
    return (merged_rows + open_rows)[:UNLINKED_MAX]


def _contains_tag(get, conn, spec, owner, repo, commit, tag) -> bool:
    try:
        ans = get(conn, spec['compare'].replace('{owner}', owner).replace('{repo}', repo)
                  .replace('{base}', commit).replace('{head}', tag), {})
    except Exception:  # noqa: BLE001
        return False
    return isinstance(ans, dict) and ans.get('status') in ('ahead', 'identical')


def is_design_doc(files) -> bool:
    """A pull request's changed files (``GET /pulls/{n}/files``) are a design document when there
    is at least one and every one is under ``DESIGN_DIRS``."""
    names = [str((f or {}).get('filename') or '') for f in files if isinstance(f, dict)]
    return bool(names) and all(n.startswith(DESIGN_DIRS) for n in names)


def linked_pr(timeline, owner: str, repo: str) -> Optional[int]:
    """The pull request an issue is delivered by: among the same repository's pull requests that
    reference it (timeline ``cross-referenced``), a merged one first, then an open one, the newest."""
    best = None
    for ev in timeline if isinstance(timeline, list) else []:
        if not isinstance(ev, dict) or ev.get('event') != 'cross-referenced':
            continue
        src = ((ev.get('source') or {}).get('issue') or {})
        if not isinstance(src.get('pull_request'), dict):
            continue
        full = str(((src.get('repository') or {}).get('full_name')) or '').lower()
        if full and full != f'{owner}/{repo}'.lower():
            continue
        merged = bool((src.get('pull_request') or {}).get('merged_at'))
        rank = (2 if merged else 1 if src.get('state') == 'open' else 0, int(src.get('number') or 0))
        if best is None or rank > best[0]:
            best = (rank, int(src.get('number') or 0))
    if best is None or best[0][0] == 0:
        return None
    return best[1]


def summarise_checks(answer, sha: str) -> dict:
    """The head commit's check runs as one status: ``failure`` if any failed, else ``pending`` while
    any runs, else ``success``; ``none`` when there are none."""
    runs = (answer or {}).get('check_runs') if isinstance(answer, dict) else None
    runs = [r for r in runs or [] if isinstance(r, dict)]
    running = sum(1 for r in runs if r.get('status') != 'completed')
    failed = sum(1 for r in runs if r.get('status') == 'completed' and r.get('conclusion') in FAILED)
    passed = len(runs) - running - failed
    status = 'none' if not runs else 'failure' if failed else 'pending' if running else 'success'
    return {'status': status, 'sha': sha, 'total': len(runs), 'running': running, 'failed': failed, 'passed': passed}


# ── the Flows claim on a card ────────────────────────────────────────────────────────────────────
#: The stage rule's own flows: their reactions never count as someone holding a card.
OWN_FLOWS = ('crm_card_stage', 'crm_card_stage_recheck')


def claim_of(card: dict, now: float) -> Optional[dict]:
    """The live claim on the card, as the CRM keeps it (founder 1 Nov 2026 «A»: a claim is a card fact,
    ``Holder`` / ``HoldUntil``, written by ``/card/claim``; the CRM answers ``hold`` only while it is live).
    The host is what follows ``@`` in the holder (``s1-worker@bbb`` → ``bbb``)."""
    hold = (card or {}).get('hold')
    if not isinstance(hold, dict) or not hold.get('holder'):
        return None
    holder = str(hold['holder'])
    return {'holder': holder, 'host': holder.split('@', 1)[1].split(':', 1)[0] if '@' in holder else None,
            'lease_until': hold.get('until'), 'read_at': now}
    for rid, flow, refs, lease, updated in rows:
        if flow in OWN_FLOWS:
            continue
        try:
            refs = json.loads(refs) if isinstance(refs, str) else (refs or {})
        except ValueError:
            continue
        if str((refs or {}).get('card') or '') != str(card_id):
            continue
        holder = None
        try:
            got = db.execute("SELECT actor, created_at FROM signal WHERE reaction_id = :r AND kind = 'claim' "
                             "ORDER BY created_at DESC LIMIT 1", {'r': rid})
            if got:
                holder = got[0][0]
        except Exception:  # noqa: BLE001 — an engine without the claim verb
            holder = None
        holder = holder or f'flows:{flow}'
        return {'holder': holder, 'host': holder.split('@', 1)[1] if '@' in holder else None,
                'since': updated, 'lease_until': lease, 'read_at': now, 'reaction': rid}
    return None
