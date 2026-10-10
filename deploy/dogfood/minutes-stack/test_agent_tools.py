import asyncio
import contextvars
import json
import unittest
from pathlib import Path

import agent_mcp
import agent_tools

PRODUCT_TOOLS = Path(__file__).resolve().parents[3] / 'core' / 'agent' / 'mcp.tools.v1.json'
FORWARD = agent_mcp.agent_forward(PRODUCT_TOOLS)


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def add(fn):
            self.tools[fn.__name__] = fn
            return fn
        return add


def registered(answers, tools_named=None):
    calls = []

    def call(method, path, body=None, timeout=60, tool=None):
        calls.append((method, path, body))
        if tools_named is not None:
            tools_named.append((tool, method, path, body))
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

    def test_each_tool_names_the_product_tool_bound_to_its_route(self):
        """A worker reaches these routes only as the gateway's MCP tools, so each call must name
        the product tool for its route, with arguments the product tool takes."""
        manifest = {t['name']: t for t in json.loads(PRODUCT_TOOLS.read_text())['tools']}
        named = []
        tools, _, call = registered({}, named)
        samples = {'connection_request': ('github',), 'gmail_read': ('m1',),
                   'gmail_thread': ('t1',), 'calendar_events': ('2026-01-01T00:00:00Z',
                   '2026-01-02T00:00:00Z'), 'mail_read': ('m1',), 'gmail_draft_create': (
                   'a@b.c', 's', 'b', 'r1'), 'onboarding_research': ('status',),
                   'secret_service_call': ('c1',), 'timezone_set': ('Europe/Lisbon',),
                   'chat_name': ('s1', 'A title')}
        for name, fn in tools.items():
            named.clear()
            fn(*samples.get(name, ()))
            tool, method, path, body = named[-1]
            self.assertEqual(tool, name)
            route = manifest[name]['route']
            self.assertEqual((method, path), (route['method'], route['path']), name)
            self.assertLessEqual(set(body or {}), set(manifest[name].get('arguments') or []), name)
        named.clear()
        agent_tools.with_time_context(lambda: '{}', call)()
        self.assertEqual(named[-1][0], 'current_time')

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


class Gateway:
    """A recording stand-in for the gateway's `/mcp`: answers the handshake, then `tools/call`."""

    def __init__(self, result=None, *, status=200, sse=False, rpc_error=None):
        self.sent = []
        self.result = result if result is not None else {
            'content': [{'type': 'text', 'text': json.dumps({'messages': []})}], 'isError': False}
        self.status, self.sse, self.rpc_error = status, sse, rpc_error

    def __call__(self, method, url, headers, payload, timeout):
        self.sent.append((method, url, dict(headers), payload))
        if method == 'DELETE':
            return 200, {}, ''
        rpc = (payload or {}).get('method')
        if rpc == 'notifications/initialized':
            return 202, {}, ''
        if self.status != 200:
            return self.status, {'content-type': 'application/json'}, json.dumps(
                {'detail': "a worker's delegation token is accepted on /mcp only"})
        if rpc == 'initialize':
            msg = {'jsonrpc': '2.0', 'id': payload['id'],
                   'result': {'protocolVersion': '2025-06-18', 'capabilities': {}}}
        elif self.rpc_error:
            msg = {'jsonrpc': '2.0', 'id': payload['id'], 'error': {'code': -32602,
                                                                   'message': self.rpc_error}}
        else:
            msg = {'jsonrpc': '2.0', 'id': payload['id'], 'result': self.result}
        hdrs = {'mcp-session-id': 'sess-1'}
        if self.sse:
            hdrs['content-type'] = 'text/event-stream'
            return 200, hdrs, 'event: message\ndata: ' + json.dumps(msg) + '\n\n'
        hdrs['content-type'] = 'application/json'
        return 200, hdrs, json.dumps(msg)

    def calls(self):
        return [(p or {}).get('method') for m, _, _, p in self.sent if m == 'POST']


def refusal(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


class AgentCallGoesThroughTheGateway(unittest.TestCase):
    """The credential broker acts for a person only on the gateway's signature, so the rig reaches
    agent-api through the gateway — never over the internal tier, never with X-User-Id. A worker's
    delegation token is admitted on the gateway's `/mcp` only, so a worker goes there."""

    def worker(self, gateway, tool='gmail_search', body=None):
        rig = Rig('vxd_header.payload.signature')
        out = agent_mcp.agent_call(rig, FORWARD, transport=gateway)(
            'POST', '/api/connections/gmail/search', body if body is not None else {'query': 'x'},
            tool=tool)
        return rig, out

    def test_a_worker_calls_the_gateway_mcp_tool_with_its_own_token(self):
        gw = Gateway()
        rig, out = self.worker(gw)
        self.assertEqual(out, (200, {'messages': []}))
        self.assertEqual(rig.sent, [], 'a worker reached a REST route')
        self.assertEqual(gw.calls(), ['initialize', 'notifications/initialized', 'tools/call'])
        for method, url, headers, payload in gw.sent:
            self.assertEqual(url, 'http://gateway:8000/mcp')
            self.assertEqual(headers['Authorization'], 'Bearer vxd_header.payload.signature')
            self.assertFalse({'X-User-Id', 'X-Internal-Secret', 'X-API-Key'} & set(headers))
        call = next(p for m, _, _, p in gw.sent if (p or {}).get('method') == 'tools/call')
        self.assertEqual(call['params'], {'name': 'gmail_search', 'arguments': {'query': 'x'}})
        self.assertEqual(gw.sent[-1][0], 'DELETE', 'the session was left open')
        self.assertEqual(gw.sent[-1][2]['Mcp-Session-Id'], 'sess-1')

    def test_a_streamed_answer_reads_the_same(self):
        _, out = self.worker(Gateway(sse=True))
        self.assertEqual(out, (200, {'messages': []}))

    def test_a_refusal_reaches_the_tool_as_a_refusal(self):
        inner = {'status': 'refused', 'reason': 'human_session_required',
                 'message': 'a person must be present'}
        text = 'human_session_required: a person must be present\n' + json.dumps(inner)
        _, (status, body) = self.worker(Gateway(refusal(text)))
        self.assertEqual((status, body), (403, {'detail': inner}))
        out = json.loads(agent_tools._error(status, body, 'fallback'))
        self.assertEqual((out['status'], out['reason']), ('refused', 'human_session_required'))

    def test_the_refusal_remedy_reaches_the_agent_through_the_gateway_render(self):
        """The product's `REFUSAL` (gateway-identity.v1) rendered by the gateway MCP and parsed here
        keeps `remedy` and `tell_your_person`, so the agent on this rig reads the same way through
        as it would on the product's own MCP."""
        errors = agent_mcp.load('product_tool_errors', PRODUCT_TOOLS.parents[2] / 'core' / 'meetings'
                                / 'services' / 'mcp' / 'src' / 'vexa_mcp' / 'tool_errors.py')
        token = agent_mcp.load('product_identity_token', PRODUCT_TOOLS.parents[2] / 'core' / 'gateway'
                               / 'contracts' / 'gateway-identity.v1' / 'identity_token.py')
        inner = token.REFUSAL
        text = errors.render_tool_error(403, json.dumps({'detail': inner}))
        _, (status, body) = self.worker(Gateway(refusal(text)))
        out = json.loads(agent_tools._error(status, body, 'fallback'))
        self.assertEqual(out['remedy'], 'ask_in_chat')
        self.assertEqual(out['tell_your_person'], inner['tell_your_person'])

    def test_an_http_status_in_the_refusal_is_kept(self):
        _, (status, body) = self.worker(Gateway(refusal('HTTP 404\n{"detail":"no such"}')))
        self.assertEqual((status, body), (404, {'detail': {'detail': 'no such'}}))

    def test_the_parser_reads_what_the_gateway_mcp_renders(self):
        """Round-trip through the product's own renderer (`vexa_mcp.tool_errors`), standing notices
        appended the way `vexa_mcp.notices` appends them."""
        errors = agent_mcp.load('product_tool_errors', PRODUCT_TOOLS.parents[2] / 'core' / 'meetings'
                                / 'services' / 'mcp' / 'src' / 'vexa_mcp' / 'tool_errors.py')
        inner = {'status': 'refused', 'reason': 'human_session_required', 'message': 'not now'}
        for upstream, want in (
                ((403, json.dumps({'detail': {'detail': inner}})), (403, inner)),
                ((404, json.dumps({'detail': 'Not Found'})), (404, 'Not Found')),
                ((422, json.dumps({'detail': {'code': 'bad', 'reason': 'x'}})),
                 (422, {'code': 'bad', 'reason': 'x'}))):
            text = errors.render_tool_error(*upstream)
            for shown in (text, text + '\n\nNOTICE: a standing notice'):
                self.assertEqual(agent_mcp._tool_answer(refusal(shown)), (want[0], {'detail': want[1]}))

    def test_a_refused_token_at_the_edge_is_the_edge_answer(self):
        gw = Gateway(status=403)
        _, (status, body) = self.worker(gw)
        self.assertEqual(status, 403)
        self.assertIn('/mcp only', body['detail'])
        self.assertEqual(gw.calls(), ['initialize'])

    def test_a_protocol_error_is_an_error(self):
        _, (status, body) = self.worker(Gateway(rpc_error='Unknown tool: nope'))
        self.assertEqual((status, body), (502, {'detail': 'Unknown tool: nope'}))

    def test_a_worker_call_must_name_its_tool(self):
        with self.assertRaises(ValueError):
            self.worker(Gateway(), tool=None)

    def test_a_call_that_came_back_through_the_gateway_is_refused(self):
        gw = Gateway()
        token = agent_mcp.INBOUND_HOP.set(agent_mcp.HOP)
        try:
            _, (status, _) = self.worker(gw)
        finally:
            agent_mcp.INBOUND_HOP.reset(token)
        self.assertEqual(status, 508)
        self.assertEqual(gw.sent, [])

    def test_the_hop_marker_is_read_off_the_inbound_request(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(agent_mcp.INBOUND_HOP.get())

        guarded = agent_mcp.hop_guard(app)
        for headers in ([(b'x-vexa-rig-hop', agent_mcp.HOP.encode())], []):
            asyncio.run(guarded({'type': 'http', 'headers': headers}, None, None))
        self.assertEqual(seen, [agent_mcp.HOP, ''])
        gw = Gateway()
        self.worker(gw)
        self.assertTrue(all(h[agent_mcp.HOP_HEADER] == agent_mcp.HOP for _, _, h, _ in gw.sent))

    def test_a_person_goes_as_their_own_gateway_key(self):
        rig = Rig('vxa_mcp_durable')
        agent_mcp.agent_call(rig, FORWARD, transport=Gateway())('GET', '/api/connections',
                                                       tool='connections_status')
        self.assertEqual(rig.sent[-1], ('gateway-as-person', '7', 'GET', '/agent/connections', None))

    def test_the_rest_path_is_the_declared_forward(self):
        """The person's path is the manifest's `forward`, never one the adapter spells."""
        self.assertEqual(FORWARD, ('/agent/', '/api/'))
        rig = Rig('vxa_mcp_durable')
        agent_mcp.agent_call(rig, ('/edge-x/', '/up-y/'))('GET', '/up-y/connections',
                                                           tool='connections_status')
        self.assertEqual(rig.sent[-1], ('gateway-as-person', '7', 'GET', '/edge-x/connections', None))
        with self.assertRaises(ValueError):
            agent_mcp.agent_call(rig, ('/edge-x/', '/up-y/'))('GET', '/api/connections')

    def test_a_manifest_without_a_forward_stops_the_boot(self):
        import tempfile
        for doc in ({}, {'forward': {'edge_prefix': '/agent/'}},
                    {'forward': {'edge_prefix': 'agent', 'upstream_prefix': '/api/'}}):
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
                json.dump(doc, f)
            with self.assertRaises(ValueError):
                agent_mcp.agent_forward(f.name)
            Path(f.name).unlink()

    def test_only_agent_api_routes(self):
        with self.assertRaises(ValueError):
            agent_mcp.agent_call(Rig(''), FORWARD)('GET', '/internal/anything')


if __name__ == '__main__':
    unittest.main()
