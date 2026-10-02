"""Explicit CRM-only initial migration; run with the migration owner's credential."""
import os
from sqlalchemy import create_engine, text
from .schema import metadata

def migrate(engine):
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Migration requires PostgreSQL')
    with engine.begin() as c:
        metadata.create_all(c)
        for table in metadata.sorted_tables:
            for index in table.indexes:index.create(c,checkfirst=True)
        c.execute(text('''CREATE OR REPLACE FUNCTION crm_history_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION 'CRM revisions are append-only';
            END $$'''))
        c.execute(text('DROP TRIGGER IF EXISTS crm_card_layouts_immutable ON crm_card_layouts'))
        c.execute(text('''CREATE TRIGGER crm_card_layouts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON crm_card_layouts FOR EACH STATEMENT EXECUTE FUNCTION crm_history_immutable()'''))
        c.execute(text('DROP TRIGGER IF EXISTS crm_revisions_immutable ON crm_revisions'))
        c.execute(text('''CREATE TRIGGER crm_revisions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON crm_revisions FOR EACH STATEMENT EXECUTE FUNCTION crm_history_immutable()'''))

if __name__ == '__main__':
    migrate(create_engine(os.environ['CRM_DATABASE_URL']))
