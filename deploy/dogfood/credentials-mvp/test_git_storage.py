import threading,unittest
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import git_storage
class Tests(unittest.TestCase):
 def setUp(self):
  self.role='git';self.actor='2';self.value=None;self.version=0;self.audit=[]
  app=FastAPI()
  def identity(req,roles):
   if self.role not in roles:raise HTTPException(403,'refused')
   return {'actor':self.actor,'session':'fixture','role':self.role}
  git_storage.register(app,root=None,identity=identity,audit=lambda *a,**kw:self.audit.append((a,kw)),lock=threading.Lock())
  self.client=TestClient(app)
 def vault(self,root,addr,method,path,body=None,**kw):
  if method=='POST':
   assert body['options']['cas']==self.version
   self.value=body['data']['value'];self.version+=1
  return {'request_id':'receipt','data':{'data':{'value':self.value},'metadata':{'version':self.version}}} if self.version else None
 def call(self,action,value=None):return self.client.post('/api/internal/git-secret',json={'name':'pat/2','action':action,'value':value})
 @patch.dict('os.environ',{'BAO_ADDR':'fixture'})
 def test_migrate_write_revoke_and_no_resurrection(self):
  with patch('git_storage.vault_client.request',side_effect=self.vault):
   self.assertFalse(self.call('get').json()['found'])
   self.assertEqual(self.call('migrate','fixture').json()['value'],'fixture')
   self.assertEqual(self.call('migrate','other').json()['value'],'fixture')
   self.assertIsNone(self.call('put').json()['value'])
   self.assertIsNone(self.call('migrate','old').json()['value'])
   self.assertTrue(all(kw['operation'] for _,kw in self.audit))
 def test_agent_and_human_cannot_read(self):
  for role in ['agent','human']:
   self.role=role;self.assertEqual(self.call('get').status_code,403)
 def test_scope_mismatch_refused(self):
  self.actor='another';self.assertEqual(self.call('get').status_code,403)
 @patch.dict('os.environ',{'BAO_ADDR':'fixture'})
 def test_store_failure_is_not_absence(self):
  with patch('git_storage.vault_client.request',side_effect=git_storage.vault_client.VaultUnavailable()):
   self.assertEqual(self.call('get').status_code,503)
if __name__=='__main__':unittest.main()
