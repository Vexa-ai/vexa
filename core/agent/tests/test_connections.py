"""Connection tool front door: subject comes from auth; consent is not an agent route."""
import pytest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from control_plane.routers import connections

def subject(request):
    actor=request.headers.get('x-user-id')
    if not actor: raise HTTPException(401)
    return actor

def test_request_identity_and_closed_provider_schema():
    app=FastAPI(); app.include_router(connections.build(subject_of=subject))
    c=TestClient(app)
    with patch.object(connections,'call_broker',side_effect=[{'connections':[]},{'connection_id':'a'*32,'status':'awaiting_user'}]) as broker:
        assert c.post('/api/connections/request',json={'provider':'google_email'}).status_code==401
        for invalid in ({'provider':'evil'}, {'provider':'google_email','actor':'victim'}, {'provider':'google_email','token':'secret'}):
            assert c.post('/api/connections/request',headers={'x-user-id':'u1','x-vexa-identity':'signed-u1'},json=invalid).status_code==422
        r=c.post('/api/connections/request',headers={'x-user-id':'u1','x-vexa-identity':'signed-u1'},json={'provider':'google_email'})
        assert r.status_code==200 and r.json()['status']=='awaiting_user'
        assert broker.call_args.args[0]=='u1'
        assert 'authorize_url' not in r.json()
        assert 'setup_path' not in r.json()
        assert r.json()['ui_action']=='open_connections'
    assert c.post('/api/connections/'+'a'*32+'/authorize').status_code==404

def test_unconfigured_broker_fails_closed(monkeypatch):
    monkeypatch.delenv('VEXA_CONNECTIONS_BROKER_URL',raising=False)
    with pytest.raises(HTTPException) as exc: connections.call_broker('u1','GET','/api/connections',identity='signed-u1')
    assert exc.value.status_code==503


def test_existing_ready_connection_is_reused():
    app=FastAPI(); app.include_router(connections.build(subject_of=subject))
    with patch.object(connections,'call_broker',side_effect=[{'connections':[{'id':'a'*32,'provider':'google_calendar','status':'ready'}]},{'connection_id':'a'*32,'status':'ready'}]) as broker:
        r=TestClient(app).post('/api/connections/request',headers={'x-user-id':'u1','x-vexa-identity':'signed-u1'},json={'provider':'google_calendar'})
        assert r.json()['status']=='ready'
        assert broker.call_count==2

def test_pending_request_signals_panel_again_without_duplicate_connection():
    app=FastAPI(); app.include_router(connections.build(subject_of=subject))
    with patch.object(connections,'call_broker',side_effect=[{'connections':[{'id':'a'*32,'provider':'google_email','status':'awaiting_user'}]}, {'connection_id':'a'*32,'status':'awaiting_user'}]) as broker:
        r=TestClient(app).post('/api/connections/request',headers={'x-user-id':'u1','x-vexa-identity':'signed-u1'},json={'provider':'google_email'})
        assert r.status_code==200
        assert broker.call_args.args==('u1','POST','/api/connections/'+'a'*32+'/request')

def test_read_selects_only_authenticated_owners_ready_provider():
    app=FastAPI();app.include_router(connections.build(subject_of=subject));client=TestClient(app)
    with patch.object(connections,'call_broker',side_effect=[{'connections':[{'id':'email','provider':'google_email','status':'ready'},{'id':'calendar','provider':'google_calendar','status':'ready'}]},{'messages':[]}]) as broker:
        r=client.post('/api/connections/read',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'action':'gmail.search','query':'fixture'})
        assert r.status_code==200
        assert broker.call_args.args[0:3]==('owner','POST','/api/connections/email/read')
    with patch.object(connections,'call_broker',return_value={'connections':[]}):
        assert client.post('/api/connections/read',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'action':'gmail.search'}).status_code==409

def test_additional_account_does_not_reuse_existing_connection():
    app=FastAPI();app.include_router(connections.build(subject_of=subject))
    with patch.object(connections,'call_broker',side_effect=[{'connections':[{'id':'a'*32,'provider':'google_email','status':'ready','label':'Work'}]},{'connection_id':'b'*32,'status':'awaiting_user'}]) as broker:
        r=TestClient(app).post('/api/connections/request',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'provider':'google_email','new_account':True,'label':'Personal'})
        assert r.json()['connection_id']=='b'*32
        assert broker.call_args.args==('owner','POST','/api/setup',{'provider':'google_email','label':'Personal'})

def test_multiple_accounts_require_selection_and_cannot_select_foreign_account():
    app=FastAPI();app.include_router(connections.build(subject_of=subject));client=TestClient(app)
    rows={'connections':[{'id':c*32,'provider':'google_email','status':'ready'} for c in ('a','b')]}
    with patch.object(connections,'call_broker',return_value=rows):
        for cid in ('','c'*32):
            assert client.post('/api/connections/read',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'action':'gmail.search','connection_id':cid}).status_code==409
    with patch.object(connections,'call_broker',side_effect=[rows,{'messages':[]}]) as broker:
        assert client.post('/api/connections/read',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'action':'gmail.search','connection_id':'b'*32}).status_code==200
        assert broker.call_args.args[2]=='/api/connections/'+'b'*32+'/read'
        assert 'connection_id' not in broker.call_args.args[3]


def test_custom_service_requires_name_before_selecting_any_saved_secret():
    app=FastAPI();app.include_router(connections.build(subject_of=subject))
    with patch.object(connections,'call_broker') as broker:
        r=TestClient(app).post('/api/connections/request',headers={'x-user-id':'owner','x-vexa-identity':'signed-owner'},json={'provider':'custom_secret'})
        assert r.status_code==422
        broker.assert_not_called()
