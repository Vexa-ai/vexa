import json,unittest,socket
from unittest.mock import patch,MagicMock
import secret_service
from test_product_identity import ProductIdentity,broker
class NetworkBoundary(unittest.TestCase):
 def test_private_mixed_multicast_and_linklocal_refused(self):
  for ips in [('127.0.0.1',),('169.254.169.254',),('224.0.0.1',),('8.8.8.8','10.0.0.1'),('::1',)]:
   with patch.object(socket,'getaddrinfo',return_value=[(0,0,0,'',(ip,443)) for ip in ips]):
    with self.assertRaises(secret_service.ServiceError):secret_service.public_addresses('fixture.test')
 def test_endpoint_and_header_injection_refused(self):
  for endpoint in ['http://fixture.test/v1','https://user:pass@fixture.test/v1','https://fixture.test/v1?token=x','https://fixture.test/../admin']:
   with self.assertRaises(secret_service.ServiceError):secret_service.configure('key',endpoint,'Authorization','bearer','GET')
  with self.assertRaises(secret_service.ServiceError):secret_service.configure('key\nCookie:x','https://fixture.test/v1','Authorization','bearer','GET')
 def test_fixed_endpoint_pinned_ip_redirect_and_secret_redaction(self):
  config=secret_service.configure('private-token','https://fixture.test/v1','Authorization','bearer','GET')
  connection=MagicMock();response=connection.getresponse.return_value;response.status=200;response.read.return_value=b'{"echo":"private-token"}'
  with patch.object(secret_service,'public_addresses',return_value=['8.8.8.8']),patch.object(secret_service,'PinnedHTTPS',return_value=connection) as constructor:
   result=secret_service.execute(config,{'q':'fixture'})
   constructor.assert_called_once_with('fixture.test','8.8.8.8');self.assertNotIn('private-token',json.dumps(result))
   self.assertEqual(connection.request.call_args.args[:2],('GET','/v1?q=fixture'))
   response.status=302
   with self.assertRaises(secret_service.ServiceError):secret_service.execute(config,{})
   with self.assertRaises(secret_service.ServiceError):secret_service.execute(config,{},body={'write':True})
class SecretBoundary(ProductIdentity):
 def test_only_human_stores_agent_uses_own_reference(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'custom_secret','label':'Fixture'})[0].json()['connection_id']
  path=f'/api/connections/{cid}/custom-secret';payload={'value':'SECRET','endpoint':'https://fixture.test/v1'}
  with patch.object(broker,'vault',return_value={'data':{'version':1}}) as vault:
   self.assertEqual(self.signed('agent','POST',path,payload)[0].status_code,403);vault.assert_not_called()
   r=self.signed('human','POST',path,payload)[0];self.assertEqual(r.status_code,200);self.assertNotIn('SECRET',r.text)
  with patch.object(broker,'vault',return_value={'data':{'data':{'value':{'value':'SECRET'}}}}),patch.object(secret_service,'execute',return_value={'http_status':200,'content':{'ok':True}}) as execute:
   self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/call',{},actor='other')[0].status_code,404);execute.assert_not_called()
   self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/call',{})[0].status_code,200)
  self.assertNotIn('SECRET',self.signed('agent','GET','/api/connections')[0].text)
