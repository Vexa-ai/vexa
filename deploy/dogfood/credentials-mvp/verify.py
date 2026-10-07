"""Integration checks against actual broker, vault and fake provider. Operator test driver only."""
import json,os,sqlite3,uuid
from pathlib import Path
import httpx
from app import mint,DB
BASE='http://broker:8100'
HEAD={'Host':'localhost:18541','Origin':'http://localhost:18541'}
actor='mvp-user';session='mvp-session-'+uuid.uuid4().hex[:8]
agent=mint(actor,session,'agent');other=mint('other-user','other-session','agent')
bootstrap=mint(actor,session,'bootstrap')
key=Path('/fixture/key').read_text().strip()
observed=[];checks=[]
def check(ok,label):
 if not ok:raise AssertionError(label)
 checks.append(label)
def req(client,method,path,body=None):
 r=client.request(method,BASE+path,json=body);observed.append(r.text);return r
with httpx.Client(headers={**HEAD,'Authorization':'Bearer '+agent},timeout=15) as a, httpx.Client(headers=HEAD,timeout=15) as h, httpx.Client(headers={**HEAD,'Authorization':'Bearer '+other},timeout=15) as b:
 check(req(h,'GET','/api/state').status_code==401,'unauthenticated refused')
 check(req(h,'POST','/login',{'value':bootstrap}).status_code==200,'human session established')
 check(req(h,'POST','/login',{'value':bootstrap}).status_code==401,'login bootstrap single-use')
 start=req(a,'POST','/api/setup',{'label':'API connection · verified fixture'});cid=start.json()['connection_id']
 check(start.status_code==200,'agent requests secure setup')
 check(req(a,'POST',f'/api/connections/{cid}/secret',{'value':key}).status_code==401,'agent cannot submit or read credentials')
 check(req(a,'GET',f'/api/connections/{cid}/secret').status_code==405,'no secret read endpoint')
 check(req(b,'GET',f'/api/connections/{cid}').status_code==404,'cross-user connection refused')
 check(req(b,'POST',f'/api/connections/{cid}/execute',{'operation_id':uuid.uuid4().hex}).status_code==404,'cross-user action refused')
 r=req(h,'POST',f'/api/connections/{cid}/secret',{'value':key});check(r.status_code==200 and r.json()['version']==1,'human stores actual vault secret')
 check(req(h,'POST',f'/api/connections/{cid}/secret',{'value':key,'unexpected':key}).status_code==422,'validation does not echo secret input')
 op=uuid.uuid4().hex
 check(req(a,'POST',f'/api/connections/{cid}/execute',{'operation_id':op,'url':'http://unapproved.invalid'}).status_code==422,'arbitrary destination refused')
 check(req(a,'POST',f'/api/connections/{cid}/execute',{'operation_id':op,'actor':'forged'}).status_code==422,'forged actor refused')
 result=req(a,'POST',f'/api/connections/{cid}/execute',{'operation_id':op}).json()
 check(result['outcome']=='success' and result['provider_receipt'],'authenticated provider operation succeeded')
 check(req(a,'POST',f'/api/connections/{cid}/execute',{'operation_id':op}).json()==result,'idempotent retry returns original receipt')
 second=mint(actor,'second-session','agent')
 with httpx.Client(headers={**HEAD,'Authorization':'Bearer '+second},timeout=15) as s:
  check(req(s,'POST',f'/api/connections/{cid}/execute',{'operation_id':uuid.uuid4().hex}).json()['outcome']=='success','authorized reuse in another session')
 # Replace with an invalid fixture credential, observe rejection, then restore.
 check(req(h,'POST',f'/api/connections/{cid}/secret',{'value':'deliberately-invalid-test-key'}).status_code==200,'credential rotation')
 check(req(a,'POST',f'/api/connections/{cid}/execute',{'operation_id':uuid.uuid4().hex}).json()['outcome']=='refused','invalid provider key refused')
 check(req(h,'POST',f'/api/connections/{cid}/secret',{'value':key}).status_code==200,'credential restored for demo')
 metadata=req(h,'GET','/api/state').json()
 events=[e for e in metadata['audit'] if e['operation_id']==op]
 check(len([e for e in events if e['action']=='fixture.verify' and e['outcome']=='success'])==1,'retry does not repeat provider action')
 reads=[e for e in events if e['action']=='credential.read']
 check(len(reads)==1 and reads[0]['vault_request_id'] and all(e['actor']==actor and e['session']==session for e in events),'server identity session and vault correlation present')
 check(all(key not in v for v in observed),'no secret in any API response')
 check(key.encode() not in DB.read_bytes(),'no plaintext secret in metadata database')
 # A separate one-time human login for browser demonstration. Never printed.
 Path('/operator/browser-login').write_text(mint(actor,'browser-demo','bootstrap'))
 Path('/operator/verified.json').write_text(json.dumps({'connection_id':cid,'checks':checks,'vault_request_ids':[e['vault_request_id'] for e in metadata['audit'] if e['vault_request_id']],'operations':len([e for e in metadata['audit'] if e['action']=='fixture.verify' and e['outcome']=='success'])},indent=2))
print(json.dumps({'passed':len(checks),'checks':checks},indent=2))
