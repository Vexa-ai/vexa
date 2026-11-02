"""A software card's stage from facts (flows_defs/card_stage.py, the crm pack's stage flows).

The pure rule ``stage(readings, flows_claim) -> (stage, evidence)`` on fixtures for every
transition and every revert; the stored readings it reads; the re-check's GET-only fresh read (the
pull request, its check runs, the prod pin in vexa-stations, the newest releases); the two steps
writing through the CRM's stage route only when the stage differs. Offline: no network, no CRM.
"""
import base64
import json
from types import SimpleNamespace

import pytest

from fixtures import rig
from flows import Done, NotPresent, StepError, admit
from flows_defs import card_stage as cs
from flows_defs.crm import DUE_ARM_FLOW, RECHECK_CRON, RECHECK_FLOW, STAGE_FLOW, build

T = 1_793_500_000.0
HEAD, HEAD2, MERGE = 'abc1234abc1234abc1234abc1234abc1234abcd', 'def5678def5678def5678def5678def5678defa', \
    '9999999999999999999999999999999999999999'
PIN_SHA = '090be9bce9f1a868a5aebff393d1183343a324c4'
PR_URL = 'https://github.com/Vexa-ai/vexa/pull/1962'
ISSUE_URL = 'https://github.com/Vexa-ai/vexa/issues/1900'


def ev(source='github/delivery', at=T):
    return {'source': source, 'read_at': at}


def pr(state='open', draft=False, head=HEAD, merge=None, merged_at=None):
    return {'url': PR_URL, 'number': 1962, 'state': state, 'draft': draft, 'head_sha': head,
            'merge_commit_sha': merge, 'merged_at': merged_at, 'evidence': ev()}


def prod(contains, commit=HEAD):
    return {'contains': contains, 'chart': '0.1.0-estate.20261031.core0134rc2.3', 'entry_seq': 58,
            'source_sha': PIN_SHA, 'commit': commit, 'via': 'head', 'evidence': ev()}


def release(tag, commit=MERGE):
    return {'tag': tag, 'commit': commit, 'checked': 1, 'evidence': ev()}


def issue(state='open', reason=None):
    return {'url': ISSUE_URL, 'state': state, 'state_reason': reason, 'evidence': ev()}


CLAIM = {'holder': 's1-worker@bbb', 'host': 'bbb', 'since': T - 600, 'lease_until': T + 3600, 'read_at': T}
MERGED = pr('merged', merge=MERGE, merged_at='2026-11-01T10:00:00Z')


# ── the pure rule: every transition ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize('readings, claim, want, because', [
    ({}, None, 'Idea', 'no pull request and nobody holds the card'),
    ({'issue': issue()}, None, 'Idea', 'no pull request'),
    ({'issue': issue()}, CLAIM, 'Code', 'held by s1-worker@bbb'),
    ({'pr': {**pr(), 'design_doc': True}}, None, 'Design', 'is an open design document'),
    ({'pr': {**pr(draft=True), 'design_doc': True}}, CLAIM, 'Design', 'design document'),
    ({'pr': {**pr(), 'design_doc': False}}, CLAIM, 'Review', 'open for review'),
    ({'pr': {**MERGED, 'design_doc': True}}, None, 'Done', 'was merged'),
    ({'pr': pr(draft=True)}, None, 'Code', '#1962 is a draft'),
    ({'pr': pr()}, None, 'Review', '#1962 is open for review at abc1234a'),
    ({'pr': pr(), 'prod': prod(False)}, None, 'Review', 'not in the prod pin'),
    ({'pr': pr(), 'prod': prod(True)}, None, 'On prod', 'contains head abc1234a'),
    ({'pr': pr(draft=True), 'prod': prod(True)}, None, 'On prod', 'the prod pin'),
    ({'pr': MERGED}, None, 'Done', '#1962 was merged at 2026-11-01T10:00:00Z as 99999999'),
    # a release is a tag, not a stage (founder 1 Nov 2026): merged is Done, released or not, issue open or not
    ({'pr': MERGED, 'release': release(None)}, None, 'Done', 'was merged'),
    ({'pr': MERGED, 'issue': issue(), 'release': release('v0.13.4')}, None, 'Done', 'was merged'),
    ({'pr': MERGED, 'release': release('v0.13.4')}, None, 'Done', 'was merged'),
    ({'issue': issue('closed', 'not_planned')}, None, 'Dropped', 'the issue was closed as not planned'),
    ({'issue': issue('closed', 'duplicate')}, CLAIM, 'Dropped', 'closed as duplicate'),
    ({'issue': issue('closed', 'completed')}, None, 'Done', 'closed as completed, with no pull request'),
])
def test_every_transition(readings, claim, want, because):
    got, evidence = cs.stage(readings, claim)
    assert got == want and because in evidence['because'], evidence['because']
    assert evidence['stage'] == want and isinstance(evidence['facts'], list)


def test_the_rule_is_pure_and_names_its_facts_with_their_sources():
    readings = {'pr': pr(), 'prod': prod(True), 'checks': {'status': 'pending', 'sha': HEAD, 'running': 2,
                                                          'evidence': ev(at=T + 1)}}
    a, b = cs.stage(readings, CLAIM), cs.stage(json.loads(json.dumps(readings)), dict(CLAIM))
    assert a == b
    facts = {f['fact']: f for f in a[1]['facts']}
    assert set(facts) == {'pr', 'prod', 'checks', 'claim'}
    assert facts['prod']['source'] == 'github/delivery' and facts['prod']['read_at'] == T
    assert facts['prod']['value']['source_sha'] == PIN_SHA and facts['checks']['read_at'] == T + 1
    assert facts['claim']['value']['host'] == 'bbb'
    assert [x['kind'] for x in a[1]['activity']] == ['agent', 'ci']          # what is happening now


# ── reverts ──────────────────────────────────────────────────────────────────────────────────────
def test_a_pull_request_closed_after_review_goes_back():
    assert cs.stage({'pr': pr()}, None)[0] == 'Review'
    closed = pr('closed')
    got, evidence = cs.stage({'pr': closed}, None)
    assert got == 'Idea' and evidence['because'] == '#1962 was closed without a merge'
    assert cs.stage({'pr': closed}, CLAIM)[0] == 'Code'                       # someone still holds it


def test_a_commit_dropped_from_the_pin_goes_back_to_review():
    assert cs.stage({'pr': pr(), 'prod': prod(True)}, None)[0] == 'On prod'
    assert cs.stage({'pr': pr(), 'prod': prod(False)}, None)[0] == 'Review'          # rolled back
    # a new push the pin was not built from: the prod fact about the old head does not count
    assert cs.stage({'pr': pr(head=HEAD2), 'prod': prod(True, commit=HEAD)}, None)[0] == 'Review'


def test_a_merge_is_never_undone_by_the_pin():
    assert cs.stage({'pr': MERGED, 'prod': prod(False, commit=MERGE)}, None)[0] == 'Done'


def test_merged_into_another_branch_is_not_done():
    """seq61 (2 Nov): Vexa-ai/vexa#2006 merged into codex/v0.13.4 read Done; Done is merged into main (founder Q3 «A»)."""
    codex = {**MERGED, 'base': 'codex/v0.13.4', 'default_branch': 'main'}
    got, ev_ = cs.stage({'pr': codex}, None)
    assert (got, ev_['because']) == ('Review', '#1962 was merged into codex/v0.13.4, not main')
    assert cs.stage({'pr': codex, 'prod': prod(True, commit=MERGE)}, None)[0] == 'On prod'
    assert cs.stage({'pr': {**MERGED, 'base': 'main', 'default_branch': 'main'}}, None)[0] == 'Done'
    assert cs.stage({'pr': MERGED}, None)[0] == 'Done'          # a reading from before base was read: unchanged


def test_a_release_fact_about_another_commit_does_not_count():
    assert cs.stage({'pr': MERGED, 'release': release('v0.13.4', commit=HEAD2)}, None)[0] == 'Done'


# ── the card's stored readings ───────────────────────────────────────────────────────────────────
def test_readings_from_a_card_take_each_fact_from_its_latest_source():
    card = {'id': 'c1', 'state': 'Review', 'source': PR_URL, 'reading': {'sources': {
        'github/vexa-pulls': {'fields': {'state': 'merged', 'head_sha': HEAD, 'merge_commit_sha': MERGE,
                                         'merged_at': '2026-11-01T10:00:00Z'}, 'evidence': ev('github/vexa-pulls', T + 60)},
        'github/delivery': {'fields': {'pr': {'state': 'open', 'head_sha': HEAD, 'draft': False},
                                       'prod': {'contains': True, 'commit': HEAD, 'source_sha': PIN_SHA}},
                            'evidence': ev('github/delivery', T)}}}}
    r = cs.readings_from_card(card)
    assert r['pr']['state'] == 'merged' and r['pr']['evidence']['source'] == 'github/vexa-pulls'
    assert r['prod']['contains'] is True and r['issue'] is None
    assert cs.stage(r, None)[0] == 'Done'


def test_a_single_legacy_reading_and_an_issue_card():
    legacy = {'source': PR_URL, 'reading': {'fields': {'state': 'open', 'head_sha': HEAD}, 'evidence': ev('github/vexa-pulls')}}
    assert cs.stage(cs.readings_from_card(legacy), None)[0] == 'Review'
    open_pr = {'pr': {'state': 'open', 'head_sha': HEAD}, 'issue': {'state': 'open'}}
    card = {'source': ISSUE_URL, 'reading': {'sources': {
        'github/vexa-issues': {'fields': {'state': 'open', 'title': 'x'}, 'evidence': ev('github/vexa-issues', T + 5)},
        'github/delivery': {'fields': open_pr, 'evidence': ev()}}}}
    r = cs.readings_from_card(card)
    assert r['issue']['state'] == 'open' and r['pr']['head_sha'] == HEAD
    assert cs.stage(r, None)[0] == 'Review'
    card['reading']['sources']['github/vexa-issues'] = {
        'fields': {'state': 'closed', 'state_reason': 'not_planned'}, 'evidence': ev('github/vexa-issues', T + 9)}
    assert cs.stage(cs.readings_from_card(card), None)[0] == 'Dropped'


# ── the re-check's fresh read ────────────────────────────────────────────────────────────────────
APP = """spec:
  source:
    repoURL: channel.vexa.ai/vexa/channel/vexa-internal/charts
    chart: vexa-platform
    targetRevision: 0.1.0-estate.20261031.core0134rc2.3
"""
CHANNEL = f"""channel: vexa-internal
current:
  entry_seq: 58
  source_sha: 1111111111111111111111111111111111111111
  chart:
    version: 0.1.0-estate.20261031.core0134rc2.3
entries:
- entry_seq: 57
  release: 0.13.4-estate-20261031
  source_sha: 2eaca7f0368982f2dfeb6cf99889caf548edd3ec
  chart:
    version: 0.1.0-estate.20261031.core0134rc2.2
- entry_seq: 58
  release: 0.13.4-estate-20261031
  source_sha: {PIN_SHA}
  chart:
    version: 0.1.0-estate.20261031.core0134rc2.3
pins:
  vexa-prod: 0.1.0-estate.20261009.dashflows.1
"""
SPEC = json.load(open(cs.__file__.replace('flows_defs/card_stage.py', 'flows_sensors/connections.github.example.json')))['delivery']


def b64(text):
    return {'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}


def test_the_pin_is_read_from_the_application_and_the_channel():
    assert cs.pin_target(APP) == '0.1.0-estate.20261031.core0134rc2.3'
    entries = cs.channel_entries(CHANNEL)
    assert entries['0.1.0-estate.20261031.core0134rc2.3'] == {'entry_seq': 58, 'source_sha': PIN_SHA}
    assert entries['0.1.0-estate.20261031.core0134rc2.2']['entry_seq'] == 57 and len(entries) == 2
    with pytest.raises(cs.ReadFault):
        cs.pin_target('spec: {}')


def github(answers):
    calls = []

    def get(conn, path, params):
        calls.append(path)
        for prefix, answer in answers.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer(path) if callable(answer) else answer
        raise AssertionError(f'unexpected GET {path}')
    return get, calls


def base_answers(pull, compare, files=()):
    return {
        '/repos/vexa-ai/vexa/pulls/1962/files': [{'filename': f} for f in files] or [{'filename': 'core/x.py'}],
        '/repos/vexa-ai/vexa/pulls/1962': pull,
        f'/repos/vexa-ai/vexa/commits/{HEAD}/check-runs': {'check_runs': [
            {'status': 'completed', 'conclusion': 'success'}, {'status': 'in_progress', 'conclusion': None}]},
        '/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/apps/': b64(APP),
        '/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/channel.yaml': b64(CHANNEL),
        '/repos/vexa-ai/vexa/compare/': compare,
        '/repos/vexa-ai/vexa/releases': [{'tag_name': 'v0.13.5', 'draft': False}, {'tag_name': 'v0.13.4', 'draft': False},
                                         {'tag_name': 'v0.14.0-draft', 'draft': True}],
    }


def test_an_open_pull_request_in_the_pin_reads_on_prod():
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': False, 'head': {'sha': HEAD},
            'merge_commit_sha': 'feedfeedfeedfeedfeedfeedfeedfeedfeedfeed', 'merged_at': None, 'updated_at': '2026-11-01T09:00:00Z'}
    get, calls = github(base_answers(pull, lambda p: {'status': 'ahead'} if p.endswith(f'{HEAD}...{PIN_SHA}') else {'status': 'diverged'}))
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL)
    assert fields['pr']['state'] == 'open' and fields['pr']['merge_commit_sha'] is None   # GitHub's test merge dropped
    assert fields['checks'] == {'status': 'pending', 'sha': HEAD, 'total': 2, 'running': 1, 'failed': 0, 'passed': 1}
    assert fields['prod']['contains'] is True and fields['prod']['entry_seq'] == 58 and fields['prod']['via'] == 'head'
    assert 'release' not in fields                                       # not merged: no release fact
    assert all(not c.startswith('/repos/vexa-ai/vexa/issues') for c in calls)   # a pull request link reads no issue
    assert cs.stage({k: {**v, 'evidence': ev()} for k, v in fields.items()}, None)[0] == 'On prod'


def test_a_merged_pull_request_the_newest_release_contains_reads_done():
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'closed', 'draft': False, 'head': {'sha': HEAD},
            'merge_commit_sha': MERGE, 'merged_at': '2026-11-01T10:00:00Z'}

    def compare(p):
        if p.endswith(f'{MERGE}...v0.13.5'):
            return {'status': 'ahead'}
        if p.endswith(f'{MERGE}...v0.13.4'):
            return {'status': 'behind'}
        return {'status': 'ahead'} if p.endswith(f'{HEAD}...{PIN_SHA}') else {'status': 'diverged'}
    get, _ = github(base_answers(pull, compare))
    cache = cs.PassCache()
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL, cache)
    assert fields['release'] == {'tag': 'v0.13.5', 'commit': MERGE, 'checked': 2}
    assert fields['prod']['contains'] is True
    assert cs.stage({k: {**v, 'evidence': ev()} for k, v in fields.items()}, None)[0] == 'Done'
    # the pin and the release list are read once per pass
    get2, calls2 = github(base_answers(pull, compare))
    cs.read_delivery(get2, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL, cache)
    assert not any('vexa-stations' in c or c.endswith('/releases') for c in calls2)


def test_an_issue_card_finds_the_pull_request_that_references_it():
    timeline = [
        {'event': 'labeled'},
        {'event': 'cross-referenced', 'source': {'issue': {'number': 1970, 'state': 'closed', 'pull_request': {},
                                                           'repository': {'full_name': 'Vexa-ai/vexa'}}}},
        {'event': 'cross-referenced', 'source': {'issue': {'number': 1975, 'state': 'open', 'pull_request': {},
                                                           'repository': {'full_name': 'Vexa-ai/vexa'}}}},
        {'event': 'cross-referenced', 'source': {'issue': {'number': 77, 'state': 'open', 'pull_request': {},
                                                           'repository': {'full_name': 'someone/fork'}}}},
        {'event': 'cross-referenced', 'source': {'issue': {'number': 1980, 'state': 'open'}}},     # an issue, not a PR
    ]
    assert cs.linked_pr(timeline, 'vexa-ai', 'vexa') == 1975
    assert cs.linked_pr(timeline[:2], 'vexa-ai', 'vexa') is None        # only a closed, unmerged one
    pull = {'html_url': 'https://github.com/Vexa-ai/vexa/pull/1975', 'number': 1975, 'state': 'open', 'draft': True,
            'head': {'sha': HEAD}, 'merged_at': None}
    answers = base_answers(pull, {'status': 'diverged'})
    answers = {'/repos/vexa-ai/vexa/issues/1900/timeline': timeline,
               '/repos/vexa-ai/vexa/issues/1900': {'html_url': ISSUE_URL, 'state': 'open', 'state_reason': None},
               '/repos/vexa-ai/vexa/pulls/1975': pull, **answers}
    get, _ = github(answers)
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1900', ISSUE_URL)
    assert fields['issue']['state'] == 'open' and fields['pr']['number'] == 1975 and fields['prod']['contains'] is False
    card = {'source': ISSUE_URL, 'reading': {'sources': {'github/delivery': {'fields': fields, 'evidence': ev()}}}}
    assert cs.stage(cs.readings_from_card(card), None)[0] == 'Code'      # a draft


def test_a_card_in_another_repository_has_no_prod_fact():
    pull = {'html_url': 'https://github.com/Vexa-ai/vexa-platform/pull/566', 'number': 566, 'state': 'open',
            'head': {'sha': HEAD}, 'merged_at': None}
    get, calls = github({'/repos/vexa-ai/vexa-platform/pulls/566': pull,
                         f'/repos/vexa-ai/vexa-platform/commits/{HEAD}/check-runs': {'check_runs': []},
                         '/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/apps/': b64(APP),
                         '/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/channel.yaml': b64(CHANNEL)})
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa-platform#566',
                              'https://github.com/Vexa-ai/vexa-platform/pull/566')
    assert 'prod' not in fields and fields['checks']['status'] == 'none'


def test_paths_take_only_plain_names():
    with pytest.raises(cs.ReadFault):
        cs.parse_subject('github.com/a/b')
    with pytest.raises(cs.ReadFault):
        cs._path(SPEC['check_runs'], owner='a', repo='b', sha='../../admin')
    assert cs.subject_of('Vexa-ai/vexa#1962') == cs.subject_of(PR_URL) == 'github.com/vexa-ai/vexa#1962'
    assert cs.subject_of('https://example.com/x') == ''


def test_failed_checks_win_over_running_ones():
    runs = {'check_runs': [{'status': 'completed', 'conclusion': 'failure'}, {'status': 'queued'}]}
    assert cs.summarise_checks(runs, HEAD)['status'] == 'failure'
    assert cs.summarise_checks({'check_runs': [{'status': 'completed', 'conclusion': 'skipped'}]}, HEAD)['status'] == 'success'


# ── the claim: a card fact (founder 1 Nov 2026 «A») ─────────────────────────────────────────────
def test_the_cards_live_hold_is_its_claim():
    got = cs.claim_of({'id': 'c1', 'hold': {'holder': 's1-worker@bbb:~/ci/s1', 'until': '2026-11-01T12:00:00+00:00'}}, T)
    assert got == {'holder': 's1-worker@bbb:~/ci/s1', 'host': 'bbb', 'lease_until': '2026-11-01T12:00:00+00:00', 'read_at': T}
    assert cs.claim_of({'id': 'c2', 'hold': None}, T) is None          # nobody, or a lapsed claim (the CRM answers null)
    assert cs.claim_of({'id': 'c3'}, T) is None
    name, ev = cs.stage({}, got)
    assert name == 'Code' and any(f['source'] == 'card/claim' for f in ev['facts'])


# ── the steps ────────────────────────────────────────────────────────────────────────────────────
def _crm(monkeypatch, answers):
    import flows_steps.common as common
    sent = []
    monkeypatch.setenv('VEXA_FLOWS_CRM_API_URL', 'http://crm-api:8300')
    monkeypatch.setenv('VEXA_FLOWS_SERVICE_KEY', 'flows-service-key-crm')

    def http(method, url, headers, body, timeout=20):
        sent.append((url.replace('http://crm-api:8300', ''), body))
        return answers(url.replace('http://crm-api:8300', ''), body)
    monkeypatch.setattr(common, 'http', http)
    return sent


SOFT = {'id': 'c1', 'revision': 3, 'state': 'Idea', 'source': PR_URL,
        'reading': {'sources': {'github/vexa-pulls': {'fields': {'state': 'open', 'head_sha': HEAD},
                                                      'evidence': ev('github/vexa-pulls')}}}}


def test_both_stage_flows_are_registered():
    db, reg, clock, _ = rig()
    build(reg, db)
    assert {f.name for f in reg.match('crm.record.changed')} == {'crm_change_log', STAGE_FLOW, DUE_ARM_FLOW,
                                                                 'card_change_to_owner', 'card_heartbeat_arm'}
    sched = {f.name: f for f in reg.schedules()}
    assert sched[RECHECK_FLOW].cron == RECHECK_CRON
    reg.refresh_from_db(db)                                                  # a DB refresh never retires it
    assert RECHECK_FLOW in {f.name for f in reg.schedules()}
    refs = {'org_id': 't', 'record_id': 'c1', 'revision': 2, 'uid': '7'}
    assert admit(db, reg, clock, source_event_id='crm-c1-2', event_type='crm.record.changed', subject_refs=refs) == 5


def test_a_record_change_moves_a_software_card_once(monkeypatch):
    db, reg, clock, _ = rig()
    build(reg, db)
    step = reg.steps['card_stage_from_readings']
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [SOFT]}) if url == '/internal/cards/software'
                else (200, {'status': 'moved'}))
    out = step(SimpleNamespace(refs={'record_id': 'c1', 'revision': 3}))
    assert isinstance(out, Done) and out.result['stage'] == 'Review' and out.result['status'] == 'moved'
    assert sent[0] == ('/internal/cards/software', {'record': 'c1'})
    url, body = sent[1]
    assert url == '/internal/cards/stage' and body['stage'] == 'Review' and body['mode'] == 'event'
    assert body['because'].startswith('the pull request is open for review')
    assert body['facts'][0]['fact'] == 'pr' and body['facts'][0]['source'] == 'github/vexa-pulls'
    # already there: nothing is written
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [{**SOFT, 'state': 'Review'}]}))
    assert step(SimpleNamespace(refs={'record_id': 'c1'})).result['status'] == 'unchanged' and len(sent) == 1
    # not a software card: nothing is written
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': []}))
    assert step(SimpleNamespace(refs={'record_id': 'p1'})).result['status'] == 'not_software' and len(sent) == 1


def test_a_record_change_needs_a_record_and_a_crm(monkeypatch):
    db, reg, clock, _ = rig()
    build(reg, db)
    step = reg.steps['card_stage_from_readings']
    with pytest.raises(StepError):
        step(SimpleNamespace(refs={}))
    monkeypatch.delenv('VEXA_FLOWS_CRM_API_URL', raising=False)
    assert isinstance(step(SimpleNamespace(refs={'record_id': 'c1'})), NotPresent)
    _crm(monkeypatch, lambda url, body: (503, {}))
    with pytest.raises(StepError) as e:
        step(SimpleNamespace(refs={'record_id': 'c1'}))
    assert e.value.retryable is True


def test_the_recheck_reads_afresh_and_marks_a_correction(monkeypatch, tmp_path):
    from flows_sensors import reading as rk
    db, reg, clock, _ = rig()
    build(reg, db)
    on_prod = {**SOFT, 'state': 'On prod'}                       # the card says On prod …
    unread = {'id': 'c2', 'revision': 1, 'state': 'Idea', 'source': None, 'reading': None}
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [on_prod, unread]}) if url == '/internal/cards/software'
                else (200, {'status': 'corrected' if url == '/internal/cards/stage' else 'applied'}))
    cfg = json.load(open(cs.__file__.replace('flows_defs/card_stage.py', 'flows_sensors/connections.github.example.json')))
    monkeypatch.setattr(rk, 'load_config', lambda path='': cfg)
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': False, 'head': {'sha': HEAD}, 'merged_at': None}
    get, _ = github(base_answers(pull, {'status': 'diverged'}))  # … but the pin was rolled back
    monkeypatch.setattr(rk, 'http_get', get)
    out = reg.steps['recheck_card_stages'](SimpleNamespace(refs={'slot': T}))
    assert out.result['cards'] == 2 and out.result['unread'] == 1
    [corrected] = out.result['corrected']
    assert corrected['card'] == 'c1' and corrected['was'] == 'On prod' and corrected['stage'] == 'Review'
    writes = [(u, b) for u, b in sent if u != '/internal/cards/software']
    assert writes[0][0] == '/internal/records/reading' and writes[0][1]['source'] == 'github/delivery'
    assert writes[0][1]['fields']['prod']['contains'] is False
    assert writes[1][0] == '/internal/cards/stage' and writes[1][1]['mode'] == 'recheck' and writes[1][1]['stage'] == 'Review'
    assert len(writes) == 2                                     # c2 tracks nothing and stays an Idea


def test_the_recheck_without_a_connection_checks_what_the_cards_have(monkeypatch):
    from flows_sensors import reading as rk
    from flows_sensors.base import SensorFault
    db, reg, clock, _ = rig()
    build(reg, db)

    def no_config(path=''):
        raise SensorFault('no connections are configured on this deployment', retryable=False)
    monkeypatch.setattr(rk, 'load_config', no_config)
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [SOFT]}) if url == '/internal/cards/software'
                else (200, {'status': 'corrected'}))
    out = reg.steps['recheck_card_stages'](SimpleNamespace(refs={}))
    assert out.result['unread'] == 1 and out.result['corrected'][0]['stage'] == 'Review'
    assert [u for u, _ in sent] == ['/internal/cards/software', '/internal/cards/stage']


# ── absence is not a fact (founder, 1 Nov 2026) ──────────────────────────────────────────────────
def test_a_card_with_a_source_and_no_reading_gets_no_stage():
    card = {'id': 'c9', 'state': 'Review', 'source': 'Vexa-ai/vexa#1969', 'reading': None}
    r = cs.readings_from_card(card)
    assert r['source'] == 'Vexa-ai/vexa#1969' and r['read'] is False and r['pr_known'] is False
    got, evidence = cs.stage(r, None)
    assert got is None and evidence['stage'] is None and evidence['no_reading'] is True
    assert evidence['because'] == 'no reading yet for Vexa-ai/vexa#1969'
    # a claim is a fact about the hold, never about the pull request: still no stage
    assert cs.stage(r, CLAIM)[0] is None
    # the caller's reason for the missing reading is carried in words
    got, evidence = cs.stage({**r, 'unread': 'not read: no delivery connection is declared'}, None)
    assert got is None and evidence['because'] == ('no reading yet for Vexa-ai/vexa#1969 '
                                                   '(not read: no delivery connection is declared)')


def test_a_pure_idea_with_no_source_may_be_an_idea_or_code():
    r = cs.readings_from_card({'id': 'c8', 'state': 'Idea', 'source': None, 'reading': None})
    assert cs.stage(r, None)[0] == 'Idea'
    assert cs.stage(r, CLAIM)[0] == 'Code'


def test_code_on_a_claim_and_idea_need_a_reading_that_proves_there_is_no_pull_request():
    # the issue was read, but nothing said which pull request delivers it: no stage
    card = {'id': 'c7', 'source': ISSUE_URL, 'reading': {'sources': {
        'github/vexa-issues': {'fields': {'state': 'open', 'title': 'x'}, 'evidence': ev('github/vexa-issues')}}}}
    r = cs.readings_from_card(card)
    assert r['read'] is True and r['pr_known'] is False
    got, evidence = cs.stage(r, CLAIM)
    assert got is None and evidence['because'] == f'no reading yet of a pull request for {ISSUE_URL}'
    # the delivery reading answered «no pull request» (pr: None): now Code under a claim, else Idea
    card['reading']['sources']['github/delivery'] = {'fields': {'issue': {'state': 'open'}, 'pr': None},
                                                      'evidence': ev()}
    r = cs.readings_from_card(card)
    assert r['pr_known'] is True
    assert cs.stage(r, CLAIM)[0] == 'Code' and cs.stage(r, None)[0] == 'Idea'
    # facts that decide without a pull request still decide: a closed issue
    card['reading']['sources']['github/vexa-issues']['fields'] = {'state': 'closed', 'state_reason': 'not_planned'}
    card['reading']['sources']['github/vexa-issues']['evidence'] = ev('github/vexa-issues', T + 9)
    del card['reading']['sources']['github/delivery']
    assert cs.stage(cs.readings_from_card(card), None)[0] == 'Dropped'


def test_no_reading_keeps_the_state_and_tells_the_crm_once(monkeypatch):
    db, reg, clock, _ = rig()
    build(reg, db)
    step = reg.steps['card_stage_from_readings']
    bare = {'id': 'c1', 'revision': 3, 'state': 'Review', 'source': PR_URL, 'reading': None}
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [bare]}) if url == '/internal/cards/software'
                else (200, {'status': 'kept', 'stage': 'Review'}))
    out = step(SimpleNamespace(refs={'record_id': 'c1'})).result
    assert out['stage'] is None and out['status'] == 'kept' and out['kept'] == 'Review'
    url, body = sent[1]
    assert url == '/internal/cards/stage' and body['stage'] is None
    assert body['because'] == f'no reading yet for {PR_URL}'
    # the CRM already holds that evidence: nothing is written
    held = {**bare, 'stage_evidence': {'stage': None, 'no_reading': True, 'because': f'no reading yet for {PR_URL}'}}
    sent = _crm(monkeypatch, lambda url, body: (200, {'cards': [held]}))
    assert step(SimpleNamespace(refs={'record_id': 'c1'})).result['status'] == 'unchanged' and len(sent) == 1


def test_an_open_pull_request_that_changes_only_design_docs_reads_design():
    assert cs.is_design_doc([{'filename': 'docs/adr/0051-stages.md'}, {'filename': 'docs/design/stages.md'}])
    assert not cs.is_design_doc([{'filename': 'docs/adr/0051.md'}, {'filename': 'core/crm/x.py'}])
    assert not cs.is_design_doc([]) and not cs.is_design_doc([{'filename': 'docs/guide.md'}])
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': True, 'head': {'sha': HEAD}, 'merged_at': None}
    get, calls = github(base_answers(pull, {'status': 'diverged'}, files=('docs/adr/0051-stages.md',)))
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL)
    assert fields['pr']['design_doc'] is True and '/repos/vexa-ai/vexa/pulls/1962/files' in calls
    assert cs.stage({k: {**v, 'evidence': ev()} for k, v in fields.items()}, None)[0] == 'Design'
    get, _ = github(base_answers(pull, {'status': 'diverged'}))                # code changed: a draft is Code
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL)
    assert fields['pr']['design_doc'] is False
    assert cs.stage({k: {**v, 'evidence': ev()} for k, v in fields.items()}, None)[0] == 'Code'



def test_the_pin_reads_through_its_own_connection_and_a_refused_check_read_is_no_fact():
    from flows_sensors.base import SensorFault
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': False, 'head': {'sha': HEAD},
            'merge_commit_sha': None, 'merged_at': None}
    answers = base_answers(pull, lambda p: {'status': 'ahead'} if p.endswith(f'{HEAD}...{PIN_SHA}') else {'status': 'diverged'})
    answers[f'/repos/vexa-ai/vexa/commits/{HEAD}/check-runs'] = SensorFault('read failed (403)', retryable=False, status=403)
    seen = []
    get0, _ = github(answers)

    def get(conn, path, params):
        seen.append((conn.get('name'), path.split('/')[2]))
        return get0(conn, path, params)
    fields = cs.read_delivery(get, {'name': 'github'}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL,
                              pin_conn={'name': 'github-stations'})
    assert 'checks' not in fields and fields['prod']['contains'] is True
    assert {c for c, owner in seen if owner == 'DmitriyG228'} == {'github-stations'}
    assert {c for c, owner in seen if owner == 'vexa-ai'} == {'github'}
    answers[f'/repos/vexa-ai/vexa/commits/{HEAD}/check-runs'] = SensorFault('read failed (500)', status=500)
    get1, _ = github(answers)
    with pytest.raises(SensorFault):                                 # any other failure stays a failed read
        cs.read_delivery(get1, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL, pin_conn={})



def test_a_pin_that_cannot_be_read_fails_the_pin_reading_once_and_every_card_is_still_read(monkeypatch):
    from flows_sensors import reading as rk
    db, reg, clock, _ = rig()
    build(reg, db)
    other = {**SOFT, 'id': 'c3'}
    _crm(monkeypatch, lambda url, body: (200, {'cards': [SOFT, other]}) if url == '/internal/cards/software'
         else (200, {'status': 'kept'}))
    cfg = json.load(open(cs.__file__.replace('flows_defs/card_stage.py', 'flows_sensors/connections.github.example.json')))
    cfg['connections']['github-stations'] = dict(cfg['connections']['github'])
    cfg['delivery']['prod_pin']['connection'] = 'github-stations'
    monkeypatch.setattr(rk, 'load_config', lambda path='': cfg)
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': False, 'head': {'sha': HEAD}, 'merged_at': None}
    answers = base_answers(pull, {'status': 'diverged'})
    answers['/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/channel.yaml'] = b64('entries:\n')
    get, calls = github(answers)
    monkeypatch.setattr(rk, 'http_get', get)
    out = reg.steps['recheck_card_stages'](SimpleNamespace(refs={'slot': T}))
    assert out.result['unread'] == 0                                  # both cards are read; only prod is unknown …
    assert out.result['failed'] == {'github/delivery': 0, 'github-stations/prod-pin': 1}   # … the pin failed, once
    assert sum(1 for c in calls if c.endswith('channel.yaml')) == 1   # read once per pass, not per card



def test_no_link_and_a_repo_outside_the_declared_ones_are_no_failure_and_a_refusal_keeps_its_code(monkeypatch):
    from flows_sensors import reading as rk
    from flows_sensors.base import SensorFault
    db, reg, clock, _ = rig()
    build(reg, db)
    no_link = {'id': 'c4', 'revision': 1, 'state': 'Idea', 'source': None, 'reading': None}
    outside = {**SOFT, 'id': 'c5', 'source': 'https://github.com/DmitriyG228/biz/issues/7'}
    refused = {**SOFT, 'id': 'c6', 'source': 'https://github.com/Vexa-ai/vexa-platform/pull/9'}
    _crm(monkeypatch, lambda url, body: (200, {'cards': [no_link, outside, refused]}) if url == '/internal/cards/software'
         else (200, {'status': 'kept'}))
    cfg = json.load(open(cs.__file__.replace('flows_defs/card_stage.py', 'flows_sensors/connections.github.example.json')))
    cfg['delivery']['repos'] = ['Vexa-ai/vexa', 'Vexa-ai/vexa-platform']
    monkeypatch.setattr(rk, 'load_config', lambda path='': cfg)
    calls = []

    def get(conn, path, params):
        calls.append(path)
        raise SensorFault('read failed (404)', retryable=True, status=404)
    monkeypatch.setattr(rk, 'http_get', get)
    out = reg.steps['recheck_card_stages'](SimpleNamespace(refs={'slot': T}))
    assert out.result['unread'] == 3
    assert out.result['failed']['github/delivery'] == 1                 # only the real refusal
    assert out.result['refused'] == [{'card': 'c6', 'status': 404}]
    assert not any('DmitriyG228/biz' in c for c in calls)               # outside the declared repos: never asked



def _pulls(rows_open, rows_closed):
    def get(conn, path, params):
        if path == '/repos/Vexa-ai/vexa/pulls':
            return rows_open if params['state'] == 'open' else rows_closed
        if path.endswith('vexa-prod.yaml'):
            return b64('spec:\n  source:\n    targetRevision: 0.1.0-x\n')
        if path.endswith('channel.yaml'):
            return b64(f'entries:\n- entry_seq: 59\n  source_sha: {PIN_SHA}\n  version: 0.1.0-x\n')
        if '/compare/' in path:
            return {'status': 'ahead' if path.split('/compare/')[1].startswith(MERGE) else 'diverged'}
        raise AssertionError(path)
    return get


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def test_a_pull_request_merged_and_pinned_to_prod_with_no_card_is_a_fact():
    spec = {**SPEC, 'repos': ['Vexa-ai/vexa']}
    merged = {'html_url': 'https://github.com/Vexa-ai/vexa/pull/2003', 'number': 2003, 'state': 'closed',
              'created_at': _iso(T - 5 * 3600), 'merged_at': _iso(T - 3600), 'merge_commit_sha': MERGE, 'body': '',
              'user': {'login': 'DmitriyG228'}}
    tracked = {**merged, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2004', 'number': 2004, 'body': 'Closes #1990'}
    old = {**merged, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/1500', 'number': 1500, 'merged_at': _iso(T - 9 * 86400)}
    unmerged = {**merged, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2005', 'number': 2005, 'merged_at': None}
    cards = {'https://github.com/Vexa-ai/vexa/issues/1990': ['c9']}
    out = cs.unlinked_prs(_pulls([], [merged, tracked, old, unmerged]), {}, spec, cs.PassCache(),
                          lambda link: cards.get(link, []), T)
    assert out == [{'pr': 'https://github.com/Vexa-ai/vexa/pull/2003', 'repo': 'vexa-ai/vexa', 'number': 2003,
                    'author': 'DmitriyG228', 'state': 'merged', 'opened_at': merged['created_at'], 'merged_at': merged['merged_at'],
                    'in_prod': True}]


def test_an_open_pull_request_older_than_the_limit_with_no_card_is_a_fact():
    spec = {**SPEC, 'repos': ['Vexa-ai/vexa'], 'unlinked': {'open_after_h': 24}}
    stale = {'html_url': 'https://github.com/Vexa-ai/vexa/pull/2002', 'number': 2002, 'state': 'open',
             'created_at': _iso(T - 30 * 3600), 'merged_at': None, 'body': 'see #2002'}
    fresh = {**stale, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2007', 'number': 2007, 'created_at': _iso(T - 3600)}
    carded = {**stale, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/1962', 'number': 1962}
    other_repo_ref = {**stale, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/1963', 'number': 1963,
                      'body': 'github.com/DmitriyG228/biz/issues/7'}
    cards = {'https://github.com/Vexa-ai/vexa/pull/1962': ['c1'], 'https://github.com/Vexa-ai/vexa/issues/7': ['c2']}
    merged = {**stale, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2006', 'number': 2006, 'state': 'closed',
              'merged_at': _iso(T - 3600), 'merge_commit_sha': HEAD}
    out = cs.unlinked_prs(_pulls([stale, fresh, carded, other_repo_ref], [merged]), {}, spec, cs.PassCache(),
                          lambda link: cards.get(link, []), T)
    assert [(r['number'], r['state'], r['in_prod']) for r in out] == [(2006, 'merged', False), (2002, 'open', None),
                                                                       (1963, 'open', None)]   # merged first


CARD = '0218468b-8959-4cc7-909f-5b3f8d1ac93c'


def test_a_pull_request_that_names_its_card_is_tracked_and_declared():
    """card: <id> in the body, or the id in the branch, links the PR to its card (2 Nov: PRs carry card: <id>)."""
    spec = {**SPEC, 'repos': ['Vexa-ai/vexa']}
    base = {'state': 'open', 'created_at': _iso(T - 30 * 3600), 'merged_at': None, 'body': ''}
    body = {**base, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2101', 'number': 2101,
            'body': f'Fixes the balance.\n\ncard: {CARD.upper()}'}
    branch = {**base, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2102', 'number': 2102,
              'head': {'ref': f'fix/{CARD}-balance'}}
    fresh = {**body, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2103', 'number': 2103, 'created_at': _iso(T - 60)}
    plain = {**base, 'html_url': 'https://github.com/Vexa-ai/vexa/pull/2104', 'number': 2104, 'body': 'a card: maybe'}
    declared = {}
    out = cs.unlinked_prs(_pulls([body, branch, fresh, plain], []), {}, spec, cs.PassCache(), lambda link: [], T,
                          declared=declared)
    assert [r['number'] for r in out] == [2104]                                  # the three that name a card are tracked
    assert declared == {CARD: {'https://github.com/Vexa-ai/vexa/pull/2101', 'https://github.com/Vexa-ai/vexa/pull/2102',
                               'https://github.com/Vexa-ai/vexa/pull/2103'}}
    card = {'reading': {'sources': {cs.CARD_LINK_SOURCE: {'fields': {'prs': sorted(declared[CARD])}}}}}
    assert cs.declared_pr(card) == 'https://github.com/Vexa-ai/vexa/pull/2101'
    assert cs.declared_pr({}) == ''


def test_an_unreadable_pin_leaves_prod_unknown_and_the_card_read():
    """2 Nov: a stations pin past the channel's last entry left 49 of 50 cards unread; now only prod is unknown."""
    pull = {'html_url': PR_URL, 'number': 1962, 'state': 'open', 'draft': False, 'head': {'sha': HEAD},
            'merge_commit_sha': None, 'merged_at': None, 'base': {'ref': 'main', 'repo': {'default_branch': 'main'}}}
    answers = base_answers(pull, lambda p: {'status': 'ahead'})
    answers['/repos/DmitriyG228/vexa-stations/contents/channels/vexa-internal/channel.yaml'] = b64('channel: x\npublishes: []\n')
    get, _ = github(answers)
    fields = cs.read_delivery(get, {}, SPEC, 'github.com/vexa-ai/vexa#1962', PR_URL, cs.PassCache())
    assert 'prod' not in fields and fields['pr']['base'] == 'main' and fields['pr']['default_branch'] == 'main'
    assert cs.stage({k: {**v, 'evidence': ev()} for k, v in fields.items()}, None)[0] == 'Review'


def test_the_recheck_names_the_pull_requests_no_card_tracks(monkeypatch):
    from flows_sensors import reading as rk
    db, reg, clock, _ = rig()
    build(reg, db)
    _crm(monkeypatch, lambda url, body: (200, {'cards': []}) if url in ('/internal/cards/software', '/internal/cards/by-source')
         else (200, {'status': 'kept'}))
    cfg = json.load(open(cs.__file__.replace('flows_defs/card_stage.py', 'flows_sensors/connections.github.example.json')))
    cfg['delivery']['repos'] = ['Vexa-ai/vexa']
    monkeypatch.setattr(rk, 'load_config', lambda path='': cfg)
    import time as _t
    now = _t.time()
    merged = {'html_url': 'https://github.com/Vexa-ai/vexa/pull/2003', 'number': 2003, 'state': 'closed',
              'created_at': _iso(now - 7200), 'merged_at': _iso(now - 3600), 'merge_commit_sha': MERGE, 'body': ''}
    monkeypatch.setattr(rk, 'http_get', _pulls([], [merged]))
    out = reg.steps['recheck_card_stages'](SimpleNamespace(refs={'slot': T}))
    assert [(r['number'], r['in_prod']) for r in out.result['unlinked']] == [(2003, True)]
    assert out.result['failed'] == {'github/delivery': 0, 'github/prod-pin': 0}     # a fact, never a failure
