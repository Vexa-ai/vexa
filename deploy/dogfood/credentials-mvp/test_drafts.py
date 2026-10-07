import unittest,time,json,base64
from unittest.mock import patch
import httpx
from test_product_identity import ProductIdentity,broker,providers,TEMP
from pathlib import Path
class Drafts(ProductIdentity):
 def test_permission_required_then_idempotent_creation(self):
  cid=self.signed('agent','POST','/api/setup',{'provider':'google_email'})[0].json()['connection_id']
  with broker.db() as c:c.execute("UPDATE connections SET status='ready',version=1 WHERE id=?",(cid,))
  Path(TEMP.name,'broker.token').write_text('fixture-key')
  payload={'request_id':'fixture-draft-'+cid,'recipient':'test@example.test','subject':'fixture','body':'private draft'}
  path=f'/api/connections/{cid}/draft';value={'access_token':'TOKEN','expires_at':time.time()+500,'scope':''}
  with patch.object(broker,'vault',return_value={'data':{'data':{'value':value}}}),patch.object(providers,'create_gmail_draft',return_value={'draft_id':'draft','status':'draft_created','sent':False}) as create:
   self.assertEqual(self.signed('agent','POST',path,payload)[0].json()['status'],'permission_required');create.assert_not_called()
   value['scope']=providers.DRAFT_SCOPE
   for _ in range(2):self.assertEqual(self.signed('agent','POST',path,payload)[0].json()['status'],'draft_created')
   self.assertEqual(create.call_count,1)
   self.assertEqual(self.signed('agent','POST',path,{**payload,'body':'changed'})[0].status_code,409)
   self.assertEqual(self.signed('agent','POST',path,payload,actor='other')[0].status_code,404)
 def test_provider_draft_only_and_header_injection_refused(self):
  def respond(req):
   self.assertEqual(str(req.url),'https://gmail.googleapis.com/gmail/v1/users/me/drafts')
   raw=json.loads(req.content)['message']['raw'];decoded=base64.urlsafe_b64decode(raw).decode()
   self.assertIn('Subject: fixture',decoded)
   return httpx.Response(200,json={'id':'draft','message':{'id':'msg'},'access_token':'hidden'})
  value={'access_token':'token','scope':providers.DRAFT_SCOPE}
  with httpx.Client(transport=httpx.MockTransport(respond)) as c:
   self.assertFalse(providers.create_gmail_draft(value,'test@example.test','fixture','body',client=c)['sent'])
   with self.assertRaises(providers.ProviderError):providers.create_gmail_draft(value,'test@example.test','x\r\nBcc: other@example.test','body',client=c)
