import unittest,json,base64,time
from unittest.mock import patch
import httpx
from test_product_identity import ProductIdentity,broker,providers

class AccountAdapters(unittest.TestCase):
 def test_gmail_read_projects_content_not_credentials(self):
  def respond(req):
   self.assertEqual(req.url.host,'gmail.googleapis.com');self.assertEqual(req.method,'GET')
   self.assertEqual(req.headers['authorization'],'Bearer private-token')
   return httpx.Response(200,json={'id':'abc','payload':{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'fixture mail').decode()},'headers':[{'name':'Subject','value':'fixture'}]},'access_token':'must-not-return'})
  with httpx.Client(transport=httpx.MockTransport(respond)) as c:
   r=providers.read_account('google_email',{'access_token':'private-token'},'gmail.read',message_id='abc',client=c)
  self.assertEqual(r['message']['body'],'fixture mail');self.assertNotIn('token',json.dumps(r))
 def test_search_fixed_host_and_bound_results(self):
  calls=[]
  def respond(req):
   calls.append(req)
   return httpx.Response(200,json={'messages':[{'id':'abc'}],'nextPageToken':'opaque'} if req.url.path.endswith('/messages') else {'id':'abc','payload':{}})
  with httpx.Client(transport=httpx.MockTransport(respond)) as c:
   r=providers.read_account('google_email',{'access_token':'x'},'gmail.search',query='from:fixture.test',client=c)
  self.assertEqual(len(r['messages']),1);self.assertTrue(r['has_more']);self.assertEqual(calls[0].url.params['q'],'from:fixture.test')
 def test_wrong_provider_paths_and_time_range_refused(self):
  with httpx.Client(transport=httpx.MockTransport(lambda r:self.fail('network forbidden'))) as c:
   for p,a,kw in [('google_calendar','gmail.search',{}),('google_email','gmail.read',{'message_id':'../../secret'}),('google_calendar','calendar.events',{'time_min':'bad','time_max':'bad'})]:
    with self.assertRaises(providers.ProviderError):providers.read_account(p,{'access_token':'x'},a,client=c,**kw)

class AccountBoundary(ProductIdentity):
 def test_read_owner_provider_ready_and_audit(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'google_email'})[0].json()['connection_id']
  body={'action':'gmail.search','query':'private search'};path=f'/api/connections/{cid}/read'
  with patch.object(broker,'vault') as vault:
   self.assertEqual(self.signed('agent','POST',path,body,actor='other')[0].status_code,404)
   self.assertEqual(self.signed('agent','POST',path,body)[0].status_code,409);vault.assert_not_called()
  with broker.db() as c:c.execute("UPDATE connections SET status='ready',version=1 WHERE id=?",(cid,))
  with patch.object(broker,'vault',return_value={'request_id':'receipt','data':{'data':{'value':{'access_token':'SECRET','expires_at':time.time()+300}}}}),patch.object(providers,'read_account',return_value={'messages':[{'id':'abc'}]}):
   r=self.signed('agent','POST',path,body)[0];self.assertEqual(r.status_code,200);self.assertNotIn('SECRET',r.text)
  with broker.db() as c:
   audit=[dict(r) for r in c.execute('SELECT * FROM audit WHERE connection=?',(cid,))]
  self.assertTrue(any(r['action']=='gmail.search' and r['outcome']=='success' for r in audit))
  self.assertNotIn('private search',json.dumps(audit));self.assertNotIn('SECRET',json.dumps(audit))

class AccountPagination(unittest.TestCase):
 def test_both_providers_forward_and_return_cursor(self):
  for provider,action,extra in [('google_email','gmail.search',{}),('google_calendar','calendar.events',{'time_min':'2026-10-01T00:00:00Z','time_max':'2026-10-02T00:00:00Z'})]:
   def respond(req):
    self.assertEqual(req.url.params['pageToken'],'second')
    return httpx.Response(200,json={'nextPageToken':'third','messages':[],'items':[]})
   with httpx.Client(transport=httpx.MockTransport(respond)) as c:
    result=providers.read_account(provider,{'access_token':'private'},action,page_token='second',client=c,**extra)
   self.assertEqual(result['next_page_token'],'third')
 def test_provider_failures_are_classified_without_body_leak(self):
  for code,fragment in [(400,'arguments'),(401,'Authorization'),(403,'scopes'),(404,'not found'),(429,'rate limit'),(503,'temporarily')]:
   with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(code,text='PRIVATE'))) as c:
    with self.assertRaises(providers.ProviderError) as e:providers.read_account('google_email',{'access_token':'private'},'gmail.search',client=c)
   self.assertIn(fragment,str(e.exception));self.assertNotIn('PRIVATE',str(e.exception))

class AccountConcurrency(ProductIdentity):
 def test_provider_read_does_not_hold_global_credential_lock(self):
  import threading
  cid=self.signed('agent','POST','/api/setup',{'provider':'google_email'})[0].json()['connection_id']
  with broker.db() as c:c.execute("UPDATE connections SET status='ready',version=1 WHERE id=?",(cid,))
  def read(*args,**kwargs):
   acquired=[]
   def probe():
    held=broker.LOCK.acquire(timeout=.2);acquired.append(held)
    if held:broker.LOCK.release()
   t=threading.Thread(target=probe);t.start();t.join()
   self.assertEqual(acquired,[True])
   return {'messages':[]}
  with patch.object(broker,'vault',return_value={'data':{'data':{'value':{'access_token':'private','expires_at':time.time()+3600}}}}),patch.object(providers,'read_account',side_effect=read):
   self.assertEqual(self.signed('agent','POST',f'/api/connections/{cid}/read',{'action':'gmail.search'})[0].status_code,200)

class ResearchEvidence(unittest.TestCase):
 def test_thread_pagination_keeps_older_messages_and_participants(self):
  def respond(req):
   self.assertEqual(req.url.path,'/gmail/v1/users/me/threads/thread1')
   return httpx.Response(200,json={'id':'thread1','messages':[{'id':str(i),'payload':{'headers':[{'name':'Cc','value':'person@example.test'},{'name':'Message-ID','value':'source-message'}],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'full older context').decode()}}} for i in range(3)]})
  with httpx.Client(transport=httpx.MockTransport(respond)) as c:
   first=providers.read_account('google_email',{'access_token':'secret'},'gmail.thread',message_id='thread1',limit=2,client=c)
   second=providers.read_account('google_email',{'access_token':'secret'},'gmail.thread',message_id='thread1',limit=2,page_token=first['next_page_token'],client=c)
  self.assertEqual(first['messages'][0]['headers']['cc'],'person@example.test')
  self.assertEqual(first['messages'][0]['headers']['message-id'],'source-message')
  self.assertEqual(second['messages'][0]['body'],'full older context')
  self.assertFalse(second['has_more'])
 def test_calendar_preserves_relationship_and_cancellation_evidence(self):
  event={'id':'e','status':'cancelled','attendees':[{'email':'person@example.test','responseStatus':'declined'}],'organizer':{'email':'host@example.test'},'recurringEventId':'series','attendeesOmitted':True}
  with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'items':[event]}))) as c:
   result=providers.read_account('google_calendar',{'access_token':'secret'},'calendar.events',time_min='2026-07-01T00:00:00Z',time_max='2026-10-01T00:00:00Z',client=c)
  for key,value in event.items():self.assertEqual(result['events'][0][key],value)
