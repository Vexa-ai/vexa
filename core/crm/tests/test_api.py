import httpx
from fastapi.testclient import TestClient
from crm.api import Identity, create_app
from test_store import store, make

def client_for(store):
    def resolve(request):
        token=__import__('json').loads(request.content)['token']
        if token=='down': return httpx.Response(503)
        if token not in ['owner','reader','agent']: return httpx.Response(401)
        return httpx.Response(200,json={'user_id':token})
    identity=Identity('http://identity','internal-test-only',httpx.MockTransport(resolve))
    return TestClient(create_app(store,identity,"one"))

def test_auth_cannot_be_forged_and_outage_is_not_invalid_key(store):
    client=client_for(store)
    body={}
    assert client.post('/describe',json=body,headers={'X-User-Id':'owner'}).status_code==401
    assert client.post('/describe',json=body,headers={'X-API-Key':'wrong'}).status_code==401
    assert client.post('/describe',json=body,headers={'X-API-Key':'down'}).status_code==503
    assert client.post('/describe',json=body,headers={'Authorization':'Bearer owner'}).status_code==200

def test_api_mutations_conflicts_and_cross_tenant(store):
    client=client_for(store); hdr={'X-API-Key':'owner'}
    result=client.post('/change',headers=hdr,json={'action':'create','object_type':'Company','fields':{'name':'A'},'reason':'test','idempotency_key':'api-create-1'})
    assert result.status_code==200,result.text
    rid=result.json()['record_id']
    data={'action':'update','record_id':rid,'expected_revision':1,'fields':{'name':'B'},'reason':'test'}
    assert client.post('/change',headers=hdr,json=data).status_code==200
    assert client.post('/change',headers=hdr,json=data).status_code==409
    assert client.post('/read',headers=hdr,json={'tenant_id':'two','record_id':rid}).status_code==422
    assert client.post('/read',headers=hdr,json={'record_id':rid,'actor':'owner'}).status_code==422

def test_six_tools_match_openapi(store):
    client=client_for(store)
    manifest=client.get('/.well-known/mcp-tools.json').json()
    assert len(manifest['tools'])==6
    spec=client.get('/openapi.json').json()
    for tool in manifest['tools']:
        assert tool['auth']=='subject'
        op=spec['paths'][tool['route']['path']]['post']
        assert op['operationId']==tool['name']

def test_instance_binding_is_not_an_agent_argument(store):
    client=client_for(store)
    spec=client.get('/openapi.json').json()
    for model in spec['components']['schemas'].values():
        assert 'tenant_id' not in model.get('properties',{})
    for operation in ['describe','search','read','change','history','review']:
        assert client.post('/'+operation,headers={'X-API-Key':'owner'},json={'tenant_id':'two'}).status_code==422
    assert client.post('/describe',headers={'X-API-Key':'reader'},json={}).status_code==200

def test_control_binding_cannot_disagree_with_service(store):
    client=client_for(store)
    assert client.post('/describe',json={},headers={'X-API-Key':'owner','X-CRM-Tenant':'two'}).status_code==403
    assert client.post('/describe',json={},headers={'X-API-Key':'owner','X-CRM-Tenant':'one'}).status_code==200
