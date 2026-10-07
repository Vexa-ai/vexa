"""Operator-only isolated harness bootstrap. Never emits keys/tokens."""
import json,os,secrets,urllib.request,urllib.error,time
from pathlib import Path
os.umask(0o077)
root=Path(__file__).resolve().parent
for name in ('bao','audit','broker','fixture','operator'):
 (root/'state'/name).mkdir(parents=True,exist_ok=True,mode=0o700)
addr=os.environ.get('BAO_ADDR','http://bao:8200')+'/v1/'
def call(method,path,body=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['X-Vault-Token']=token
 r=urllib.request.Request(addr+path,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None)
 try:
  with urllib.request.urlopen(r,timeout=60) as res:return json.load(res) if res.status!=204 else {}
 except urllib.error.HTTPError as e:
  info=json.loads(e.read()).get('errors',[])
  raise RuntimeError(f'{path}: HTTP {e.code}; {info}') from None
recovery=root/'state/operator/recovery.json'
if not recovery.exists():
 init=call('PUT','sys/init',{'secret_shares':1,'secret_threshold':1});recovery.write_text(json.dumps(init));recovery.chmod(0o600)
else:init=json.loads(recovery.read_text())
call('PUT','sys/unseal',{'key':init['keys'][0]})
token=init['root_token']
mounts=call('GET','sys/mounts',token=token)
if 'connections/' not in mounts:
 call('POST','sys/mounts/connections',{'type':'kv','options':{'version':'2'}},token)
audit=call('GET','sys/audit',token=token)
if 'file/' not in audit:
 call('PUT','sys/audit/file',{'type':'file','options':{'file_path':'/bao/audit/events.jsonl','log_raw':'false'}},token)
policy='path "connections/data/*" { capabilities = ["create", "update", "read"] }'
call('PUT','sys/policies/acl/mvp-broker',{'policy':policy},token)
f=root/'state/broker/broker.token'
if not f.exists():
 issued=call('POST','auth/token/create',{'policies':['mvp-broker'],'no_default_policy':True,'ttl':'24h','renewable':False,'display_name':'mvp-connection-broker'},token)
 f.write_text(issued['auth']['client_token']);f.chmod(0o600)
k=root/'state/fixture/key'
if not k.exists():k.write_text('mvp-test-'+secrets.token_urlsafe(32));k.chmod(0o600)
print('Initialized and unsealed isolated OpenBao; audit enabled; restricted broker token installed. No secrets printed.')
