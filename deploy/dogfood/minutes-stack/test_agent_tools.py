import contextvars
import json
import unittest

import agent_mcp
import agent_tools


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def add(fn):
            self.tools[fn.__name__] = fn
            return fn
        return add


def registered(answers):
    calls = []

    def call(method, path, body=None, timeout=60):
        calls.append((method, path, body))
        return answers.get((method, path), (200, {}))

    mcp = Registry()
    agent_tools.register(mcp, call=call, guard=lambda fn: fn)
    return mcp.tools, calls, call


class AgentToolsOnTheRig(unittest.TestCase):
    def test_every_product_tool_is_registered_by_name(self):
        tools, _, _ = registered({})
        self.assertEqual(set(tools), {
            'connection_request', 'connections_status', 'gmail_search', 'gmail_read', 'gmail_thread',
            'calendar_events', 'mail_inbox', 'mail_read', 'gmail_draft_create', 'onboarding_research',
            'secret_service_call', 'current_time', 'timezone_set', 'chat_name'})

    def test_tools_call_the_typed_agent_api_routes(self):
        tools, calls, _ = registered({('POST', '/api/connections/gmail/search'): (200, {'messages': []})})
        self.assertEqual(json.loads(tools['gmail_search']('from:a', 2)), {'messages': []})
        self.assertEqual(calls[-1], ('POST', '/api/connections/gmail/search',
                                     {'query': 'from:a', 'limit': 2, 'connection_id': '', 'page_token': ''}))
        tools['chat_name']('s1', 'Connect calendar')
        self.assertEqual(calls[-1], ('POST', '/api/chat/name/agent', {'session': 's1', 'title': 'Connect calendar'}))
        tools['timezone_set']('Europe/Lisbon')
        self.assertEqual(calls[-1], ('PUT', '/api/time/zone', {'timezone': 'Europe/Lisbon'}))

    def test_a_refusal_from_agent_api_reaches_the_agent_as_a_refusal(self):
        refusal = (403, {'detail': {'status': 'refused', 'reason': 'human_session_required'}})
        tools, _, _ = registered({('POST', '/api/connections/gmail/read'): refusal})
        out = json.loads(tools['gmail_read']('m1'))
        self.assertEqual(out['status'], 'refused')
        self.assertEqual(out['reason'], 'human_session_required')

    def test_whats_waiting_carries_the_clock(self):
        _, _, call = registered({('GET', '/api/time'): (200, {'timezone': 'Europe/Lisbon'})})
        waiting = agent_tools.with_time_context(lambda: json.dumps({'waiting': []}), call)
        self.assertEqual(waiting.__name__, 'whats_waiting')
        self.assertEqual(json.loads(waiting())['time_context'], {'timezone': 'Europe/Lisbon'})


class Rig:
    """The slice of the rig `agent_call` uses: the caller's credential, the gateway, two doors."""
    DELEGATION_PREFIX = 'vxd_'
    GATEWAY = 'http://gateway:8000'
    AGENT_API = 'http://agent-api:8100'

    def __init__(self, token):
        self.CALL_TOKEN = contextvars.ContextVar('t', default=None)
        self.CALL_TOKEN.set(token)
        self.sent = []

    def me(self):
        return '7'

    def _http(self, method, url, headers=None, body=None, timeout=40):
        self.sent.append(('http', method, url, dict(headers or {}), body))
        return 200, {}

    def _gw_http(self, uid, method, path, body=None, timeout=40):
        self.sent.append(('gateway-as-person', uid, method, path, body))
        return 200, {}


class AgentCallGoesThroughTheGateway(unittest.TestCase):
    """The credential broker acts for a person only on the gateway's signature, so the rig reaches
    agent-api through the gateway — never over the internal tier, never with X-User-Id."""

    def test_a_worker_presents_its_own_delegation_token(self):
        rig = Rig('vxd_header.payload.signature')
        agent_mcp.agent_call(rig)('POST', '/api/connections/gmail/search', {'query': 'x'})
        kind, method, url, headers, body = rig.sent[-1]
        self.assertEqual((kind, method, url), ('http', 'POST', 'http://gateway:8000/agent/connections/gmail/search'))
        self.assertEqual(headers, {'X-API-Key': 'vxd_header.payload.signature'})
        self.assertNotIn('X-User-Id', headers)
        self.assertNotIn('X-Internal-Secret', headers)

    def test_a_person_goes_as_their_own_gateway_key(self):
        rig = Rig('vxa_mcp_durable')
        agent_mcp.agent_call(rig)('GET', '/api/connections')
        self.assertEqual(rig.sent[-1], ('gateway-as-person', '7', 'GET', '/agent/connections', None))

    def test_only_agent_api_routes(self):
        with self.assertRaises(ValueError):
            agent_mcp.agent_call(Rig(''))('GET', '/internal/anything')


if __name__ == '__main__':
    unittest.main()
