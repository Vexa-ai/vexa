"""Offline security fixtures; no real provider accounts or network calls."""
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import httpx

TEMP=tempfile.TemporaryDirectory()
os.environ['MVP_STATE']=TEMP.name
os.environ['MVP_ORIGIN']='http://localhost:18541'
import app as broker
import providers
from fastapi.testclient import TestClient

class Providers(unittest.TestCase):
 def setUp(self):
  self.root=Path(TEMP.name)
  (self.root/'broker.token').write_text('fixture-operator-token')
  (self.root/'oauth-clients.json').write_text(json.dumps({p:{'client_id':'fixture-client','client_secret':'fixture-client-secret'} for p in ['google','microsoft']}))
  self.client=TestClient(broker.app,base_url=broker.ORIGIN,headers={'Origin':broker.ORIGIN})
  self.client.post('/login',json={'value':broker.mint('provider-test','session','bootstrap')})
 def setup(self,provider):
  r=self.client.post('/api/setup',json={'provider':provider,'label':'Test'})
  self.assertEqual(r.status_code,200);return r.json()['connection_id']
 def test_registered_callback_port_is_path_scoped(self):
  callback='http://localhost:3001/api/auth/callback/google'
  with patch.object(broker,'OAUTH_REDIRECT',callback),patch.object(broker,'_redirect',urlsplit(callback)):
   cid=self.setup('google_calendar')
   q=parse_qs(urlsplit(self.client.post(f'/api/connections/{cid}/authorize').json()['authorize_url']).query)
   self.assertEqual(q['redirect_uri'],[callback])
   self.assertEqual(self.client.get('/api/state',headers={'Host':'localhost:3001'}).status_code,403)
   self.assertEqual(self.client.get('/api/auth/callback/google',headers={'Host':'evil.invalid'},params={'state':q['state'][0]}).status_code,403)
   token={'access_token':'fixture-access','refresh_token':'fixture-refresh','expires_at':99999999999}
   with patch.object(providers,'tokens',return_value=token) as exchange,patch.object(broker,'vault',return_value={'data':{'version':1},'request_id':'callback-vault'}):
    r=self.client.get('/api/auth/callback/google',params={'state':q['state'][0],'code':'fixture-code'},headers={'Host':'localhost:3001'},follow_redirects=False)
    self.assertEqual(r.status_code,303);self.assertTrue(r.headers['location'].startswith(broker.ORIGIN+'/#connection='))
    self.assertEqual(exchange.call_args.kwargs['redirect'],callback)
 def test_operator_configuration_boundary(self):
  payload={'client_id':'123-fixture.apps.googleusercontent.com','client_secret':'fixture-application-secret'}
  self.assertEqual(self.client.post('/api/operator/google',json=payload).status_code,401)
  self.client.post('/login',json={'value':broker.mint('operator-test','operator-session','operator_bootstrap')})
  with patch.object(broker,'vault',return_value={'data':{'version':1},'request_id':'operator-vault'}) as store:
   response=self.client.post('/api/operator/google',json=payload)
   self.assertEqual(response.status_code,200)
   self.assertEqual(store.call_args.args[1],'connections/data/operator-google')
   self.assertNotIn(payload['client_secret'],response.text)
   self.assertNotIn(payload['client_secret'],self.client.get('/api/state').text)
  self.assertEqual(self.client.get('/api/state').json()['operator'],True)
 def test_catalog_no_credentials(self):
  r=self.client.get('/api/state');self.assertNotIn('fixture-client-secret',r.text)
  self.assertEqual({p['id'] for p in r.json()['providers']},set(providers.CATALOG))
 def test_missing_application(self):
  (self.root/'oauth-clients.json').unlink()
  cid=self.setup('google_email');r=self.client.post(f'/api/connections/{cid}/authorize')
  self.assertEqual(r.status_code,409);self.assertNotIn('authorize_url',r.text)
 def test_agent_cannot_authorize_or_write(self):
  cid=self.setup('github');headers={'Authorization':'Bearer '+broker.mint('provider-test','agent','agent')}
  for path,body in [('authorize',{}),('secret',{'value':'fixture-secret-value'})]:
   self.assertEqual(self.client.post(f'/api/connections/{cid}/{path}',headers=headers,json=body).status_code,401)
 def test_oauth_start_pkce_scope(self):
  for provider in ['google_email','microsoft_calendar']:
   cid=self.setup(provider);r=self.client.post(f'/api/connections/{cid}/authorize');self.assertEqual(r.status_code,200)
   u=urlsplit(r.json()['authorize_url']);q=parse_qs(u.query)
   self.assertEqual(q['code_challenge_method'],['S256']);self.assertEqual(q['redirect_uri'],[broker.ORIGIN+'/oauth/callback'])
   self.assertNotIn('client_secret',q);self.assertNotIn('code_verifier',q)
   self.assertEqual(q['code_challenge'],[broker.pkce(q['state'][0])[1]])
 def test_callback_owner_session_replay(self):
  cid=self.setup('google_email');q=parse_qs(urlsplit(self.client.post(f'/api/connections/{cid}/authorize').json()['authorize_url']).query)
  url='/oauth/callback?state='+q['state'][0]+'&code=fixture-code'
  other=TestClient(broker.app,base_url=broker.ORIGIN,headers={'Origin':broker.ORIGIN})
  other.post('/login',json={'value':broker.mint('provider-test','different-session','bootstrap')})
  self.assertEqual(other.get(url).status_code,403)
  token={'access_token':'fixture-user-secret','refresh_token':'fixture-refresh-secret','expires_at':99999999999}
  with patch.object(providers,'tokens',return_value=token),patch.object(broker,'vault',return_value={'data':{'version':1},'request_id':'fixture-vault'}) as store:
   r=self.client.get(url,follow_redirects=False);self.assertEqual(r.status_code,303)
   self.assertEqual(store.call_args.args[2]['data']['value'],token)
   self.assertNotIn('fixture-user-secret',r.text)
   self.assertNotIn('fixture-user-secret',self.client.get('/api/state').text)
   self.assertEqual(self.client.get(url).status_code,403)
 def test_denied_consent_never_connected(self):
  cid=self.setup('microsoft_email');u=self.client.post(f'/api/connections/{cid}/authorize').json()['authorize_url'];state=parse_qs(urlsplit(u).query)['state'][0]
  with patch.object(broker,'vault') as store:
   r=self.client.get('/oauth/callback',params={'state':state,'error':'access_denied'},follow_redirects=False)
   self.assertEqual(r.status_code,303);store.assert_not_called()
  self.assertEqual(self.client.get('/api/connections/'+cid).json()['status'],'awaiting_user')
 def test_oauth_cannot_accept_pasted_tokens(self):
  cid=self.setup('google_calendar')
  self.assertEqual(self.client.post(f'/api/connections/{cid}/secret',json={'value':'raw-secret'}).status_code,409)
 def test_feed_ssrf_and_redirect_refusal(self):
  for value in ['http://calendar.google.com/a.ics','https://127.0.0.1/a.ics','https://calendar.google.com.evil/a.ics','https://x@calendar.google.com/a.ics','https://calendar.google.com:444/a.ics','https://calendar.google.com/calendar/embed']:
   with self.assertRaises(providers.ProviderError):providers.secret('calendar_ics',value)
  client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})))
  self.assertEqual(providers.verify('calendar_ics','https://calendar.google.com/a.ics','operation',client=client),(False,''))
 def test_tokens_sanitized_and_scopes_checked(self):
  for data in [{'access_token':'secret','token_type':'Bearer','scope':'wrong','expires_in':3600}, {'error':'secret-echo'}]:
   client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)))
   with self.assertRaises(providers.ProviderError) as err:providers.tokens(self.root,'google_email',code='fixture-code',client=client)
   self.assertNotIn('secret',str(err.exception))
 def test_refresh_rotation(self):
  seen=[]
  def wire(r):
   seen.append(parse_qs(r.content.decode()));return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','token_type':'Bearer','expires_in':3600})
  value=providers.tokens(self.root,'microsoft_email',refresh='old-refresh',client=httpx.Client(transport=httpx.MockTransport(wire)))
  self.assertEqual(value['refresh_token'],'new-refresh');self.assertEqual(seen[0]['grant_type'],['refresh_token'])
 def test_verify_does_not_return_content(self):
  def wire(r):
   self.assertEqual(str(r.url),'https://api.github.com/user');self.assertEqual(r.headers['Authorization'],'Bearer test-secret')
   return httpx.Response(200,json={'login':'private-user','email':'private@example.com'})
  self.assertEqual(providers.verify('github','test-secret','operation',client=httpx.Client(transport=httpx.MockTransport(wire))),(True,''))
 def test_disconnect_blocks_use(self):
  cid=self.setup('github')
  self.assertEqual(self.client.post(f'/api/connections/{cid}/disconnect').status_code,200)
  self.assertEqual(self.client.post(f'/api/connections/{cid}/execute',json={'operation_id':'test-op-1234','action':'connection.verify'}).status_code,409)
 def test_invalid_secret_error_does_not_echo(self):
  cid=self.setup('calendar_ics');r=self.client.post(f'/api/connections/{cid}/secret',json={'value':'private-secret-not-url'})
  self.assertEqual(r.status_code,409);self.assertNotIn('private-secret-not-url',r.text)

if __name__=='__main__':unittest.main()
