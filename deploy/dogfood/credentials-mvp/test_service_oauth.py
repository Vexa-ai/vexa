import json, unittest
from unittest.mock import patch,MagicMock
from urllib.parse import parse_qs,urlsplit
import service_oauth,connection_setup,secret_service
from test_product_identity import ProductIdentity,broker

SPEC={'endpoint':'https://api.example.com/v2/data','oauth':{'authorization_url':'https://login.example.com/authorize','token_url':'https://api.example.com/token','scopes':['read'],'token_auth':'client_secret_post'},'fields':[]}

class GenericOAuth(unittest.TestCase):
 def test_schema_accepts_provider_neutral_oauth_and_rejects_unsafe_urls(self):
  self.assertEqual(connection_setup.validate(SPEC)['oauth']['scopes'],['read'])
  for url in ['http://example.com/token','https://example.com/token?secret=x','https://user:pass@example.com/token']:
   with self.assertRaises((ValueError,secret_service.ServiceError)):
    connection_setup.validate({**SPEC,'oauth':{**SPEC['oauth'],'token_url':url}})
 def test_exchange_pins_dns_rotates_tokens_and_exposes_no_response_extras(self):
  conn=MagicMock();response=conn.getresponse.return_value;response.status=200
  response.read.return_value=json.dumps({'access_token':'access','refresh_token':'rotated','expires_in':3600,'token_type':'bearer','secret':'private'}).encode()
  with patch.object(secret_service,'public_addresses',return_value=['93.184.216.34']),patch.object(secret_service,'PinnedHTTPS',return_value=conn) as transport:
   result=service_oauth.exchange(SPEC,{'client_id':'id','client_secret':'private'},refresh='old')
  transport.assert_called_once_with('api.example.com','93.184.216.34')
  self.assertEqual(result['refresh_token'],'rotated');self.assertNotIn('private',json.dumps(result))
  form=parse_qs(conn.request.call_args.kwargs['body'].decode());self.assertEqual(form['refresh_token'],['old'])
 def test_private_resolution_is_rejected_before_credentials_sent(self):
  with patch.object(secret_service,'public_addresses',side_effect=secret_service.ServiceError('private')),patch.object(secret_service,'PinnedHTTPS') as transport:
   with self.assertRaises(secret_service.ServiceError):service_oauth.exchange(SPEC,{'client_id':'id','client_secret':'private'},refresh='old')
   transport.assert_not_called()

class GenericOAuthBoundary(ProductIdentity):
 def test_application_owner_human_and_exact_proposal_binding(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Example'})[0].json()['connection_id']
  self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/prepare',{'setup':SPEC})[0].status_code,200)
  with broker.db() as c:row=dict(c.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone())
  body={'client_id':'id','client_secret':'PRIVATE','setup_request':row['setup_request']};path=f'/api/connections/{cid}/oauth-application'
  with patch.object(broker,'vault',return_value={'request_id':'receipt','data':{'version':1}}) as vault:
   self.assertEqual(self.signed('agent','POST',path,body)[0].status_code,403)
   self.assertEqual(self.signed('human','POST',path,body,actor='other')[0].status_code,404)
   self.assertEqual(self.signed('human','POST',path,{**body,'setup_request':'stale'})[0].status_code,409)
   vault.assert_not_called()
   r=self.signed('human','POST',path,body)[0];self.assertEqual(r.status_code,200);self.assertNotIn('PRIVATE',r.text)
  with broker.db() as c:
   self.assertNotIn('PRIVATE',json.dumps([dict(r) for r in c.execute('SELECT * FROM connections')]))
   self.assertNotIn('PRIVATE',json.dumps([dict(r) for r in c.execute('SELECT * FROM audit')]))
  result=self.signed('agent','GET','/api/connections')[0].json()['connections'][0]
  self.assertTrue(result['application_configured']);self.assertNotIn('PRIVATE',json.dumps(result))
 def test_generic_callback_binds_and_stores_approved_config(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Example'})[0].json()['connection_id']
  spec=connection_setup.validate(SPEC);cfg={'client_id':'id','client_secret':'PRIVATE','spec':spec}
  with broker.db() as c:c.execute('UPDATE connections SET setup_spec=?,oauth_app_version=1 WHERE id=?',(json.dumps(spec),cid))
  def vault(method,path,body=None):
   if method=='GET':return {'request_id':'read','data':{'data':{'value':cfg}}}
   self.assertEqual(body['data']['value']['oauth_application'],cfg)
   return {'request_id':'write','data':{'version':2}}
  with patch.object(broker,'vault',side_effect=vault),patch.object(secret_service,'public_addresses',return_value=['93.184.216.34']),patch.object(service_oauth,'exchange',return_value={'access_token':'ACCESS','refresh_token':'REFRESH','expires_at':99999999999}):
   r=self.signed('human','POST',f'/api/connections/{cid}/authorize',{})[0]
   state=parse_qs(urlsplit(r.json()['authorize_url']).query)['state'][0]
   r=self.signed('human','GET','/api/auth/callback/google?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'state':state,'code':'CODE'}))[0]
   self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'connected')
   self.assertNotIn('PRIVATE',r.text);self.assertNotIn('ACCESS',r.text)
   self.assertEqual(self.signed('human','GET','/api/auth/callback/google?state='+state+'&code=CODE')[0].status_code,403)
 def test_stale_agent_cannot_downgrade_oauth_to_token_form(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Example'})[0].json()['connection_id']
  path=f'/api/connections/{cid}/prepare'
  self.assertEqual(self.signed('agent','POST',path,{'setup':SPEC})[0].status_code,200)
  self.assertEqual(self.signed('agent','POST',path,{'setup':{'endpoint':'https://api.example.com/data'}})[0].status_code,409)
  with broker.db() as c:row=c.execute('SELECT setup_spec FROM connections WHERE id=?',(cid,)).fetchone()
  self.assertTrue(json.loads(row['setup_spec'])['oauth'])
 def test_identical_setup_does_not_invalidate_approved_application(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Example'})[0].json()['connection_id'];path=f'/api/connections/{cid}/prepare'
  self.assertEqual(self.signed('agent','POST',path,{'setup':SPEC})[0].status_code,200)
  with broker.db() as c:c.execute('UPDATE connections SET oauth_app_version=7 WHERE id=?',(cid,))
  self.assertEqual(self.signed('agent','POST',path,{'setup':SPEC})[0].status_code,200)
  with broker.db() as c:self.assertEqual(c.execute('SELECT oauth_app_version FROM connections WHERE id=?',(cid,)).fetchone()[0],7)
