import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('connections_tools', Path(__file__).parents[1] / 'mcp/connections.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Registry:
    def __init__(self): self.tools = {}
    def tool(self):
        def add(fn):
            self.tools[fn.__name__] = fn
            return fn
        return add


def registry(scope, calls, body):
    mcp = Registry()
    def http(*args, **kwargs):
        calls.append(args)
        return 200, body
    module.register(mcp, http=http, subject=lambda: 'owner', guard=lambda fn: fn,
                    scope=lambda: scope, agent_api='http://agent-api')
    return mcp.tools


def test_request_bound_to_subject_without_secret_projection():
    calls = []
    tools = registry({'regime': 'human'}, calls, {'status':'awaiting_user', 'token':'hidden'})
    assert json.loads(tools['connection_request']('google_email')) == {'status':'awaiting_user'}
    assert calls[0][2] == {'X-User-Id':'owner'}


def test_autonomous_or_unknown_provider_cannot_request_consent():
    for scope, provider in [({'regime':'autonomous'}, 'google_email'), ({'regime':'human'}, 'unknown')]:
        calls = []
        tools = registry(scope, calls, {})
        assert json.loads(tools['connection_request'](provider))['status'] == 'refused'
        assert not calls


def test_status_returns_metadata_only():
    tools = registry(None, [], {'connections':[{'id':'id','status':'ready','access_token':'hidden'}]})
    assert json.loads(tools['connections_status']()) == {'connections':[{'id':'id','status':'ready'}]}

def test_account_reads_are_human_scoped_and_use_fixed_route():
    calls=[]
    tools=registry({'regime':'human'},calls,{'messages':[{'id':'fixture'}]})
    assert json.loads(tools['gmail_search']('from:fixture.test',2))['messages'][0]['id']=='fixture'
    assert calls[0][0:2]==('POST','http://agent-api/api/connections/read')
    assert calls[0][3]=={'action':'gmail.search','query':'from:fixture.test','limit':2,'connection_id':'','page_token':''}
    calls=[];tools=registry({'regime':'autonomous'},calls,{})
    assert json.loads(tools['gmail_read']('fixture'))['status']=='refused'
    assert not calls


def test_setup_proposal_forwarded_without_secret_value():
    calls=[]
    tools=registry({'regime':'human'},calls,{'connection_id':'fixture','status':'awaiting_user'})
    setup={'endpoint':'https://api.example.com/data','secret_label':'API key','fields':[]}
    tools['connection_request']('custom_secret','Example',setup=setup)
    assert calls[0][3]['setup']==setup


def test_service_validation_reason_is_not_collapsed_into_unavailable():
    mcp=Registry()
    module.register(mcp,http=lambda *args:(409,{'detail':'Service connection is not ready'}),subject=lambda:'owner',guard=lambda fn:fn,scope=lambda:{'regime':'human'},agent_api='http://agent-api')
    result=json.loads(mcp.tools['secret_service_call']('fixture'))
    assert result['http_status']==409
    assert result['reason']=='Service connection is not ready'


def test_read_validation_error_retains_fields_but_never_values():
    mcp=Registry()
    module.register(mcp,http=lambda *args:(422,{'detail':[{'loc':['body','limit'],'input':'PRIVATE'}]}),subject=lambda:'owner',guard=lambda fn:fn,scope=lambda:{'regime':'human'},agent_api='http://agent-api')
    result=mcp.tools['gmail_search']()
    assert 'limit' in result and 'PRIVATE' not in result


def test_account_error_preserves_actionable_reason():
    mcp=Registry()
    module.register(mcp,http=lambda *args:(409,{'detail':'Select one ready account'}),subject=lambda:'owner',guard=lambda fn:fn,scope=lambda:{'regime':'human'},agent_api='http://agent-api')
    result=json.loads(mcp.tools['gmail_search']())
    assert result['reason']=='Select one ready account'
    assert result['http_status']==409


def test_research_tool_binds_actor_and_refuses_nonhuman():
    calls=[]; tools=registry({'regime':'human'},calls,{'status':'not_started'})
    assert json.loads(tools['onboarding_research']('status'))['status']=='not_started'
    assert calls[0][1]=='http://agent-api/api/onboarding/research'
    assert calls[0][2]=={'X-User-Id':'owner'}
    calls=[];tools=registry({'regime':'autonomous'},calls,{})
    assert json.loads(tools['onboarding_research']('start', ['a'*32]))['status']=='refused'
    assert calls==[]
