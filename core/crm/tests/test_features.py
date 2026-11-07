"""The CRM is an unproven feature: a release install (``VEXA_FEATURES`` empty) does not serve it.

Off, crm-api answers every route but liveness with 403 ``feature_disabled`` and its MCP manifest
lists no tool; ``all`` or ``crm`` serves it as before. The parse is agent-api's
(``core/agent/control_plane/features.py`` ``install_defaults``), copied — read here by path only.
"""
import pathlib
import re

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from crm import features
from crm.api import create_app

AGENT_FEATURES = pathlib.Path(__file__).resolve().parents[2] / 'agent' / 'control_plane' / 'features.py'


def _client():
    def identity(request): raise HTTPException(401, 'no credential in this test')
    return TestClient(create_app(None, identity, 'one'))


@pytest.mark.parametrize('raw,on', [
    (None, False), ('', False), (' ', False), ('all', True), ('ALL', True), ('crm', True),
    ('calls, crm', True), ('calls', False), ('all,-crm', False), ('-crm,all', True),
    ('nonsense', False), ('+crm', True),
])
def test_the_parse_is_agent_apis(raw, on):
    env = {} if raw is None else {'VEXA_FEATURES': raw}
    assert features.switched_on('crm', env) is on


def test_parity_with_the_registry_it_copies():
    if not AGENT_FEATURES.is_file():
        pytest.skip('agent-api is not in this tree')
    text = AGENT_FEATURES.read_text()
    assert re.search(r'^ENV = "VEXA_FEATURES"$', text, re.M)
    assert re.search(r'^REASON = "feature_disabled"$', text, re.M)
    block = re.search(r'"crm": \{(.*?)\n    \}', text, re.S)
    assert block and '"scope": "install"' in block.group(1)
    assert features.SUMMARY in block.group(1)


@pytest.mark.parametrize('method,path', [
    ('post', '/describe'), ('post', '/search'), ('post', '/read'), ('post', '/change'),
    ('post', '/internal/references'), ('post', '/internal/accounts'),
])
def test_off_by_default_every_route_refuses(monkeypatch, method, path):
    monkeypatch.delenv('VEXA_FEATURES', raising=False)
    r = getattr(_client(), method)(path, json={})
    assert r.status_code == 403
    assert r.json() == {'detail': features.refusal('crm')}
    assert r.json()['detail']['reason'] == 'feature_disabled'


def test_off_the_manifest_lists_no_tool_and_liveness_answers(monkeypatch):
    monkeypatch.setenv('VEXA_FEATURES', '')
    client = _client()
    assert client.get('/health').status_code == 200
    manifest = client.get('/.well-known/mcp-tools.json').json()
    assert manifest['domain'] == 'crm' and manifest['tools'] == []
    assert client.get('/openapi.json').status_code == 200


@pytest.mark.parametrize('raw,tools', [('all', 16), ('crm', 8), ('crm,desktop', 13), ('crm,desktop,desktop_cards', 16), ('crm,desktop_cards', 8)])
def test_switched_on_it_is_served(monkeypatch, raw, tools):
    monkeypatch.setenv('VEXA_FEATURES', raw)
    client = _client()
    # the desk's three desktop_* tools ride the `desktop` feature, which needs `crm`
    assert len(client.get('/.well-known/mcp-tools.json').json()['tools']) == tools
    # past the gate: the route runs and asks identity, which this client refuses to answer
    assert client.post('/describe', json={}).status_code == 401


def test_all_but_crm_is_off(monkeypatch):
    monkeypatch.setenv('VEXA_FEATURES', 'all,-crm')
    assert _client().post('/describe', json={}).status_code == 403


@pytest.mark.parametrize('raw,on', [
    (None, False), ('', False), ('all', True), ('desktop', True), ('crm', False), ('all,-desktop', False),
    ('crm, desktop', True), ('-desktop,all', True),
])
def test_desktop_is_parsed_the_same_way(raw, on):
    env = {} if raw is None else {'VEXA_FEATURES': raw}
    assert features.switched_on('desktop', env) is on


@pytest.mark.parametrize('raw,served', [('all', True), ('crm,desktop', True), ('desktop', False), ('crm', False),
                                        ('all,-crm', False), ('all,-desktop', False)])
def test_the_desk_needs_crm_and_desktop(raw, served):
    assert features.desk_served({'VEXA_FEATURES': raw}) is served


def test_desk_parity_with_the_registry_it_copies():
    if not AGENT_FEATURES.is_file():
        pytest.skip('agent-api is not in this tree')
    block = re.search(r'"desktop": \{(.*?)\n    \}', AGENT_FEATURES.read_text(), re.S)
    if block is None:
        pytest.skip('agent-api has not registered desktop in this tree yet')
    assert '"scope": "install"' in block.group(1)
    assert features.DESKTOP_SUMMARY in block.group(1)       # one refusal text whichever service answers


@pytest.mark.parametrize('raw', ['', 'all,-desktop', 'crm'])
def test_desk_routes_refuse_for_desktop_and_liveness_still_answers(monkeypatch, raw):
    monkeypatch.setenv('VEXA_FEATURES', raw)
    client = _client()
    r = client.post('/desk/read', json={})
    assert r.status_code == 403 and r.json()['detail']['feature'] == 'desktop'
    assert client.get('/health').status_code == 200
