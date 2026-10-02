import csv,json,sys
from pathlib import Path
from crm.importer import load_export,definitions,compile_policies,rid
from crm.store import Store,NotFound
p=Path(sys.argv[1]); rows=load_export(p); defs=definitions(p)
org=rows['Organization'][0]['Id'];users={u['Id']:u['Id'] for u in rows['User']}
policies=compile_policies(rows,defs,'test',org,users)
lookup={kind:{r['Id']:r for r in data if 'Id' in r} for kind,data in rows.items()}
errors=[];checked=0
for expected in csv.DictReader((p.parent/'expected-visibility.csv').open()):
 policy={**policies[expected['UserId']],'subject_id':expected['UserId']}
 raw=lookup[expected['Object']][expected['RecordId']]
 row={'id':rid('test',org,expected['Object'],raw['Id']),'object_type':expected['Object'],'owner_id':raw.get('OwnerId',''),'quarantined':False}
 for permission,column in [('read','can_read'),('write','can_edit')]:
  try:
   Store(None)._rights(policy,row,permission)
   actual=bool(policy['active'])
  except NotFound:actual=False
  if actual!=(expected[column]=='Y'):errors.append({'user':expected['User'],'object':expected['Object'],'record':expected['External_Id__c'],'permission':permission,'expected':expected[column],'actual':actual})
  checked+=1
 if expected['Object']=='Opportunity':
  fields=policy['objects']['Opportunity']['read_fields']
  actual=bool(policy['active']) and expected['can_read']=='Y' and ('*' in fields or 'Fee_Terms__c' in fields)
  if actual!=(expected['can_see_fee_terms']=='Y'):errors.append({'user':expected['User'],'record':expected['External_Id__c'],'permission':'fee','actual':actual})
  checked+=1
print(json.dumps({'checked':checked,'mismatches':len(errors),'examples':errors[:15]},indent=2))
sys.exit(bool(errors))
