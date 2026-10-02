import pytest
from sqlalchemy import create_engine, event, select, func, update
from sqlalchemy.pool import StaticPool
from crm import schema as s
from crm.store import Store, Forbidden, NotFound, Conflict, CRMError

@pytest.fixture
def store():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def foreign_keys(dbapi,c): dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    with engine.begin() as c:
        for tenant in ['one','two']:
            c.execute(s.object_types.insert().values(tenant_id=tenant,name='Company',definition={
                'type':'object','properties':{'name':{'type':'string'},'revenue':{'type':'number'}},
                'required':['name'],'additionalProperties':False}))
            c.execute(s.policies.insert().values(tenant_id=tenant,subject_id='owner',policy={'active':True,'admin':True}))
        c.execute(s.policies.insert().values(tenant_id='one',subject_id='reader',policy={'active':True,'objects':{
            'Company':{'read':'all','read_fields':['name'],'write':'none'}}}))
        c.execute(s.policies.insert().values(tenant_id='one',subject_id='agent',policy={'active':True,'review_required':True,'objects':{
            'Company':{'read':'all','read_fields':['*'],'write':'all','write_fields':['*']}}}))
    return Store(engine)

def make(store):
    return store.create('one','owner','Company',{'name':'Acme','revenue':100},'Revenue is 100',reason='fixture')['record_id']

def test_tenant_and_field_isolation(store):
    rid=make(store)
    assert store.get('one','reader',rid)['fields']=={'name':'Acme'}
    assert store.get('one','reader',rid)['narrative'] is None
    with pytest.raises(Forbidden): store.get('two','reader',rid)
    with pytest.raises(NotFound): store.get('two','owner',rid)
    with pytest.raises(Forbidden): store.search('one','reader','Company',{'revenue':100})
    with pytest.raises(NotFound): store.change('one','reader',rid,1,{'name':'oops'})

def test_conflict_and_atomic_history_outbox(store):
    rid=make(store)
    store.change('one','owner',rid,1,{'name':'Updated'},reason='call')
    with pytest.raises(Conflict): store.change('one','owner',rid,1,{'name':'lost'})
    with pytest.raises(CRMError): store.change('one','owner',rid,2,{'revenue':'invalid'})
    with store.engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(s.revisions))==2
        assert c.scalar(select(func.count()).select_from(s.outbox))==2
    assert store.get('one','owner',rid)['revision']==2

def test_history_redacts_hidden_fields_and_evidence(store):
    rid=make(store)
    store.change('one','owner',rid,1,{'revenue':200},reason='Revenue increased',evidence=[{'secret':'200'}])
    history=store.history('one','reader',rid)['revisions']
    assert history[0]['after']['fields']=={'name':'Acme'}
    assert history[0]['reason'] is None
    assert history[0]['evidence']==[]
    assert history[0]['after']['narrative'] is None

def test_proposal_review_and_replay(store):
    rid=make(store)
    with pytest.raises(Forbidden): store.change('one','agent',rid,1,{'name':'Direct'})
    p=store.change('one','agent',rid,1,{'name':'Reviewed'},propose=True)['proposal_id']
    assert store.get('one','owner',rid)['fields']['name']=='Acme'
    with pytest.raises(Forbidden): store.review('one','agent',p,True)
    assert store.review('one','owner',p,True)['revision']==2
    with pytest.raises(Conflict): store.review('one','owner',p,True)

def test_stale_proposal_does_not_mutate_record(store):
    rid=make(store)
    p=store.change('one','agent',rid,1,{'name':'Stale'},propose=True)['proposal_id']
    store.change('one','owner',rid,1,{'name':'New'})
    with pytest.raises(Conflict): store.review('one','owner',p,True)
    assert store.get('one','owner',rid)['fields']['name']=='New'

def test_revocation_and_quarantine(store):
    rid=make(store)
    with store.engine.begin() as c:
        c.execute(update(s.records).where(s.records.c.id==rid).values(quarantined=True))
    assert store.search('one','reader','Company')['records']==[]
    with pytest.raises(NotFound): store.get('one','reader',rid)
    with store.engine.begin() as c:
        c.execute(update(s.policies).where(s.policies.c.subject_id=='reader').values(policy={'active':False}))
    with pytest.raises(Forbidden): store.describe('one','reader')

def test_links_filter_unreadable_targets_and_reference_fields(store):
    rid=make(store);other=store.create('one','owner','Company',{'name':'Hidden target'},reason='fixture')['record_id']
    with store.engine.begin() as c:
        c.execute(s.relationships.insert().values(tenant_id='one',id='link',record_id=rid,
            kind='name',target_domain='crm',target_id=other))
        c.execute(update(s.records).where(s.records.c.id==other).values(quarantined=True))
    assert store.get('one','owner',rid)['links'][0]['record_id']==other
    assert store.get('one','reader',rid)['links']==[]

def test_proposal_is_validated_before_review(store):
    rid=make(store)
    with pytest.raises(CRMError):
        store.change('one','owner',rid,1,{'revenue':'not a number'},propose=True)

def test_create_retry_and_key_reuse(store):
    first=store.create('one','owner','Company',{'name':'Retry'},idempotency_key='request-1')
    replay=store.create('one','owner','Company',{'name':'Retry'},idempotency_key='request-1')
    assert first['record_id']==replay['record_id']
    assert replay['replayed']
    with pytest.raises(Conflict):store.create('one','owner','Company',{'name':'Changed'},idempotency_key='request-1')
    assert len(store.history('one','owner',first['record_id'])['revisions'])==1

def test_read_matches_published_wire_contract(store):
    import json
    from pathlib import Path
    from jsonschema import validate
    schema=json.loads((Path(__file__).parents[1]/'contracts/records.v1/record.schema.json').read_text())
    rid=make(store)
    validate(store.get('one','owner',rid),schema)
    validate(store.get('one','reader',rid),schema)

def test_review_queue_is_scoped_and_includes_the_change_before_acceptance(store):
    rid=make(store)
    proposal=store.change('one','agent',rid,1,{'name':'Review me'},reason='meeting',evidence=[{'meeting_id':'m1'}],propose=True)['proposal_id']
    owner=store.get('one','owner',rid)
    assert owner['actions']['can_review']
    assert owner['proposals'][0]['fields']=={'name':'Review me'}
    assert owner['proposals'][0]['evidence']==[{'meeting_id':'m1'}]
    assert store.get('one','reader',rid)['proposals']==[]
    assert store.get('one','agent',rid)['proposals'][0]['id']==proposal
    store.review('one','owner',proposal,False)
    assert store.get('one','owner',rid)['proposals']==[]
    assert store.get('one','owner',rid)['revision']==1

def test_card_configuration_is_versioned_and_does_not_change_records(store):
    rid=make(store)
    layout={'title_field':'name','sections':[{'title':'Overview','fields':[{'field':'revenue','format':'currency'},{'field':'name'}]}]}
    assert store.configure('one','owner','Company')['version']==0
    saved=store.configure('one','owner','Company',layout,0,'Readable card')
    assert saved['version']==1
    with pytest.raises(Conflict):store.configure('one','owner','Company',layout,0,'Stale layout')
    with pytest.raises(Forbidden):store.configure('one','agent','Company',layout,1,'Not admin')
    with pytest.raises(CRMError):store.configure('one','owner','Company',{'sections':[{'fields':[{'field':'secret'}]}]},1,'Bad field')
    assert store.get('one','owner',rid)['revision']==1
    assert store.get('one','owner',rid)['fields']['revenue']==100
    restricted=store.get('one','reader',rid)['card']['layout']
    assert [f['field'] for f in restricted['sections'][0]['fields']]==['name']
    table=store.search('one','reader','Company',{'name':'Acme'})
    assert [f['field'] for f in table['card']['layout']['sections'][0]['fields']]==['name']
    from urllib.parse import urlparse, parse_qs
    import json
    query=parse_qs(urlparse(table['href']).query)
    assert query['object']==['Company']
    assert json.loads(query['filters'][0])=={'name':'Acme'}
    assert store.configure('two','owner','Company')['version']==0
    store.configure('one','owner','Company',layout,1,'Second layout')
    with store.engine.connect() as c:assert c.scalar(select(func.count()).select_from(s.card_layouts))==2

def test_resolve_name_respects_field_access_tenant_and_ambiguity(store):
    rid=make(store)
    assert store.resolve_name('one','reader','ACME')['records'][0]['id']==rid
    assert store.resolve_name('two','owner','Acme')['records']==[]
    store.create('one','owner','Company',{'name':'Acme Capital','revenue':2},reason='prefix fixture')
    assert store.resolve_name('one','reader','Acme')['records'][0]['id']==rid
    assert len(store.resolve_name('one','reader','Acm')['records'])==2
    second=make(store)
    assert len(store.resolve_name('one','reader','Acme')['records'])==2
    with store.engine.begin() as c:
        c.execute(s.policies.update().where(s.policies.c.subject_id=='reader').values(policy={'active':True,'objects':{'Company':{'read':'all','read_fields':['revenue']}}}))
    assert store.resolve_name('one','reader','Acme')['records']==[]

def test_markdown_description_graph_revisions_and_review(store):
    target=make(store)
    source=store.create('one','owner','Company',{'name':'Source'},narrative=f'## Context\n\nRelated to [Acme](/crm?record={target}).',reason='Markdown fixture')['record_id']
    assert any(l['field']=='Description' and l['record_id']==target for l in store.get('one','owner',source)['links'])
    assert any(l['field']=='Referenced by' and l['record_id']==source for l in store.get('one','owner',target)['links'])
    assert store.get('one','reader',target)['links']==[]
    proposal=store.change('one','agent',source,1,{},narrative='## Revised description',reason='review text',propose=True)['proposal_id']
    assert store.get('one','owner',source)['proposals'][0]['narrative']=='## Revised description'
    assert store.get('one','owner',target)['links']
    store.review('one','owner',proposal,True)
    assert store.get('one','owner',target)['links']==[]
    revisions=store.history('one','owner',source)['revisions']
    assert any('Related to' in r['after']['narrative'] for r in revisions)

def test_description_code_samples_are_not_graph_links():
    from crm.markdown_links import crm_references
    rid='11111111-1111-4111-8111-111111111111'
    link=f'[Example](/crm?record={rid})'
    assert crm_references(link)==[rid]
    assert crm_references(f'`{link}`\n```md\n{link}\n````')==[]
    assert crm_references(f'```\n{link}')==[]
    assert crm_references('!'+link)==[]
