import json,unittest
from unittest.mock import patch,MagicMock
import connection_setup,secret_service
from test_product_identity import ProductIdentity,broker
SPEC={'endpoint':'https://api.telegram.org/bot{secret}/sendMessage','scheme':'telegram','method':'POST','secret_label':'Bot token','fields':[{'name':'chat_id','label':'Chat ID','location':'body'}]}
class PreparedBoundary(ProductIdentity):
 def test_proposal_requires_human_save_and_exact_revision(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Telegram'})[0].json()['connection_id']
  path=f'/api/connections/{cid}'
  self.assertEqual(self.signed('agent','POST',path+'/prepare',{'setup':SPEC})[0].status_code,200)
  rows=self.signed('agent','GET','/api/connections')[0].json()['connections'];row=next(x for x in rows if x['id']==cid)
  payload={'value':'123:fixture_token','fields':{'chat_id':'-12345'},'setup_request':row['setup_request']}
  with patch.object(broker,'vault',return_value={'data':{'version':1}}) as vault:
   self.assertEqual(self.signed('agent','POST',path+'/custom-secret',payload)[0].status_code,403)
   self.assertEqual(self.signed('human','POST',path+'/custom-secret',{**payload,'setup_request':'old'})[0].status_code,409)
   vault.assert_not_called()
   self.assertEqual(self.signed('human','POST',path+'/custom-secret',payload)[0].status_code,200)
   self.assertEqual(vault.call_args.args[2]['data']['value']['fixed_body'],{'chat_id':'-12345'})
  with patch.object(broker,'vault',side_effect=[{'data':{'data':{'value':{'value':'123:fixture_token','endpoint':SPEC['endpoint'],'header':'Authorization','scheme':'telegram','method':'POST'}}}},{'data':{'version':2}}]) as vault:
   self.assertEqual(self.signed('human','POST',path+'/custom-secret',{**payload,'value':''})[0].status_code,200)
   self.assertEqual(vault.call_args.args[2]['data']['value']['value'],'123:fixture_token')
  self.assertNotIn('fixture_token',self.signed('agent','GET','/api/connections')[0].text)
 def test_reuse_cannot_move_existing_secret_to_another_destination(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Service'})[0].json()['connection_id']
  with broker.db() as c:c.execute("UPDATE connections SET status='ready',version=1 WHERE id=?",(cid,))
  saved={'value':'fixture-secret','endpoint':'https://api.example.com/one','header':'Authorization','scheme':'bearer','method':'GET'}
  with patch.object(broker,'vault',return_value={'data':{'data':{'value':saved}}}) as vault:
   result=self.signed('human','POST',f'/api/connections/{cid}/custom-secret',{'value':'','endpoint':'https://other.example/two'})[0]
   self.assertEqual(result.status_code,409)
   self.assertEqual(vault.call_count,1)
   self.assertEqual(vault.call_args.args[0],'GET')
   self.assertNotIn('fixture-secret',result.text)
 def test_delete_owner_human_only_hides_and_disables(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Disposable fixture'})[0].json()['connection_id']
  path=f'/api/connections/{cid}'
  self.assertEqual(self.signed('agent','POST',path+'/delete')[0].status_code,403)
  self.assertEqual(self.signed('human','POST',path+'/delete',actor='other')[0].status_code,404)
  self.assertEqual(self.signed('human','POST',path+'/delete')[0].status_code,200)
  self.assertNotIn(cid,self.signed('agent','GET','/api/connections')[0].text)
  self.assertEqual(self.signed('agent','POST',path+'/request')[0].status_code,404)
  self.assertEqual(self.signed('human','POST',path+'/custom-secret',{'value':'fixture'})[0].status_code,404)
 def test_invalid_spec_and_foreign_owner_refused(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Fixture'})[0].json()['connection_id']
  path=f'/api/connections/{cid}/prepare'
  self.assertEqual(self.signed('agent','POST',path,{'setup':SPEC},actor='other')[0].status_code,404)
  self.assertEqual(self.signed('agent','POST',path,{'setup':{**SPEC,'endpoint':'https://other.test/bot{secret}/sendMessage'}})[0].status_code,422)
class Execution(unittest.TestCase):
 def test_path_token_redaction_and_fixed_recipient(self):
  config=secret_service.configure('123:fixture_token',SPEC['endpoint'],'Authorization','telegram','POST');config['fixed_body']={'chat_id':'-12345'}
  c=MagicMock();c.getresponse.return_value.status=200;c.getresponse.return_value.read.return_value=b'{"echo":"123:fixture_token"}'
  with patch.object(secret_service,'public_addresses',return_value=['8.8.8.8']),patch.object(secret_service,'PinnedHTTPS',return_value=c):
   result=secret_service.execute(config,{}, {'text':'fixture'})
   self.assertEqual(c.request.call_args.args[:2],('POST','/bot123:fixture_token/sendMessage'))
   self.assertNotIn('Authorization',c.request.call_args.kwargs['headers'])
   self.assertEqual(json.loads(c.request.call_args.kwargs['body'])['chat_id'],'-12345')
   self.assertNotIn('fixture_token',json.dumps(result))
   with self.assertRaises(secret_service.ServiceError):secret_service.execute(config,{}, {'chat_id':'other'})
 def test_generic_spec_and_no_get_body(self):
  self.assertEqual(connection_setup.validate({'endpoint':'https://api.example.com/v1'})['fields'],[])
  with self.assertRaises(ValueError):connection_setup.validate({'endpoint':'https://api.example.com/v1','fields':[{'name':'id','label':'ID'}]})
