import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from control_plane.routers.chats import build

@pytest.fixture
def stack():
    return _Sessions()

@pytest.fixture
def client(stack):
    from collections import defaultdict
    deps=defaultdict(lambda: None)
    deps.update(sess=stack, subject_of=lambda req:req.headers['x-user-id'])
    app=FastAPI()
    app.include_router(build(**{k:deps[k] for k in [
        '_global_root','_meeting_note_recorder','_meeting_owner_lookup','_read_target',
        '_resolve_room','_scaffold_is_for','_scaffold_view','_schedule_source','dispatcher',
        'invocations_url','mindex','redis_url','scaffolds','scheduler','sess','settings',
        'stream_reader','subject_of','workspace_registry','wsr']}))
    return TestClient(app)

from control_plane.api_shared import _Sessions


def test_human_name_is_protected_and_scoped():
    sessions = _Sessions()
    sessions.upsert('a','chat', title='raw first prompt')
    assert sessions.name('a','chat','Connect personal calendar',human=False)
    assert sessions.name('a','chat','My calendar',human=True)
    assert not sessions.name('a','chat','Auto overwrite',human=False)
    assert not sessions.name('b','chat','Wrong owner',human=True)
    sessions.upsert('a','chat',title='old machinery')
    assert sessions.list('a')[0]['named_title']=='My calendar'
    assert sessions.list('a')[0]['name_source']=='human'


def test_order_is_per_person():
    sessions=_Sessions()
    assert sessions.rail_order('a',['b','a']) == ['b','a']
    assert sessions.rail_order('a')==['b','a']
    assert sessions.rail_order('b')==[]


def test_routes_validate_and_isolate(client, stack):
    # The app keeps a durable subject-owned index; a caller cannot mint a foreign chat by rename.
    assert client.post('/api/chat/name',headers={'X-User-Id':'a'},json={'session':'missing','title':'New name'}).status_code==404
    assert client.post('/api/chat/name',headers={'X-User-Id':'a'},json={'session':'missing','title':' '}).status_code==422
    assert client.put('/api/chat/order',headers={'X-User-Id':'a'},json={'order':['b','a']}).status_code==200
    assert client.get('/api/chat/order',headers={'X-User-Id':'a'}).json()['order']==['b','a']
    assert client.get('/api/chat/order',headers={'X-User-Id':'b'}).json()['order']==[]
    assert client.put('/api/chat/order',headers={'X-User-Id':'a'},json={'order':['a','a']}).status_code==422


def test_agent_title_and_manual_title_reach_the_rail(client, stack):
    stack.upsert('a','chat',title='read all me emails')
    headers={'X-User-Id':'a'}
    response=client.post('/api/chat/name',headers=headers,json={'session':'chat','title':'Summarize recent customer emails','source':'agent'})
    assert response.status_code==200
    assert response.json()['label']=='Summarize recent customer emails'
    response=client.post('/api/chat/name',headers=headers,json={'session':'chat','title':'Customer follow-ups'})
    assert response.json()['name_source']=='human'
    response=client.post('/api/chat/name',headers=headers,json={'session':'chat','title':'Another title','source':'agent'})
    assert response.json()['changed'] is False
    assert response.json()['label']=='Customer follow-ups'
    assert client.post('/api/chat/name',headers={'X-User-Id':'b'},json={'session':'chat','title':'No'}).status_code==404
