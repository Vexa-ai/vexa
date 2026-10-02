"""Optional CRM database. Metadata is never installed by another Vexa domain."""
from sqlalchemy import JSON, Boolean, Column, ForeignKeyConstraint, Integer, Index, MetaData, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
Document = JSON().with_variant(JSONB(), 'postgresql')
def tenant():
    return Column('tenant_id', String, primary_key=True)
def record_fk():
    return ForeignKeyConstraint(['tenant_id', 'record_id'], ['crm_records.tenant_id', 'crm_records.id'])

object_types = Table('crm_object_definitions', metadata, tenant(),
    Column('name', String, primary_key=True), Column('definition', Document, nullable=False))
records = Table('crm_records', metadata, tenant(), Column('id', String, primary_key=True),
    Column('object_type', String, nullable=False), Column('owner_id', String, nullable=False),
    Column('revision', Integer, nullable=False), Column('fields', Document, nullable=False),
    Column('narrative', Text, nullable=False), Column('quarantined', Boolean, nullable=False),
    Column('deleted', Boolean, nullable=False),
    ForeignKeyConstraint(['tenant_id', 'object_type'], ['crm_object_definitions.tenant_id', 'crm_object_definitions.name']))
# Policies are provisioned by import/admin, never by the record writer.
# One policy per tenant+subject includes membership, per-object fields, and record grants.
policies = Table('crm_access_policies', metadata, tenant(), Column('subject_id', String, primary_key=True),
    Column('policy', Document, nullable=False))
source_mappings = Table('crm_source_mappings', metadata, tenant(),
    Column('system', String, primary_key=True), Column('org_id', String, primary_key=True),
    Column('object_type', String, primary_key=True), Column('source_id', String, primary_key=True),
    Column('record_id', String, nullable=False), record_fk())
relationships = Table('crm_relationships', metadata, tenant(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('kind', String, nullable=False),
    Column('target_domain', String, nullable=False), Column('target_id', String, nullable=False), record_fk())
revisions = Table('crm_revisions', metadata, tenant(), Column('record_id', String, primary_key=True),
    Column('revision', Integer, primary_key=True), Column('actor_id', String, nullable=False),
    Column('created_at', String, nullable=False), Column('reason', Text, nullable=False),
    Column('before', Document), Column('after', Document, nullable=False),
    Column('evidence', Document, nullable=False), record_fk())
change_proposals = Table('crm_change_proposals', metadata, tenant(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('base_revision', Integer, nullable=False),
    Column('author_id', String, nullable=False), Column('patch', Document, nullable=False),
    Column('evidence', Document, nullable=False), Column('reason', Text, nullable=False),
    Column('status', String, nullable=False), record_fk())
import_runs = Table('crm_import_receipts', metadata, tenant(), Column('id', String, primary_key=True),
    Column('org_id', String, nullable=False), Column('manifest', Document, nullable=False),
    Column('quarantine', Document, nullable=False), Column('status', String, nullable=False))
outbox = Table('crm_outbox', metadata, tenant(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('event', Document, nullable=False), Column('delivered', Boolean, nullable=False),
    UniqueConstraint('tenant_id', 'record_id', 'revision'),
    ForeignKeyConstraint(['tenant_id', 'record_id', 'revision'],
                         ['crm_revisions.tenant_id', 'crm_revisions.record_id', 'crm_revisions.revision']))

Index("crm_records_by_type", records.c.tenant_id, records.c.object_type, records.c.id)
Index("crm_records_by_owner", records.c.tenant_id, records.c.owner_id, records.c.object_type)

Index("crm_sources_by_record", source_mappings.c.tenant_id, source_mappings.c.record_id)
Index("crm_links_by_record", relationships.c.tenant_id, relationships.c.record_id)
