"""Product boundary fixtures: role separation, tamper/replay, owner/session binding."""
import base64, hashlib, hmac, json, os, time, uuid, unittest
from pathlib import Path
from unittest.mock import patch
import test_providers as fixtures
TEMP, broker, providers = fixtures.TEMP, fixtures.broker, fixtures.providers

class ProductIdentity(unittest.TestCase):
 def setUp(self):
  fixtures.Providers.setUp(self)
  for role in ('agent','human','git'):
   p=Path(TEMP.name)/(role+'.key');p.write_text(role*32)
   os.environ['VEXA_CONNECTIONS_'+role.upper()+'_KEY_FILE']=str(p)
  os.environ['VEXA_CONNECTIONS_PRODUCT_REDIRECT']='https://app.dev.vexa.ai/api/auth/callback/google'
 def signed(self,role,method,path,body=None,actor='product-user',session='browser-session',headers=None):
  raw=json.dumps(body,separators=(',',':')).encode() if body is not None else b''
  claims=dict(role=role,actor=actor,session=session,at=int(time.time()),nonce=uuid.uuid4().hex,method=method,path=path,body=hashlib.sha256(raw).hexdigest())
  encoded=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
  key=Path(os.environ['VEXA_CONNECTIONS_'+role.upper()+'_KEY_FILE']).read_bytes()
  h={'X-Vexa-Assertion':encoded+'.'+hmac.new(key,encoded.encode(),hashlib.sha256).hexdigest(),'Content-Type':'application/json',**(headers or {})}
  return self.client.request(method,path,content=raw,headers=h,follow_redirects=False),h
 def test_agent_requests_but_cannot_authorize_or_read_other_users(self):
  r,_=self.signed('agent','POST','/api/setup',{'provider':'google_email','label':'Gmail'})
  cid=r.json()['connection_id']
  self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/authorize')[0].status_code,403)
  self.assertEqual(self.signed('human','POST',f'/api/connections/{cid}/authorize',actor='other')[0].status_code,404)
  self.assertEqual(self.signed('agent','GET','/api/connections',actor='other')[0].json(),{'connections':[]})
  self.assertEqual(self.signed('agent','POST','/api/operator/google',{'client_id':'123-fixture.apps.googleusercontent.com','client_secret':'fixture-secret'})[0].status_code,403)
 def test_tampering_and_replay_refused(self):
  r,h=self.signed('human','GET','/api/connections');self.assertEqual(r.status_code,200)
  self.assertEqual(self.client.get('/api/connections',headers=h).status_code,401)
  self.assertEqual(self.client.post('/api/setup',headers=h,json={'provider':'google_email'}).status_code,401)
 def test_pending_request_signal_is_owner_scoped(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'google_email','label':'Gmail'})[0].json()['connection_id']
  initial=self.signed('human','GET','/api/connections')[0].json()['connections']
  before=next(r['setup_request'] for r in initial if r['id']==cid)
  self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/request',actor='other')[0].status_code,404)
  self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/request')[0].status_code,200)
  rows=self.signed('human','GET','/api/connections')[0].json()['connections']
  self.assertEqual(len(rows),len(initial))
  self.assertNotEqual(before,next(r['setup_request'] for r in rows if r['id']==cid))
 def test_product_callback_bound_to_session_and_single_use(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'google_calendar','label':'Calendar'})[0].json()['connection_id']
  r,_=self.signed('human','POST',f'/api/connections/{cid}/authorize')
  from urllib.parse import parse_qs,urlsplit,urlencode
  q=parse_qs(urlsplit(r.json()['authorize_url']).query)
  self.assertEqual(q['redirect_uri'],['https://app.dev.vexa.ai/api/auth/callback/google'])
  self.assertTrue(q['state'][0].startswith('vxc_'))
  path='/api/auth/callback/google?'+urlencode({'state':q['state'][0],'code':'fixture-code'})
  self.assertEqual(self.signed('human','GET',path,session='another-session')[0].status_code,403)
  with patch.object(providers,'tokens',return_value={'access_token':'DO-NOT-EXPOSE'}),patch.object(broker,'vault',return_value={'data':{'version':1}}):
   r,_=self.signed('human','GET',path)
   self.assertEqual(r.json(),{'connection_id':cid,'status':'connected'})
  self.assertEqual(self.signed('human','GET',path)[0].status_code,403)
  r,_=self.signed('agent','GET','/api/connections')
  self.assertNotIn('DO-NOT-EXPOSE',r.text)

 def test_git_service_role_is_separate_from_agent_and_human(self):
  payload={'name':'pat/2','action':'get','value':None}
  for role in ('agent','human'):
   self.assertEqual(self.signed(role,'POST','/api/internal/git-secret',payload,actor='2')[0].status_code,403)
  self.assertEqual(self.signed('git','GET','/api/connections',actor='2')[0].status_code,403)
  self.assertEqual(self.signed('git','POST','/api/internal/git-secret',payload,actor='other')[0].status_code,403)
