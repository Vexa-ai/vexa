"""Control MCP keeps the verified subject and delegation ceiling on CRM calls."""
import json
from types import SimpleNamespace
import pytest
from crm_tools import register_crm_tools

class MCP:
    def __init__(self): self.tools = {}
    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register

@pytest.fixture
def surface():
    state = SimpleNamespace(scope=None, calls=[], status=200)
    mcp = MCP()
    def http(*args, **kw):
        state.calls.append((args, kw))
        return state.status, {'id': 'permitted-record'}
    register_crm_tools(mcp, base_url='http://crm:8300', tenant_id='tenant-a', subject=lambda: 'verified-7',
                       scope=lambda: state.scope, user_key=lambda uid: 'key-for-'+uid,
                       http=http, guard=lambda f: f)
    return state, mcp.tools

def test_disabled_registers_nothing():
    mcp = MCP()
    register_crm_tools(mcp, base_url='', subject=None, scope=None, user_key=None, http=None, guard=None)
    assert not mcp.tools

def test_tools_forward_subject_and_revision(surface):
    state, tools = surface
    assert len(tools) == 7
    tools['crm_change']('propose','Meeting evidence',record_id='r1',expected_revision=4,fields={'Name':'new'})
    args, kw = state.calls[0]
    assert args == ('POST','http://crm:8300/change')
    assert kw['headers'] == {'X-API-Key':'key-for-verified-7','X-CRM-Tenant':'tenant-a'}
    assert kw['body']['expected_revision'] == 4
    assert 'tenant_id' not in kw['body']

@pytest.mark.parametrize('scope',[{}, {'regime':'autonomous','workspaces':['tenant-a']},
                                  {'regime':'human','workspaces':['tenant-a']},
                                  {'crm_tenants':'tenant-a'}])
def test_workspace_delegation_cannot_expand_into_crm(surface, scope):
    state, tools = surface; state.scope = scope
    result = json.loads(tools['crm_read']('r1'))
    assert result['refused'] == 'out_of_scope'
    assert not state.calls

@pytest.mark.parametrize('scope',[{'regime':'human','workspaces':'*'},
                                  {'regime':'autonomous','workspaces':[], 'crm_tenants':['tenant-a']}])
def test_explicit_ceiling_forwards_to_service_authorization(surface,scope):
    state, tools = surface; state.scope = scope
    state.status = 403
    assert json.loads(tools['crm_read']('r1'))['status'] == 403
    assert len(state.calls) == 1

def test_outage_is_explicit(surface):
    state, tools = surface; state.status = 0
    assert json.loads(tools['crm_read']('r1'))['status'] == 503

def test_agent_tools_cannot_select_a_tenant(surface):
    import inspect
    _, tools = surface
    for tool in tools.values():
        assert 'tenant_id' not in inspect.signature(tool).parameters
    with pytest.raises(TypeError):
        tools['crm_describe'](tenant_id='other')

def test_enabled_crm_requires_explicit_instance_binding(monkeypatch):
    monkeypatch.delenv('CRM_TENANT_ID',raising=False)
    with pytest.raises(ValueError,match='CRM_TENANT_ID'):
        register_crm_tools(MCP(),base_url='http://crm',subject=None,scope=None,user_key=None,http=None,guard=None)

def test_card_configuration_forwards_version_and_layout(surface):
    state, tools=surface
    layout={'title_field':'Name','sections':[]}
    tools['crm_configure']('Account',layout,3,'Simplify layout')
    args,kw=state.calls[0]
    assert args==('POST','http://crm:8300/configure')
    assert kw['body']=={'object_type':'Account','layout':layout,'expected_version':3,'reason':'Simplify layout'}
