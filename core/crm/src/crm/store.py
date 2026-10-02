"""Permission-aware record operations shared by HTTP, MCP and Minutes."""
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
import json
import hashlib
from urllib.parse import urlencode
from jsonschema import Draft202012Validator
from sqlalchemy import select, update, delete, or_, false, text
from . import schema as s
from .cards import CardLayout, visible_layout

class CRMError(Exception):
    status = 400
class Forbidden(CRMError):
    status = 403
class NotFound(CRMError):
    status = 404
class Conflict(CRMError):
    status = 409

class Store:
    def __init__(self, engine):
        self.engine = engine

    def _policy(self, c, tenant, actor):
        value = c.execute(select(s.policies.c.policy).where(
            s.policies.c.tenant_id == tenant, s.policies.c.subject_id == actor)).scalar_one_or_none()
        if not value or not value.get('active', False):
            raise Forbidden('No active CRM membership')
        return {**value, "subject_id": actor}

    def _definition(self, c, tenant, kind):
        value = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.tenant_id == tenant, s.object_types.c.name == kind)).scalar_one_or_none()
        if value is None:
            raise NotFound('Unknown object type')
        return value

    def _rights(self, policy, row, permission):
        if policy.get('admin'):
            return ['*']
        if row.get('quarantined'):
            raise NotFound('Record not found')
        rule = policy.get('objects', {}).get(row['object_type'], {})
        scope = rule.get(permission, 'none')
        granted = policy.get('records', {}).get(row['id'], [])
        allowed = scope == 'all' or (scope == 'own' and row['owner_id'] == policy.get('subject_id')) or permission in granted
        if not allowed:
            raise NotFound('Record not found')
        return rule.get(permission + '_fields', [])

    def _row(self, c, tenant, record_id):
        row = c.execute(select(s.records).where(s.records.c.tenant_id == tenant, s.records.c.id == record_id)).mappings().first()
        if row is None or row['deleted']:
            raise NotFound('Record not found')
        return dict(row)

    def _visible(self, policy, row):
        fields = self._rights(policy, row, 'read')
        result = {k: row[k] for k in ('id', 'tenant_id', 'object_type', 'revision')}
        result['quarantined'] = row['quarantined']
        result['fields'] = {k: v for k, v in row['fields'].items() if '*' in fields or k in fields}
        # Narrative may contain any field: partial-field users cannot read it.
        result['narrative'] = row['narrative'] if '*' in fields else None
        return result

    def _card(self, c, tenant, kind):
        row = c.execute(select(s.card_layouts).where(s.card_layouts.c.tenant_id == tenant,
            s.card_layouts.c.object_type == kind).order_by(s.card_layouts.c.version.desc()).limit(1)).mappings().first()
        return {'version': row['version'], 'layout': row['layout']} if row else {'version': 0, 'layout': None}

    def configure(self, tenant, actor, object_type, layout=None, expected_version=0, reason=''):
        with self.engine.begin() as c:
            policy = self._policy(c, tenant, actor)
            if not policy.get('admin'):
                raise Forbidden('CRM administrator rights required to configure shared cards')
            # Serialize the first insert too, without granting writes on imported definitions.
            if self.engine.dialect.name == 'postgresql':
                lock = int.from_bytes(hashlib.sha256(json.dumps([tenant, object_type]).encode()).digest()[:8], 'big', signed=True)
                c.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
            definition = c.execute(select(s.object_types.c.definition).where(
                s.object_types.c.tenant_id == tenant, s.object_types.c.name == object_type)).scalar_one_or_none()
            if definition is None: raise NotFound('Unknown object type')
            current = self._card(c, tenant, object_type)
            if layout is None: return current
            if expected_version != current['version']: raise Conflict('Card layout changed; read configuration again')
            if not reason.strip(): raise CRMError('Configuration changes require a reason')
            try: layout = CardLayout.model_validate(layout).model_dump()
            except ValueError as e: raise CRMError('Invalid card layout: '+str(e)) from e
            properties = definition.get('properties', {})
            references = [layout['title_field']] + [f['field'] for sec in layout['sections'] for f in sec['fields']]
            if any(name and name not in properties for name in references): raise CRMError('Unknown field in card layout')
            version = current['version'] + 1
            c.execute(s.card_layouts.insert().values(tenant_id=tenant, object_type=object_type, version=version,
                layout=layout, actor_id=actor, reason=reason, created_at=datetime.now(timezone.utc).isoformat()))
            return {'version': version, 'layout': layout}

    def describe(self, tenant, actor, object_type=None):
        with self.engine.connect() as c:
            policy = self._policy(c, tenant, actor)
            rows = c.execute(select(s.object_types).where(s.object_types.c.tenant_id == tenant)).mappings()
            result = []
            for row in rows:
                kind = row['name']
                if object_type and kind != object_type:
                    continue
                rule = policy.get('objects', {}).get(kind, {})
                if not policy.get('admin') and rule.get('read', 'none') == 'none':
                    continue
                allowed = ['*'] if policy.get('admin') else rule.get('read_fields', [])
                definition = row['definition']
                result.append({'object_type': kind, 'fields': {k: v for k, v in definition.get('properties', {}).items() if '*' in allowed or k in allowed}})
            return {'objects': result}

    def get(self, tenant, actor, record_id):
        with self.engine.connect() as c:
            policy = self._policy(c, tenant, actor)
            row=self._row(c, tenant, record_id)
            result=self._visible(policy,row)
            card=self._card(c,tenant,row['object_type'])
            result['card']={**card,'layout':visible_layout(card['layout'],result['fields']) if card['layout'] else None}
            result['href']='/crm?'+urlencode({'record':record_id})
            result['sources']=[dict(r) for r in c.execute(select(s.source_mappings.c.system,
                s.source_mappings.c.org_id,s.source_mappings.c.object_type,s.source_mappings.c.source_id).where(
                s.source_mappings.c.tenant_id==tenant,s.source_mappings.c.record_id==record_id)).mappings()]
            result['links']=[]
            for link in c.execute(select(s.relationships).where(s.relationships.c.tenant_id==tenant,
                    s.relationships.c.record_id==record_id)).mappings():
                if link['kind'] not in result['fields'] or link['target_domain']!='crm':continue
                try:
                    target=self._visible(policy,self._row(c,tenant,link['target_id']))
                except NotFound:continue
                result['links'].append({'field':link['kind'],'record_id':target['id'],
                    'object_type':target['object_type'], 'label':target['fields'].get('Name') or ' '.join(str(target['fields'].get(k) or '') for k in ('FirstName','LastName')).strip() or target['object_type']})
            can_write=False
            try:can_write=bool(self._rights(policy,row,'write'))
            except NotFound:pass
            can_review=can_write and bool(policy.get('admin') or policy.get('reviewer'))
            result['actions']={'can_review':can_review,'can_update':can_write and not policy.get('review_required',False),'can_propose':True}
            result['proposals']=[]
            query=select(s.change_proposals).where(s.change_proposals.c.tenant_id==tenant,
                s.change_proposals.c.record_id==record_id,s.change_proposals.c.status=='pending').limit(50)
            if not can_review:query=query.where(s.change_proposals.c.author_id==actor)
            allowed=self._rights(policy,row,'read')
            for proposal in c.execute(query).mappings():
                result['proposals'].append({'id':proposal['id'],'base_revision':proposal['base_revision'],
                    'fields':{k:v for k,v in proposal['patch']['fields'].items() if '*' in allowed or k in allowed},
                    'reason':proposal['reason'] if '*' in allowed else None,
                    'evidence':proposal['evidence'] if '*' in allowed else []})
            return result

    def search(self, tenant, actor, object_type, filters=None, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise CRMError('Limit must be 1..100 and offset nonnegative')
        with self.engine.connect() as c:
            policy = self._policy(c, tenant, actor)
            definition = self._definition(c, tenant, object_type)
            allowed = ['*'] if policy.get('admin') else policy.get('objects', {}).get(object_type, {}).get('read_fields', [])
            for name in (filters or {}):
                if name not in definition.get('properties', {}) or ('*' not in allowed and name not in allowed):
                    raise Forbidden('Filter field unavailable')
            query = select(s.records).where(s.records.c.tenant_id == tenant,
                s.records.c.object_type == object_type, s.records.c.deleted == False).order_by(s.records.c.id)
            if not policy.get('admin'):
                rule=policy.get('objects',{}).get(object_type,{})
                scope=rule.get('read','none')
                granted=[record_id for record_id, rights in policy.get('records',{}).items() if 'read' in rights]
                access=s.records.c.id.in_(granted) if granted else false()
                if scope=='all':
                    access=s.records.c.tenant_id==tenant
                elif scope=='own':
                    access=or_(access,s.records.c.owner_id==actor)
                query=query.where(access,s.records.c.quarantined==False)
            for name,value in (filters or {}).items():
                errors=list(Draft202012Validator(definition['properties'][name]).iter_errors(value))
                if errors:raise CRMError('Invalid filter value for '+name)
                column=s.records.c.fields[name]
                if isinstance(value,bool):column=column.as_boolean()
                elif isinstance(value,int):column=column.as_integer()
                elif isinstance(value,float):column=column.as_float()
                else:column=column.as_string()
                query=query.where(column==value)
            rows=c.execute(query.offset(offset).limit(limit+1)).mappings().all()
            result=[self._visible(policy,dict(row)) for row in rows[:limit]]
            for record in result:record['href']='/crm?'+urlencode({'record':record['id']})
            by_id={record['id']:record for record in result}
            links=c.execute(select(s.relationships).where(s.relationships.c.tenant_id==tenant,
                s.relationships.c.record_id.in_(by_id),s.relationships.c.target_domain=='crm')).mappings().all() if by_id else []
            target_ids={link['target_id'] for link in links}
            targets={}
            if target_ids:
                for target in c.execute(select(s.records).where(s.records.c.tenant_id==tenant,
                        s.records.c.id.in_(target_ids),s.records.c.deleted==False)).mappings():
                    try:targets[target['id']]=self._visible(policy,dict(target))
                    except NotFound:pass
            for link in links:
                record=by_id[link['record_id']]
                target=targets.get(link['target_id'])
                if target and link['kind'] in record['fields']:
                    record.setdefault('links',[]).append({'field':link['kind'],'record_id':target['id'],
                        'label':target['fields'].get('Name') or ' '.join(str(target['fields'].get(k) or '') for k in ('FirstName','LastName')).strip() or target['object_type']})
            card=self._card(c,tenant,object_type)
            readable=set(definition.get('properties',{})) if '*' in allowed else set(allowed)
            return {'records':result,'next_offset':offset+limit if len(rows)>limit else None,
                    'card':{**card,'layout':visible_layout(card['layout'],readable) if card['layout'] else None},
                    'href':'/crm?'+urlencode({'object':object_type,'filters':json.dumps(filters or {},separators=(',',':'))})}

    def resolve_name(self, tenant, actor, name):
        """Case-insensitive entity names, preferring exact matches over readable prefixes."""
        from sqlalchemy import func
        name=name.strip().casefold()
        if not name:raise CRMError('Entity name is required')
        with self.engine.connect() as c:
            policy=self._policy(c,tenant,actor)
            fields=s.records.c.fields
            full_name=func.trim(func.coalesce(fields['FirstName'].as_string(),'')+' '+func.coalesce(fields['LastName'].as_string(),''))
            query=select(s.records).where(s.records.c.tenant_id==tenant,s.records.c.deleted==False,
                or_(*[func.lower(fields[k].as_string()).startswith(name,autoescape=True) for k in ('Name','name','Subject')],func.lower(full_name).startswith(name,autoescape=True))).order_by(s.records.c.id)
            matches=[];prefixes=[]
            for row in c.execute(query).mappings():
                try:record=self._visible(policy,dict(row))
                except NotFound:continue
                visible=record['fields']
                labels=[str(visible.get(k) or '') for k in ('Name','name','Subject')]
                labels.append(' '.join(str(visible.get(k) or '') for k in ('FirstName','LastName')).strip())
                exact=any(label.casefold()==name for label in labels)
                if not exact and not any(label.casefold().startswith(name) for label in labels):continue
                record['href']='/crm?'+urlencode({'record':record['id']})
                (matches if exact else prefixes).append(record)
            return {'records':matches or prefixes}

    def _validate(self, definition, fields):
        errors = sorted(Draft202012Validator(definition).iter_errors(fields), key=lambda e: str(e.path))
        if errors:
            raise CRMError('Invalid fields: ' + errors[0].message)

    def _sync_relationships(self,c,row):
        tenant=row['tenant_id']
        definition=self._definition(c,tenant,row['object_type'])
        c.execute(delete(s.relationships).where(s.relationships.c.tenant_id==tenant,
            s.relationships.c.record_id==row['id'],s.relationships.c.target_domain=='crm'))
        for name,field in definition.get('properties',{}).items():
            targets=field.get('referenceTo',[])
            value=row['fields'].get(name)
            if not targets or not value:continue
            # Source references resolve within the importing source org, never across orgs.
            sources=c.execute(select(s.source_mappings.c.system,s.source_mappings.c.org_id).where(
                s.source_mappings.c.tenant_id==tenant,s.source_mappings.c.record_id==row['id'])).all()
            for system,org in sources:
                target=c.execute(select(s.source_mappings.c.record_id).where(s.source_mappings.c.tenant_id==tenant,
                    s.source_mappings.c.system==system,s.source_mappings.c.org_id==org,
                    s.source_mappings.c.object_type.in_(targets),s.source_mappings.c.source_id==value)).scalar_one_or_none()
                if target:
                    c.execute(s.relationships.insert().values(tenant_id=tenant,id=str(uuid4()),record_id=row['id'],
                        kind=name,target_domain='crm',target_id=target))

    def _revision(self, c, tenant, actor, before, after, reason, evidence):
        c.execute(s.revisions.insert().values(tenant_id=tenant, record_id=after['id'], revision=after['revision'],
            actor_id=actor, created_at=datetime.now(timezone.utc).isoformat(), reason=reason,
            before=before, after=after, evidence=evidence))
        event_id=str(uuid4())
        c.execute(s.outbox.insert().values(tenant_id=tenant, id=event_id, record_id=after['id'], revision=after['revision'],
            event={'event_type':'crm.record.changed', 'source_event_id':event_id,
                   'subject_refs':{'tenant_id':tenant,'record_id':after['id'],'revision':after['revision'],'uid':actor}}, delivered=False))

    def create(self, tenant, actor, object_type, fields, narrative='', reason='', evidence=None, idempotency_key=''):
        with self.engine.begin() as c:
            policy=self._policy(c,tenant,actor)
            rule=policy.get('objects',{}).get(object_type,{})
            if not policy.get('admin') and not rule.get('create'):
                raise Forbidden('Create not permitted')
            allowed=['*'] if policy.get('admin') else rule.get('write_fields',[])
            if '*' not in allowed and (set(fields)-set(allowed) or narrative):
                raise Forbidden('Field write not permitted')
            self._validate(self._definition(c,tenant,object_type), fields)
            record_id=str(uuid5(NAMESPACE_URL,json.dumps(['crm-create',tenant,actor,idempotency_key]))) if idempotency_key else str(uuid4())
            if idempotency_key:
                # Serialize concurrent retries without a process-local lock or another table.
                if self.engine.dialect.name=='postgresql':
                    from sqlalchemy import text
                    c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':record_id})
                original=c.execute(select(s.revisions.c.after).where(s.revisions.c.tenant_id==tenant,
                    s.revisions.c.record_id==record_id,s.revisions.c.revision==1)).scalar_one_or_none()
                if original:
                    if original['object_type']!=object_type or original['fields']!=fields or original['narrative']!=narrative:
                        raise Conflict('Idempotency key was used for a different create request')
                    self._rights(policy,self._row(c,tenant,record_id),'read')
                    return {'status':'saved','record_id':record_id,'revision':1,'replayed':True}
            row=dict(tenant_id=tenant,id=record_id,object_type=object_type,owner_id=actor,
                     revision=1,fields=fields,narrative=narrative,quarantined=False,deleted=False)
            c.execute(s.records.insert().values(**row))
            self._revision(c,tenant,actor,None,row,reason,evidence or [])
            return {'status':'saved','record_id':row['id'],'revision':1}

    def _edit(self,c,tenant,actor,policy,record_id,expected_revision,fields,narrative,reason,evidence):
        row=self._row(c,tenant,record_id)
        self._rights(policy,row,'read')
        allowed=self._rights(policy,row,'write')
        if '*' not in allowed and (set(fields)-set(allowed) or narrative is not None):
            raise Forbidden('Field write not permitted')
        if row['revision'] != expected_revision:
            raise Conflict('Record changed; read the current revision')
        after={**row,'fields':{**row['fields'],**fields},'revision':row['revision']+1}
        if narrative is not None:
            after['narrative']=narrative
        self._validate(self._definition(c,tenant,row['object_type']),after['fields'])
        changed=c.execute(update(s.records).where(s.records.c.tenant_id==tenant,s.records.c.id==record_id,
                          s.records.c.revision==expected_revision).values(**after)).rowcount
        if changed != 1:
            raise Conflict('Record changed; read the current revision')
        self._sync_relationships(c,after)
        self._revision(c,tenant,actor,row,after,reason,evidence)
        return {'status':'saved','record_id':record_id,'revision':after['revision']}

    def change(self,tenant,actor,record_id,expected_revision,fields,narrative=None,reason='',evidence=None,propose=False):
        with self.engine.begin() as c:
            policy=self._policy(c,tenant,actor)
            if propose:
                row=self._row(c,tenant,record_id)
                visible=self._visible(policy,row)
                if row['revision']!=expected_revision:
                    raise Conflict('Record changed; read the current revision')
                allowed=self._rights(policy,row,'read')
                if ('*' not in allowed and set(fields)-set(allowed)) or (visible['narrative'] is None and narrative is not None):
                    raise Forbidden('Cannot propose changes to unreadable fields')
                self._validate(self._definition(c,tenant,row['object_type']),{**row['fields'],**fields})
                proposal=str(uuid4())
                c.execute(s.change_proposals.insert().values(tenant_id=tenant,id=proposal,record_id=record_id,
                    base_revision=expected_revision,author_id=actor,patch={'fields':fields,'narrative':narrative},
                    evidence=evidence or [],reason=reason,status='pending'))
                return {'status':'proposed','proposal_id':proposal}
            if policy.get('review_required') and not policy.get('admin'):
                raise Forbidden('This caller must submit a proposal')
            return self._edit(c,tenant,actor,policy,record_id,expected_revision,fields,narrative,reason,evidence or [])

    def history(self,tenant,actor,record_id):
        with self.engine.connect() as c:
            policy=self._policy(c,tenant,actor)
            row=self._row(c,tenant,record_id)
            allowed=self._rights(policy,row,'read')
            result=[]
            for revision in c.execute(select(s.revisions).where(s.revisions.c.tenant_id==tenant,
                    s.revisions.c.record_id==record_id).order_by(s.revisions.c.revision.desc()).limit(100)).mappings():
                # Reason/evidence/snapshots can reveal restricted fields. Partial readers get
                # filtered snapshots, but no free-form reason/evidence or historical ownership.
                result.append({'revision':revision['revision'],'created_at':revision['created_at'],
                    'before':self._visible(policy,revision['before']) if revision['before'] else None,
                    'after':self._visible(policy,revision['after']),
                    'reason':revision['reason'] if '*' in allowed else None,
                    'evidence':revision['evidence'] if '*' in allowed else []})
            return {'revisions':result}

    def review(self,tenant,actor,proposal_id,accept):
        with self.engine.begin() as c:
            policy=self._policy(c,tenant,actor)
            if not policy.get('admin') and not policy.get('reviewer'):
                raise Forbidden('Review not permitted')
            p=c.execute(select(s.change_proposals).where(s.change_proposals.c.tenant_id==tenant,
                         s.change_proposals.c.id==proposal_id).with_for_update()).mappings().first()
            if not p:
                raise NotFound('Proposal not found')
            if p['status']!='pending':
                raise Conflict('Proposal already reviewed')
            self._rights(policy,self._row(c,tenant,p['record_id']),'write')
            result={'status':'rejected','proposal_id':proposal_id}
            if accept:
                result=self._edit(c,tenant,actor,policy,p['record_id'],p['base_revision'],
                    p['patch']['fields'],p['patch']['narrative'],p['reason'],p['evidence'])
            c.execute(update(s.change_proposals).where(s.change_proposals.c.tenant_id==tenant,
                s.change_proposals.c.id==proposal_id).values(status='accepted' if accept else 'rejected'))
            return result
