from pathlib import Path
from datetime import datetime
import pytest
from control_plane.onboarding_research import Research, ResearchError

MAIL={'id':'a'*32,'provider':'google_email'}
CAL={'id':'b'*32,'provider':'google_calendar'}


def test_restart_replays_and_receipts_gate_cursor(tmp_path):
    calls=[]
    def read(cid, args):
        calls.append(args)
        if args['action']=='gmail.search':
            return {'messages':[{'id':'second' if args['page_token'] else 'first'}],
                    'has_more':not bool(args['page_token']), 'next_page_token':'page2' if not args['page_token'] else ''}
        return {'message':{'id':args['message_id'],'body':'evidence'}}
    run=Research(tmp_path,read)
    start=run.run('start',connections=[MAIL])
    assert (datetime.fromisoformat(start['time_max'])-datetime.fromisoformat(start['time_min'])).days==90
    first=run.run('next')
    assert first['items'][0]['body']=='evidence'
    run=Research(tmp_path,read)
    assert run.run('next')['batch_id']==first['batch_id']
    assert sum(c['action']=='gmail.search' for c in calls)==1
    sid=first['items'][0]['source_id']
    with pytest.raises(ResearchError):run.run('ack',batch_id=first['batch_id'],receipts=[])
    with pytest.raises(ResearchError):run.run('ack',batch_id=first['batch_id'],receipts=[{'source_id':sid,'paths':['kg/missing.md']}])
    page=tmp_path/'kg/project.md';page.parent.mkdir();page.write_text(sid)
    receipt={'source_id':sid,'paths':['kg/project.md']}
    result=run.run('ack',batch_id=first['batch_id'],receipts=[receipt])
    assert result['streams'][0]['cursor']=='page2'
    assert run.run('ack',batch_id=first['batch_id'],receipts=[receipt])['counts']['reviewed']==1
    second=run.run('next');assert second['items'][0]['id']=='second'
    result=run.run('ack',batch_id=second['batch_id'],receipts=[{'source_id':second['items'][0]['source_id'],'excluded':'no_durable_facts','reason':'Only acknowledges receipt'}])
    assert result['status']=='source_pass_complete'
    assert result['counts']=={'reviewed':2,'extracted':1,'excluded':1}
    assert 'graph synthesis' in run.run('next')['instruction']


def test_failure_is_not_consumed_and_accounts_are_isolated(tmp_path):
    fail=True
    def read(cid,args):
        if args['action']=='gmail.search':return {'messages':[{'id':'1'}],'has_more':False}
        if fail:raise RuntimeError('provider unavailable')
        return {'message':{'id':'1'}}
    run=Research(tmp_path/'one',read);run.run('start',connections=[MAIL])
    with pytest.raises(RuntimeError):run.run('next')
    pending=run.run('status')['pending']['id']
    assert run.run('status')['counts']['reviewed']==0
    assert Research(tmp_path/'two',read).run('status')['status']=='not_started'
    fail=False
    assert run.run('next')['batch_id']==pending


def test_calendar_metadata_snapshot_survives_restart(tmp_path):
    event={'id':'e','organizer':{'email':'fixture@example.test'},'attendees':[{'email':'guest@example.test'}]}
    calls=[]
    def read(cid,args):calls.append(cid);return {'events':[event],'has_more':False}
    run=Research(tmp_path,read);run.run('start',connections=[CAL])
    first=run.run('next')
    assert first['items'][0]['attendees']==event['attendees']
    assert Research(tmp_path,read).run('next')==first
    assert len(calls)==1
    assert 'events' not in run.run('status')['pending']


def test_all_accounts_and_empty_pages_are_traversed(tmp_path):
    calls=[]
    def read(cid,args):calls.append(cid);return {'messages':[],'events':[],'has_more':False}
    run=Research(tmp_path,read);run.run('start',connections=[MAIL,CAL])
    for _ in range(2):
        batch=run.run('next');run.run('ack',batch_id=batch['batch_id'])
    assert calls==[MAIL['id'],CAL['id']]
    assert run.run('status')['status']=='source_pass_complete'


def test_unadvanced_cursor_and_receipt_traversal_refused(tmp_path):
    run=Research(tmp_path,lambda *_:{'messages':[],'has_more':True})
    run.run('start',connections=[MAIL])
    with pytest.raises(ResearchError,match='cursor'):run.run('next')
    assert run.run('status')['pending'] is None


def test_missing_source_reference_does_not_advance(tmp_path):
    run=Research(tmp_path,lambda cid,a: {'messages':[{'id':'m'}]} if a['action']=='gmail.search' else {'message':{'id':'m'}})
    run.run('start',connections=[MAIL]);batch=run.run('next')
    (tmp_path/'kg').mkdir();(tmp_path/'kg/project.md').write_text('Uncited assertion')
    with pytest.raises(ResearchError,match='source reference'):
        run.run('ack',batch_id=batch['batch_id'],receipts=[{'source_id':batch['items'][0]['source_id'],'paths':['kg/project.md']}])
    assert run.run('status')['counts']['reviewed']==0


def test_unauthorized_account_cannot_start_and_route_uses_owner(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from control_plane.routers import connections
    class Reader:
        def workspace_dir(self, actor):return tmp_path/actor
    def subject(request):
        if not request.headers.get('x-user-id'):raise HTTPException(401)
        return request.headers['x-user-id']
    calls=[]
    def broker(actor,method,path,payload=None,*,identity):
        assert identity=='signed-one'
        calls.append(actor)
        if method=='GET':return {'connections':[{**MAIL,'status':'ready'}]}
        return {'messages':[], 'has_more':False}
    monkeypatch.setattr(connections,'call_broker',broker)
    app=FastAPI();app.include_router(connections.build(subject_of=subject,wsr=Reader()))
    client=TestClient(app);headers={'x-user-id':'one','x-vexa-identity':'signed-one'}
    assert client.post('/api/onboarding/research',json={'action':'status'}).status_code==401
    assert client.post('/api/onboarding/research',headers=headers,json={'action':'start','connection_ids':[CAL['id']]}).status_code==409
    assert client.post('/api/onboarding/research',headers=headers,json={'action':'start','connection_ids':[MAIL['id']]}).status_code==200
    batch=client.post('/api/onboarding/research',headers=headers,json={'action':'next'}).json()
    assert batch['status']=='batch'
    assert client.post('/api/onboarding/research',headers=headers,json={'action':'ack','batch_id':batch['batch_id']}).json()['status']=='source_pass_complete'
    assert set(calls)=={'one'}
    assert client.post('/api/onboarding/research',headers={'x-user-id':'two','x-vexa-identity':'signed-two'},json={'action':'status'}).json()['status']=='not_started'


def test_malformed_provider_result_is_not_an_empty_success(tmp_path):
    run=Research(tmp_path,lambda *_:{'status':'unavailable'})
    run.run('start',connections=[MAIL])
    with pytest.raises(ResearchError,match='invalid source page'):run.run('next')
    assert run.run('status')['status']=='researching'


def test_later_calendar_consent_adds_stream_without_resetting_mail(tmp_path):
    run=Research(tmp_path,lambda *_:{'messages':[],'events':[]})
    first=run.run('start',connections=[MAIL]);batch=run.run('next')
    run.run('ack',batch_id=batch['batch_id'])
    extended=run.run('start',connections=[MAIL,CAL])
    assert extended['time_min']==first['time_min']
    assert extended['streams'][0]['done']
    assert not extended['streams'][1]['done']
    assert extended['status']=='researching'


def test_checkpoint_content_is_outside_the_user_git_workspace(tmp_path):
    workspace=tmp_path/'person';workspace.mkdir()
    run=Research(workspace,lambda *_:{'events':[{'id':'e','description':'private calendar description'}]})
    run.run('start',connections=[CAL]);run.run('next')
    assert workspace not in run.directory.parents
    assert not list(workspace.rglob('state.json'))
