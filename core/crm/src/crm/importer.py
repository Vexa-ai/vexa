"""Describe/CSV importer. Explicit source IDs, typed values, conservative access.

This module never reads expected-visibility.csv: the oracle is independent test input.
Import is operator-only, outside the six subject tools. Reimport preserves local edits.
"""
import csv
import json
import hashlib
from collections import defaultdict
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL, uuid4
from sqlalchemy import select, update, delete
from . import schema as s
from .store import Store


def truth(v): return str(v).lower() in ('true','1','y')
def rid(tenant,org,kind,source): return str(uuid5(NAMESPACE_URL,json.dumps([tenant,org,kind,source])))
def load_export(path):
    base=Path(path)
    return {f.stem:list(csv.DictReader(f.open(newline=''))) for f in (base/'csv').glob('*.csv')}

def definitions(path):
    result={}
    for f in (Path(path)/'describe').glob('*.json'):
        desc=json.loads(f.read_text()); props={}
        for field in desc['fields']:
            kind=field['type']
            typ='boolean' if kind=='boolean' else 'integer' if kind=='int' else 'number' if kind in ('double','percent') else 'string'
            props[field['name']]={'type':[typ,'null'],'title':field.get('label',field['name']),
                'source_type':kind,'referenceTo':field.get('referenceTo',[])}
        result[f.stem]={'type':'object','properties':props,'additionalProperties':False}
    return result

def convert(row,definition):
    out={}
    for k,v in row.items():
        typ=definition['properties'][k]['type'][0]
        out[k]=None if v=='' else truth(v) if typ=='boolean' else int(v) if typ=='integer' else float(v) if typ=='number' else v
    return out

def compile_policies(rows,defs,tenant,org,user_map):
    users={u['Id']:u for u in rows.get('User',[])}
    roles={r['Id']:r.get('ParentRoleId','') for r in rows.get('UserRole',[])}
    groups={g['Id']:g for g in rows.get('Group',[])}
    group_members=defaultdict(set)
    for r in rows.get('GroupMember',[]): group_members[r['GroupId']].add(r['UserOrGroupId'])
    def above(role):
        seen=set()
        while roles.get(role) and roles[role] not in seen:
            role=roles[role];seen.add(role)
        return seen
    def expand(target,seen=None):
        seen=set() if seen is None else seen
        if target in seen: return set()
        seen=seen|{target}
        if target in users:return {target}
        group=groups.get(target,{})
        typ=group.get('Type'); role=group.get('RelatedId')
        if typ=='Organization':return set(users)
        if typ in ('Role','RoleAndSubordinates','RoleAndSubordinatesInternal'):
            return {uid for uid,u in users.items() if u.get('UserRoleId')==role or
                (typ!='Role' and role in above(u.get('UserRoleId','')))}
        out=set()
        for m in group_members[target]:out |= expand(m,seen)
        return out
    def reach(target):
        direct=expand(target);out=set(direct)
        for uid in direct:
            ancestors=above(users[uid].get('UserRoleId',''))
            out|={v for v,u in users.items() if u.get('UserRoleId') in ancestors}
        return out
    levels=defaultdict(lambda:defaultdict(int))
    level={'None':0,'Read':1,'Edit':2,'All':2}
    opp_by_account=defaultdict(list)
    for row in rows.get('Opportunity',[]):opp_by_account[row.get('AccountId')].append(row['Id'])
    for kind,parent,access in [('Account','AccountId','AccountAccessLevel'),('Opportunity','OpportunityId','OpportunityAccessLevel'),('Case','CaseId','CaseAccessLevel')]:
        for share in rows.get(kind+'Share',[]):
            if truth(share.get('IsDeleted')):continue
            for uid in reach(share['UserOrGroupId']):
                levels[uid][(kind,share[parent])]=max(levels[uid][(kind,share[parent])],level.get(share.get(access),0))
                if kind=='Account':
                    for oid in opp_by_account[share[parent]]:
                        levels[uid][('Opportunity',oid)]=max(levels[uid][('Opportunity',oid)],level.get(share.get('OpportunityAccessLevel'),0))
    # Owner access and upward hierarchy also work when the export omits owner share rows.
    for kind in ['Account','Opportunity','Case']:
        for row in rows.get(kind,[]):
            for uid in reach(row.get('OwnerId','')):
                levels[uid][(kind,row['Id'])]=2
    permission_sets={p['Id']:p for p in rows.get('PermissionSet',[])}
    profiles={p['Id']:p for p in rows.get('Profile',[])}
    assignments=defaultdict(set)
    for a in rows.get('PermissionSetAssignment',[]):assignments[a['AssigneeId']].add(a['PermissionSetId'])
    for uid,u in users.items():
        assignments[uid]|={pid for pid,p in permission_sets.items() if p.get('ProfileId')==u['ProfileId'] and truth(p.get('IsOwnedByProfile'))}
    result={}
    for uid,u in users.items():
        if uid not in user_map:continue
        sets=assignments[uid]; profile=profiles.get(u['ProfileId'],{})
        all_permissions=[profile]+[permission_sets[x] for x in sets if x in permission_sets]
        modify_all=any(truth(p.get('PermissionsModifyAllData')) for p in all_permissions)
        view_all=modify_all or any(truth(p.get('PermissionsViewAllData')) for p in all_permissions)
        policy={'active':truth(u.get('IsActive')),'objects':{},'records':{}}
        for kind,definition in defs.items():
            permissions=[p for p in rows.get('ObjectPermissions',[]) if p['ParentId'] in sets and p['SobjectType']==kind]
            def permission(name):return any(truth(p.get(name)) for p in permissions)
            can_read=permission('PermissionsRead');can_edit=permission('PermissionsEdit')
            fields=definition['properties']
            read_fields=[];write_fields=[]
            for name in fields:
                all_fp=[p for p in rows.get('FieldPermissions',[]) if p['SobjectType']==kind and p['Field']==kind+'.'+name]
                fp=[p for p in all_fp if p['ParentId'] in sets]
                if not all_fp or any(truth(p.get('PermissionsRead')) for p in fp):read_fields.append(name)
                if name not in ('Id','CreatedDate','CreatedById','SystemModstamp','LastModifiedDate','LastModifiedById') and (not all_fp or any(truth(p.get('PermissionsEdit')) for p in fp)):write_fields.append(name)
            scope_read='all' if can_read and (view_all or permission('PermissionsViewAllRecords') or permission('PermissionsModifyAllRecords')) else 'grants'
            scope_write='all' if can_edit and (modify_all or permission('PermissionsModifyAllRecords')) else 'grants'
            policy['objects'][kind]={'read':scope_read if can_read else 'none','write':scope_write if can_edit else 'none',
                'read_fields':read_fields,'write_fields':write_fields,'create':permission('PermissionsCreate')}
            for row in rows.get(kind,[]):
                lv=levels[uid].get((kind,row['Id']),0)
                rights=[]
                if lv>=1 and can_read:rights.append('read')
                if lv>=2 and can_edit:rights.append('write')
                if rights:policy['records'][rid(tenant,org,kind,row['Id'])]=rights
        result[user_map[uid]]=policy
    return result

def unresolved_records(rows,defs):
    """Unknown references or share principals require operator reconciliation."""
    identifiers={kind:{r.get('Id') for r in data} for kind,data in rows.items()}
    principals=identifiers.get('User',set())|identifiers.get('Group',set())
    unsupported_groups={r['Id'] for r in rows.get('Group',[]) if r.get('Type') not in
        {'Regular','Queue','Organization','Role','RoleAndSubordinates','RoleAndSubordinatesInternal'}}
    problems=defaultdict(list)
    for kind,data in rows.items():
        if kind not in defs:continue
        for row in data:
            for name,field in defs[kind]['properties'].items():
                value=row.get(name); targets=field.get('referenceTo',[])
                if value and targets and not any(value in identifiers.get(k,set()) for k in targets):
                    problems[(kind,row['Id'])].append('Unresolved reference: '+name)
    for kind,parent in [('Account','AccountId'),('Opportunity','OpportunityId'),('Case','CaseId')]:
        for share in rows.get(kind+'Share',[]):
            target=share.get('UserOrGroupId')
            if target not in principals or target in unsupported_groups:
                problems[(kind,share[parent])].append('Unsupported share principal')
    return problems

def import_export(engine,path,tenant,user_map,actor):
    rows=load_export(path);defs=definitions(path)
    org=rows.get('Organization',[{'Id':'unknown'}])[0]['Id']
    policies=compile_policies(rows,defs,tenant,org,user_map)
    problems=unresolved_records(rows,defs)
    store=Store(engine); receipt=str(uuid4());counts={'created':0,'unchanged':0,'conflicts':0};quarantine=[]
    with engine.begin() as c:
        for kind,definition in defs.items():
            old=c.execute(select(s.object_types.c.definition).where(s.object_types.c.tenant_id==tenant,s.object_types.c.name==kind)).scalar_one_or_none()
            if old is None:c.execute(s.object_types.insert().values(tenant_id=tenant,name=kind,definition=definition))
            elif old!=definition:raise ValueError('Schema changed: explicit schema migration required for '+kind)
        for kind,data in rows.items():
            if kind not in defs:continue
            for raw in data:
                if 'Id' not in raw:continue
                identifier=rid(tenant,org,kind,raw['Id']);fields=convert(raw,defs[kind])
                old=c.execute(select(s.records).where(s.records.c.tenant_id==tenant,s.records.c.id==identifier)).mappings().first()
                if old:
                    if old['fields']==fields:counts['unchanged']+=1
                    else:
                        counts['conflicts']+=1;quarantine.append({'object':kind,'source_id':raw['Id'],'reason':'Existing record differs; explicit reconciliation required'})
                    continue
                issues=problems.get((kind,raw['Id']),[])
                if issues:quarantine.append({'object':kind,'source_id':raw['Id'],'reasons':issues})
                row=dict(tenant_id=tenant,id=identifier,object_type=kind,owner_id=user_map.get(raw.get('OwnerId'),actor),
                    revision=1,fields=fields,narrative='',quarantined=bool(issues),deleted=truth(raw.get('IsDeleted')))
                store._validate(defs[kind],fields)
                c.execute(s.records.insert().values(**row))
                c.execute(s.source_mappings.insert().values(tenant_id=tenant,system='salesforce',org_id=org,object_type=kind,source_id=raw['Id'],record_id=identifier))
                store._revision(c,tenant,actor,None,row,'Imported source record',[{'system':'salesforce','org_id':org,'object_type':kind,'record_id':raw['Id']}])
                counts['created']+=1
        # All mappings must exist before linking forward references.
        imported_ids={rid(tenant,org,kind,row['Id']) for kind,data in rows.items() if kind in defs for row in data if row.get('Id')}
        mappings={(r['object_type'],r['source_id']):r['record_id'] for r in c.execute(
            select(s.source_mappings).where(s.source_mappings.c.tenant_id==tenant,s.source_mappings.c.org_id==org,
                s.source_mappings.c.system=='salesforce')).mappings()}
        links=[]
        for record in c.execute(select(s.records).where(s.records.c.tenant_id==tenant)).mappings():
            if record['id'] not in imported_ids:continue
            for field,definition in defs[record['object_type']]['properties'].items():
                value=record['fields'].get(field)
                if not value:continue
                for kind in definition.get('referenceTo',[]):
                    target=mappings.get((kind,value))
                    if target:
                        links.append(dict(tenant_id=tenant,id=str(uuid4()),record_id=record['id'],kind=field,
                            target_domain='crm',target_id=target))
                        break
        identifiers=list(imported_ids)
        for i in range(0,len(identifiers),500):
            c.execute(delete(s.relationships).where(s.relationships.c.tenant_id==tenant,
                s.relationships.c.target_domain=='crm',s.relationships.c.record_id.in_(identifiers[i:i+500])))
        for i in range(0,len(links),500):c.execute(s.relationships.insert(),links[i:i+500])
        for subject,policy in policies.items():
            existing=c.execute(select(s.policies.c.subject_id).where(s.policies.c.tenant_id==tenant,s.policies.c.subject_id==subject)).first()
            if existing:c.execute(update(s.policies).where(s.policies.c.tenant_id==tenant,s.policies.c.subject_id==subject).values(policy=policy))
            else:c.execute(s.policies.insert().values(tenant_id=tenant,subject_id=subject,policy=policy))
        c.execute(s.import_runs.insert().values(tenant_id=tenant,id=receipt,org_id=org,manifest={**counts,'access_mode':'materialized_snapshot','input_sha256':{str(f.relative_to(path)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(path).rglob('*')) if f.is_file()}},quarantine=quarantine,status='quarantined' if quarantine else 'imported'))
    return {'receipt_id':receipt,'org_id':org,**counts}
