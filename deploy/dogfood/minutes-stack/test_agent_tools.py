import json
import unittest

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


if __name__ == '__main__':
    unittest.main()
