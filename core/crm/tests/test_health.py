from fastapi.testclient import TestClient
from crm.api import create_app


def test_liveness_does_not_require_a_user_credential():
    def identity(): raise AssertionError('Liveness must not authenticate')
    response=TestClient(create_app(None,identity,"one")).get('/health')
    assert response.status_code==200
    assert response.json()=={'status':'ok','domain':'crm'}
