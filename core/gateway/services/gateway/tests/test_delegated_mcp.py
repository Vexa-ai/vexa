from fastapi.testclient import TestClient
from gateway import create_app
from conftest import FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY


def setup(enabled=True, status=200):
    downstream = FakeDownstream(status_code=status)
    app = create_app(FakeAuthorizer(), downstream, FakeRedis(),
                     agent_mcp_url='http://agent-mcp:8011' if enabled else '')
    return TestClient(app), downstream


def test_delegated_route_carries_token_without_authority_headers():
    client, downstream = setup()
    r = client.post('/mcp', headers={'Authorization': 'Bearer vxd_test',
        'X-User-Id': 'victim', 'X-Admin-API-Key': 'injected', 'Cookie': 'secret'}, json={})
    assert r.status_code == 200
    call = downstream.last
    assert call['url'] == 'http://agent-mcp:8011/mcp'
    assert call['headers']['authorization'] == 'Bearer vxd_test'
    assert not {'x-user-id','x-admin-api-key','cookie'} & call['headers'].keys()


def test_delegation_never_authorizes_rest_or_suffix():
    client, downstream = setup()
    for path in ('/bots', '/mcp/other'):
        assert client.get(path, headers={'Authorization': 'Bearer vxd_test'}).status_code == 401
    assert not downstream.last


def test_absent_agent_service_fails_closed():
    client, downstream = setup(False)
    assert client.post('/mcp', headers={'Authorization': 'Bearer vxd_test'}, json={}).status_code == 401
    assert not downstream.last


def test_upstream_auth_refusal_preserved():
    client, downstream = setup(status=401)
    assert client.post('/mcp', headers={'Authorization': 'Bearer vxd_bad'}, json={}).status_code == 401


def test_api_key_stays_on_meetings_assembly():
    client, downstream = setup()
    assert client.post('/mcp', headers={'X-API-Key': VALID_KEY}, json={}).status_code == 200
    assert downstream.last['url'] == 'http://mcp:8010/mcp'
