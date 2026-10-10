"""A provider link reaches the agent only as the broker's `web_url`, and every read tool says so.

The broker builds `web_url` (credential-broker.v1 `AccountReadResponse`) from the connected
account it recorded at consent. agent-api passes it through unchanged and never builds or rewrites
one; each account read tool's description, the MCP manifest's notes and the worker's per-turn
reference rules all carry the same rule, so the agent never constructs a provider URL itself."""
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from control_plane import broker_client
from control_plane.routers import connections
from worker import engine

AGENT = Path(__file__).resolve().parents[1]
READ_TOOLS = ('gmail_search', 'gmail_read', 'gmail_thread', 'calendar_events', 'mail_inbox', 'mail_read')
RULE = 'Link to a message or event only with its web_url; never construct a provider URL.'
MAIL_URL = 'https://mail.google.com/mail/u/robin.vale%2Bwork@example.test/#all/t1'
EVENT_URL = 'https://calendar.google.com/calendar/event?eid=ZXZ0MQ'


def _subject(request):
    actor = request.headers.get('x-user-id')
    if not actor:
        raise HTTPException(401)
    return actor


def _client():
    app = FastAPI()
    app.include_router(connections.build(subject_of=_subject))
    return TestClient(app)


def _broker(answers):
    """Stand in for the broker at the wire: agent-api's own call_broker parses each answer."""
    def request(**kw):
        return httpx.Response(200, json=answers[(kw['method'], kw['path'])])
    return patch.object(broker_client, 'request', side_effect=request)


HEADERS = {'x-user-id': 'owner', 'x-vexa-identity': 'signed-owner'}


@pytest.mark.parametrize('route,body,provider,answer,pick', [
    ('/api/connections/gmail/read', {'message_id': 'm1'}, 'google_email',
     {'operation_id': 'o', 'source': 'google_email', 'untrusted_content': True,
      'message': {'id': 'm1', 'threadId': 't1', 'web_url': MAIL_URL}}, lambda r: r['message']['web_url']),
    ('/api/connections/gmail/search', {'query': 'from:nora@example.test'}, 'google_email',
     {'operation_id': 'o', 'source': 'google_email', 'untrusted_content': True,
      'messages': [{'id': 'm1', 'threadId': 't1', 'web_url': MAIL_URL}]}, lambda r: r['messages'][0]['web_url']),
    ('/api/connections/gmail/thread', {'thread_id': 't1'}, 'google_email',
     {'operation_id': 'o', 'source': 'google_email', 'untrusted_content': True, 'thread_id': 't1',
      'messages': [{'id': 'm1', 'threadId': 't1', 'web_url': MAIL_URL}]}, lambda r: r['messages'][0]['web_url']),
    ('/api/connections/calendar/events', {'time_min': '2026-10-01T00:00:00Z', 'time_max': '2026-10-02T00:00:00Z'},
     'google_calendar', {'operation_id': 'o', 'source': 'google_calendar', 'untrusted_content': True,
                         'events': [{'id': 'e1', 'web_url': EVENT_URL}]}, lambda r: r['events'][0]['web_url']),
])
def test_agent_api_passes_the_brokers_web_url_through_unchanged(monkeypatch, route, body, provider, answer, pick):
    monkeypatch.setenv('VEXA_CONNECTIONS_BROKER_URL', 'http://broker.test')
    monkeypatch.setenv('VEXA_CONNECTIONS_AGENT_KEY_FILE', '/dev/null')
    answers = {('GET', '/api/connections'): {'connections': [{'id': 'c' * 32, 'provider': provider, 'status': 'ready'}]},
               ('POST', '/api/connections/' + 'c' * 32 + '/read'): answer}
    with _broker(answers) as sent:
        r = _client().post(route, headers=HEADERS, json=body)
    assert r.status_code == 200, r.text
    assert pick(r.json()) == pick(answer)
    # the request agent-api sends names no account: the broker's own metadata does
    read = [c.kwargs for c in sent.call_args_list if c.kwargs['method'] == 'POST'][0]
    assert 'account' not in read['payload']


def test_a_read_tool_cannot_be_given_an_account_for_the_link():
    with patch.object(connections, 'call_broker', side_effect=AssertionError('must not reach the broker')):
        r = _client().post('/api/connections/gmail/read', headers=HEADERS,
                           json={'message_id': 'm1', 'account': 'nora@example.test'})
    assert r.status_code == 422


def test_every_read_tool_description_forbids_constructing_a_provider_url():
    spec = json.loads((AGENT / 'mcp.tools.v1.openapi.json').read_text())
    manifest = {t['name']: t for t in json.loads((AGENT / 'mcp.tools.v1.json').read_text())['tools']}
    for name in READ_TOOLS:
        route = manifest[name]['route']
        assert RULE in spec['paths'][route['path']][route['method'].lower()]['description'], name
        assert RULE in manifest[name]['note'], name


def test_the_worker_reference_rules_forbid_constructing_a_provider_url():
    text = ' '.join(engine.kg_links_preamble().split())
    assert 'web_url' in text and 'never construct a provider URL' in text
