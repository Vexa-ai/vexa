"""The desk's limits are company policy (`_global/POLICIES.md`), read through agent-api's door.

A company may only narrow them: a value above the default, or one that is not a whole number, is the
default and is named in a log line. No agent door, an unreachable one, or a file that is not there
changes nothing. The answer is cached for 30 seconds.
"""
import ast
import logging
import pathlib

import httpx
import pytest

from crm import policies
from crm.policies import DEFAULTS, Policies, front_matter, resolve

ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED = ROOT / 'behavior' / 'global' / 'POLICIES.md'
FLOWS_POLICIES = ROOT / 'core' / 'flows' / 'src' / 'flows_steps' / 'policies.py'


def page(**rows):
    return '---\nkind: policies\n' + ''.join(f'{k}: {v}\n' for k, v in rows.items()) + '---\n# body\n'


def test_the_defaults_are_todays_constants():
    assert DEFAULTS == {'desk_placements_max': 50, 'card_proposals_pending_max': 5,
                        'card_proposals_per_agent_per_day': 20, 'card_proposal_ttl_days': 7}


def test_no_policy_is_the_defaults():
    for raw in (None, '', '# no front matter\n', '---\nkind: policies\n'):
        limits, problems = resolve(raw)
        assert limits == DEFAULTS and problems == []


def test_narrowed_values_are_applied():
    limits, problems = resolve(page(desk_placements_max=10, card_proposals_pending_max=2,
                                    card_proposals_per_agent_per_day=3, card_proposal_ttl_days=1))
    assert limits == {'desk_placements_max': 10, 'card_proposals_pending_max': 2,
                      'card_proposals_per_agent_per_day': 3, 'card_proposal_ttl_days': 1}
    assert problems == []


def test_zero_switches_proposals_off_but_a_ttl_is_at_least_a_day():
    limits, problems = resolve(page(card_proposals_pending_max=0, card_proposal_ttl_days=0))
    assert limits['card_proposals_pending_max'] == 0
    assert limits['card_proposal_ttl_days'] == 7 and len(problems) == 1


def test_a_larger_value_is_refused_and_named():
    limits, problems = resolve(page(desk_placements_max=500, card_proposal_ttl_days=30))
    assert limits == DEFAULTS
    assert len(problems) == 2
    assert any('desk_placements_max' in p and '500' in p for p in problems)


@pytest.mark.parametrize('value', ['ten', '-3', '2.5', 'forever', 'on'])
def test_an_unreadable_value_is_the_default(value):
    limits, problems = resolve(page(card_proposals_pending_max=value))
    assert limits == DEFAULTS and len(problems) == 1 and 'card_proposals_pending_max' in problems[0]


def test_an_empty_row_is_not_an_answer():
    limits, problems = resolve(page(desk_placements_max=''))
    assert limits == DEFAULTS and problems == []


# ── the door ────────────────────────────────────────────────────────────────────────────────────
class Door:
    """agent-api's GET /api/workspace/file, counted."""
    def __init__(self, content=None, status=200, fail=False):
        self.content, self.status, self.fail, self.calls, self.seen = content, status, fail, 0, []

    def __call__(self, request):
        self.calls += 1
        self.seen.append(request)
        if self.fail:
            raise httpx.ConnectError('down', request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={'detail': 'not found'})
        return httpx.Response(200, json={'path': 'POLICIES.md', 'content': self.content})


class Now:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def policy(door, now=None, url='http://agent-api:8100'):
    return Policies(url, 's3cret', transport=httpx.MockTransport(door), now=now or Now())


def test_reads_global_through_agent_apis_door_with_the_internal_secret():
    door = Door(page(desk_placements_max=12))
    assert policy(door).limits()['desk_placements_max'] == 12
    req = door.seen[0]
    assert req.url.path == '/api/workspace/file'
    assert req.url.params['path'] == 'POLICIES.md' and req.url.params['slug'] == '_global'
    assert req.headers['x-internal-secret'] == 's3cret' and req.headers['x-user-id'] == 'crm'


def test_no_agent_door_is_the_defaults_and_asks_nobody():
    door = Door(page(desk_placements_max=12))
    assert policy(door, url='').limits() == DEFAULTS and door.calls == 0


def test_an_unreachable_door_is_the_defaults():
    assert policy(Door(fail=True)).limits() == DEFAULTS


def test_a_missing_file_is_the_defaults():
    assert policy(Door(status=404)).limits() == DEFAULTS


def test_the_answer_is_cached_for_thirty_seconds():
    door, now = Door(page(card_proposals_pending_max=2)), Now()
    p = policy(door, now)
    assert p.limits()['card_proposals_pending_max'] == 2
    door.content = page(card_proposals_pending_max=3)
    now.t += 29
    assert p.limits()['card_proposals_pending_max'] == 2 and door.calls == 1
    now.t += 2
    assert p.limits()['card_proposals_pending_max'] == 3 and door.calls == 2


def test_a_failed_read_is_cached_too():
    door, now = Door(fail=True), Now()
    p = policy(door, now)
    p.limits(); p.limits()
    assert door.calls == 1


def test_a_refused_value_is_named_in_a_log_line(caplog):
    with caplog.at_level(logging.WARNING, logger='crm.policies'):
        assert policy(Door(page(desk_placements_max=999))).limits() == DEFAULTS
    assert any('desk_placements_max' in r.getMessage() for r in caplog.records)


# ── parity with the registry ────────────────────────────────────────────────────────────────────
def test_the_seed_carries_these_keys_at_these_defaults():
    if not SEED.is_file():
        pytest.skip('no seed in this tree')
    declared = front_matter(SEED.read_text(encoding='utf-8'))
    for key, default in DEFAULTS.items():
        assert declared.get(key) == str(default), f'{SEED} says {key}: {declared.get(key)!r}'


def test_the_front_matter_parse_is_flows_own():
    """A copy (a domain may not import another), held to flows' `front_matter` by running both."""
    if not FLOWS_POLICIES.is_file():
        pytest.skip('flows is not in this tree')
    tree = ast.parse(FLOWS_POLICIES.read_text(encoding='utf-8'))
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'front_matter')
            or (isinstance(n, ast.AnnAssign | ast.Assign) and '_FENCE' in ast.unparse(n).split('=')[0])]
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(FLOWS_POLICIES), 'exec'), ns)  # noqa: S102
    samples = [None, '', 'x', '---\na: 1\n', page(desk_placements_max=3), '---\n# c\nA: "q"\nb:x\n---\n',
               SEED.read_text(encoding='utf-8') if SEED.is_file() else '']
    for raw in samples:
        assert front_matter(raw) == ns['front_matter'](raw)
