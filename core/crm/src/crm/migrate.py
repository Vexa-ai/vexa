"""The CRM's migrations path and its own Postgres role (ADR-0047 § Storage; ADR-0048's pattern).

crm-api never holds the instance's admin credential and runs no DDL. The migrations path — compose
`crm-init`, the Helm `crm-schema` Job — runs this module with that credential and:

1. creates (or re-asserts) a LOGIN role, by default ``vexa_crm``, with no admin power and the
   password it is given;
2. creates the ``crm_*`` tables and the append-only triggers on revisions, card layouts, the audit
   and desk layout versions, and
   records the schema version;
3. grants that role exactly what the record writer needs — SELECT on every CRM table, and the
   writes below — and nothing on any other table, so it cannot read accounts, tokens or meetings;
4. creates the organisation and its stable entity types if they are missing (`bootstrap`);
5. creates the two functions crm-api calls for an individual's personal CRM (``personal_crm``), and
   lets that role EXECUTE them and nothing more: ``crm_bootstrap_personal_org`` makes one on first
   use, ``crm_erase_personal_org`` erases one on account delete (``erase_plan``). Both are SECURITY
   DEFINER, accept only an org of one (``p-<id>``), and are re-created on every ``init`` from this
   code, so the definitions and the erase plan they carry follow the release.

crm-api then logs in as that role and only CHECKS (``check``): not a superuser, and the schema
version this code knows.

    python -m crm.migrate init    # admin DB_* + CRM_DB_USER / CRM_DB_PASSWORD + CRM_ORG_ID
    python -m crm.migrate check   # the role's own login (CRM_DATABASE_URL); exits 0 once ready
"""
import json
import os
import re
import sys
import time
from urllib.parse import quote

from sqlalchemy import create_engine, text

from .schema import SCHEMA_VERSION, metadata, schema_version

DEFAULT_ROLE = 'vexa_crm'
_ROLE = re.compile(r'^[a-z_][a-z0-9_]{2,62}$')
_PASSWORD = re.compile(r'^[A-Za-z0-9._~-]{24,256}$')
_RESERVED = {'postgres', 'root', 'admin', 'rdsadmin', 'azure_superuser', 'cloudsqlsuperuser'}

#: The record writer's rights, table by table. SELECT on every CRM table is granted besides these.
#: Revisions, layouts and the audit are insert-only (and triggers refuse their mutation); policies,
#: definitions, the organisation, imports and source mappings are configuration the migrations path
#: and the importer write, never crm-api.
WRITES = {
    'crm_records': 'INSERT, UPDATE',
    'crm_change_proposals': 'INSERT, UPDATE',
    'crm_outbox': 'INSERT, UPDATE',
    'crm_relationships': 'INSERT, DELETE',
    'crm_revisions': 'INSERT',
    'crm_card_layouts': 'INSERT',
    'crm_audit': 'INSERT',
    'crm_match_ids': 'INSERT, UPDATE',
    'crm_page_references': 'INSERT, DELETE',
    'crm_workspace_rosters': 'INSERT, DELETE',
    'crm_workspace_heads': 'INSERT, UPDATE',
    'crm_workspace_bindings': 'INSERT, DELETE',
    'crm_desk': 'INSERT, UPDATE, DELETE',
    'crm_desk_layouts': 'INSERT',
    'crm_desk_proposals': 'INSERT, UPDATE',
    'crm_card_members': 'INSERT, UPDATE, DELETE',
    'crm_card_seen': 'INSERT, UPDATE',
    'crm_member_settings': 'INSERT, UPDATE',
    'crm_profiles': 'INSERT, UPDATE',
    'crm_card_nonces': 'INSERT, UPDATE',
}

#: Columns schema 6 adds to an existing ``crm_audit`` (``tables_and_version``).
AUDIT_COLUMNS_6 = ('principal', 'on_behalf_of', 'workspace')
#: Columns schema 8 adds to an existing ``crm_revisions`` (D13 decision records), in place with an
#: empty default so every existing append-only row keeps its values.
REVISION_COLUMNS_7 = ('decision', 'credential_class', 'nonce', 'content_hash')

APPEND_ONLY = ('crm_revisions', 'crm_card_layouts', 'crm_audit', 'crm_desk_layouts')

#: TODO(founder decision pending): how an individual's append-only rows (``APPEND_ONLY``: revisions,
#: card layouts, the audit, desk layout versions — UPDATE and DELETE refused by their triggers) are
#: erased with their personal CRM on account delete. Until that is decided, the erase leaves those
#: tables untouched and answers and logs what it left (``store.Store.erase_org``). Their foreign keys
#: then hold two more tables: the records the revisions name stay as empty, deleted rows, and the
#: object definitions the records and card layouts name stay. Turning this on needs that decision
#: and an erase path the triggers allow; ``erase_function_sql`` refuses to build one before then.
ERASE_APPEND_ONLY = False

#: The functions crm-api's role may EXECUTE (``init`` grants them; nothing else is granted on them).
BOOTSTRAP_FN = 'crm_bootstrap_personal_org(text, text)'
ERASE_FN = 'crm_erase_personal_org(text)'
_PERSONAL_ORG_SQL = '^p-[0-9]+$'          # orgs.PERSONAL_ORG, as Postgres reads it


def erase_plan(erase_append_only=ERASE_APPEND_ONLY):
    """``(table, action)`` for every org-keyed CRM table, children before parents. ``delete``;
    ``leave`` (append-only, untouched); ``tombstone`` (records the kept revisions point at: emptied
    and marked deleted); ``pin`` (object definitions the kept rows point at: left as they are)."""
    plan = []
    for table in reversed(metadata.sorted_tables):
        if 'org_id' not in table.c:
            continue
        if erase_append_only:
            plan.append((table.name, 'delete'))
        elif table.name in APPEND_ONLY:
            plan.append((table.name, 'leave'))
        elif table.name == 'crm_records':
            plan.append((table.name, 'tombstone'))
        elif table.name == 'crm_object_definitions':
            plan.append((table.name, 'pin'))
        else:
            plan.append((table.name, 'delete'))
    return plan


def _literal(value):
    return "'" + json.dumps(value, sort_keys=True).replace("'", "''") + "'::jsonb"


def erase_function_sql():
    """``crm_erase_personal_org(org)``: ``erase_plan`` as one SECURITY DEFINER function."""
    if ERASE_APPEND_ONLY:
        raise NotImplementedError('erasing the append-only tables is not decided; see ERASE_APPEND_ONLY')
    steps = []
    for name, action in erase_plan():
        if action == 'delete':
            steps.append(f"DELETE FROM {name} WHERE org_id = p_org; GET DIAGNOSTICS n = ROW_COUNT; "
                         f"removed := removed || jsonb_build_object('{name}', n); total := total + n;")
        elif action == 'tombstone':
            steps.append(f"UPDATE {name} SET fields = '{{}}'::jsonb, narrative = '', deleted = true, "
                         f"account_id = NULL, merged_into = NULL, owner_id = '' WHERE org_id = p_org; "
                         f"GET DIAGNOSTICS n = ROW_COUNT; tombstoned := tombstoned || jsonb_build_object('{name}', n);")
        else:
            into = 'left_' if action == 'leave' else 'pinned'
            steps.append(f"SELECT count(*) INTO n FROM {name} WHERE org_id = p_org; "
                         f"{into} := {into} || jsonb_build_object('{name}', n);")
    body = '\n  '.join(steps)
    return f"""CREATE OR REPLACE FUNCTION crm_erase_personal_org(p_org text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
  n bigint; total bigint := 0;
  removed jsonb := '{{}}'; tombstoned jsonb := '{{}}'; pinned jsonb := '{{}}'; left_ jsonb := '{{}}';
BEGIN
  IF p_org IS NULL OR p_org !~ '{_PERSONAL_ORG_SQL}' THEN
    RAISE EXCEPTION 'only an individual''s own CRM (p-<id>) can be erased';
  END IF;
  {body}
  RETURN jsonb_build_object('rows', total, 'removed', removed, 'tombstoned', tombstoned,
                            'pinned', pinned, 'left', left_);
END $fn$"""


def bootstrap_function_sql():
    """``crm_bootstrap_personal_org(org, name)``: ``bootstrap.bootstrap_org`` for an org of one,
    with the personal policy and this release's definitions (``orgs.PERSONAL_POLICY``,
    ``bootstrap.DEFINITIONS``) built in; it creates what is missing and changes nothing else."""
    from .bootstrap import CARD_LIFECYCLE_PROPERTIES, DEFINITIONS
    from .orgs import PERSONAL_POLICY
    return f"""CREATE OR REPLACE FUNCTION crm_bootstrap_personal_org(p_org text, p_name text) RETURNS text[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
  created text[] := ARRAY[]::text[]; d record;
BEGIN
  IF p_org IS NULL OR p_org !~ '{_PERSONAL_ORG_SQL}' THEN
    RAISE EXCEPTION 'only an individual''s own CRM (p-<id>) is made here';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('crm-bootstrap-' || p_org, 0));
  IF NOT EXISTS (SELECT 1 FROM crm_orgs WHERE org_id = p_org) THEN
    INSERT INTO crm_orgs (org_id, name, default_policy, created_at)
      VALUES (p_org, COALESCE(NULLIF(p_name, ''), p_org), {_literal(PERSONAL_POLICY)},
              to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'));
    created := created || 'org'::text;
  END IF;
  -- D11 (schema 8): an existing org's Card type gains the lifecycle properties it lacks; an
  -- administrator's own property definitions win (the right side of `||`).
  UPDATE crm_object_definitions
     SET definition = jsonb_set(definition, '{{properties}}',
                                {_literal(CARD_LIFECYCLE_PROPERTIES)} || COALESCE(definition->'properties', '{{}}'::jsonb))
   WHERE org_id = p_org AND name = 'Card';
  FOR d IN SELECT key, value FROM jsonb_each({_literal(DEFINITIONS)}) LOOP
    IF NOT EXISTS (SELECT 1 FROM crm_object_definitions WHERE org_id = p_org AND name = d.key) THEN
      INSERT INTO crm_object_definitions (org_id, name, definition) VALUES (p_org, d.key, d.value);
      created := created || d.key;
    END IF;
  END LOOP;
  RETURN created;
END $fn$"""


def _functions(c):
    for sql in (bootstrap_function_sql(), erase_function_sql()):
        if '%' in sql or '$fn$' in sql.split('$fn$', 1)[1].rsplit('$fn$', 1)[0]:
            raise ValueError('a function body the driver would misread')
        c.exec_driver_sql(sql)


class SchemaNotReady(RuntimeError):
    """crm-api must not serve: no schema yet, an unknown version, or a superuser login."""


def check_role(role):
    if not _ROLE.fullmatch(role or '') or role in _RESERVED or role.startswith('pg_'):
        raise ValueError('not a role name the CRM may use')
    return role


def tables_and_version(c):
    """Create what is missing and record the version this code knows. An existing database keeps
    its tables and rows; a table added in a later version (the desk's in 3, card members in 4, workspace
    settings in 5) is created beside them, and 4 lets a proposal name no record yet (a proposed card).
    5 adds only a new table, so an install at 4 upgrades by this same `create_all` and the re-grant in
    `init`; no column of an existing table changes. 6 adds the field classification table (configuration:
    SELECT only for crm-api's role, seeded by ``bootstrap``) and three audit columns, added in place
    with an empty default so the existing append-only rows keep their values. 7 adds only a new table
    (``crm_profiles``: a claimed Person record's person-owned fields, D35). 8 (D13) adds the decision
    nonce table and four decision-record columns on ``crm_revisions``, also in place with an empty
    default; the Card definition's new lifecycle properties reach an existing org through
    ``bootstrap.upgrade_card_definition``."""
    metadata.create_all(c)
    if c.dialect.name == 'postgresql':
        # 4 (cards): a proposal may name no record yet — it proposes a new card. Idempotent.
        c.execute(text('ALTER TABLE crm_change_proposals ALTER COLUMN record_id DROP NOT NULL'))
        # 6 (D4/H9): the audit names the principal, the person acted for and the workspace. Idempotent.
        for column in AUDIT_COLUMNS_6:
            c.execute(text(f"ALTER TABLE crm_audit ADD COLUMN IF NOT EXISTS {column} varchar NOT NULL DEFAULT ''"))
        # 8 (D13): a revision may be a decision record. Idempotent; ALTER is DDL, not an UPDATE, so
        # the append-only trigger is not involved.
        for column in REVISION_COLUMNS_7:
            c.execute(text(f"ALTER TABLE crm_revisions ADD COLUMN IF NOT EXISTS {column} varchar NOT NULL DEFAULT ''"))
    for table in metadata.sorted_tables:
        for index in table.indexes:
            index.create(c, checkfirst=True)
    if c.execute(text('SELECT version FROM crm_schema')).first() is None:
        c.execute(schema_version.insert().values(version=SCHEMA_VERSION))
    else:
        c.execute(text('UPDATE crm_schema SET version = :v'), {'v': SCHEMA_VERSION})


def migrate(engine):
    """The tables and triggers alone (the tests' and the importer's path)."""
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Migration requires PostgreSQL')
    with engine.begin() as c:
        tables_and_version(c)
        c.execute(text('''CREATE OR REPLACE FUNCTION crm_history_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION 'CRM revisions are append-only';
            END $$'''))
        for table in APPEND_ONLY:
            c.execute(text(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}'))
            c.execute(text(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
                ON {table} FOR EACH STATEMENT EXECUTE FUNCTION crm_history_immutable()'''))
        _functions(c)       # the personal CRM's two functions (step 5); `init` grants EXECUTE


def init(engine, *, role, password, database):
    """With the admin credential. Idempotent: re-asserts the role's limits and password, creates
    what is missing, re-grants."""
    role = check_role(role)
    if not _PASSWORD.fullmatch(password or ''):
        raise ValueError("the CRM's database password must be 24-256 URL-safe characters")
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,62}', database or ''):
        raise ValueError('not a database name')
    migrate(engine)
    q = f'"{role}"'
    with engine.begin() as c:
        if c.execute(text('SELECT 1 FROM pg_roles WHERE rolname = :r'), {'r': role}).first() is None:
            c.execute(text(f'CREATE ROLE {q}'))
        c.execute(text(f"ALTER ROLE {q} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                       f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}'"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {q}'))
        c.execute(text(f'GRANT USAGE ON SCHEMA public TO {q}'))
        tables = [t.name for t in metadata.sorted_tables]
        c.execute(text(f'GRANT SELECT ON TABLE {", ".join(tables)} TO {q}'))
        for table, rights in WRITES.items():
            c.execute(text(f'GRANT {rights} ON TABLE {table} TO {q}'))
        for fn in (BOOTSTRAP_FN, ERASE_FN):
            c.execute(text(f'REVOKE ALL ON FUNCTION {fn} FROM PUBLIC'))
            c.execute(text(f'GRANT EXECUTE ON FUNCTION {fn} TO {q}'))


def check(engine):
    """crm-api's boot check, as its own role."""
    try:
        with engine.connect() as c:
            row = c.execute(text('SELECT rolsuper FROM pg_roles WHERE rolname = current_user')).first()
            if row is None or bool(row[0]):
                raise SchemaNotReady('crm-api refuses a superuser login; give it its own role '
                                     '(CRM_DB_USER, created by `python -m crm.migrate init`)')
            got = c.execute(text('SELECT version FROM crm_schema')).first()
    except SchemaNotReady:
        raise
    except Exception as exc:  # noqa: BLE001 — no table yet: the migrations path has not run
        raise SchemaNotReady(f"the CRM's schema is not there yet ({type(exc).__name__})") from None
    if got is None or int(got[0]) != SCHEMA_VERSION:
        raise SchemaNotReady(f"the CRM's schema version is {got[0] if got else 'unset'}, this crm-api knows {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _admin_url():
    return 'postgresql+pg8000://{}:{}@{}:{}/{}'.format(
        quote(os.environ.get('DB_USER', 'postgres'), safe=''), quote(os.environ.get('DB_PASSWORD', ''), safe=''),
        os.environ.get('DB_HOST', 'localhost'), os.environ.get('DB_PORT', '5432') or '5432', os.environ.get('DB_NAME', 'vexa'))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ''
    if cmd == 'init':
        role = os.environ.get('CRM_DB_USER', DEFAULT_ROLE)
        password = os.environ.get('CRM_DB_PASSWORD', '')
        org = os.environ.get('CRM_ORG_ID', '').strip()
        if not password or not org:
            print('[crm-schema] CRM_DB_PASSWORD or CRM_ORG_ID is unset; the CRM stays off', flush=True)
            return 0
        engine = create_engine(_admin_url(), pool_pre_ping=True, pool_size=1, max_overflow=0)
        for attempt in range(30):
            try:
                with engine.connect():
                    break
            except Exception as exc:  # noqa: BLE001
                if attempt == 29:
                    print(f'[crm-schema] STOP: the database did not answer ({type(exc).__name__})', flush=True)
                    return 1
                time.sleep(2)
        try:
            init(engine, role=role, password=password, database=os.environ.get('DB_NAME', 'vexa'))
        except ValueError as exc:
            print(f'[crm-schema] STOP: {exc}', flush=True)
            return 1
        from .bootstrap import bootstrap_org, upgrade_card_definition
        created = bootstrap_org(engine, org, os.environ.get('CRM_ORG_NAME', '') or org)
        if upgrade_card_definition(engine, org):
            created.append('Card lifecycle properties')
        print(f'[crm-schema] role {role} and the crm_* tables are ready (schema version {SCHEMA_VERSION}; '
              f'grants on those tables only); created: {", ".join(created) or "nothing"}', flush=True)
        return 0
    if cmd == 'check':
        engine = create_engine(os.environ['CRM_DATABASE_URL'], pool_pre_ping=True, pool_size=1, max_overflow=0)
        deadline = time.time() + float(os.environ.get('CRM_WAIT_SEC', '600') or 600)
        while True:
            try:
                check(engine)
                print('[crm-schema] ready', flush=True)
                return 0
            except SchemaNotReady as exc:
                if time.time() > deadline:
                    print(f'[crm-schema] not ready: {exc}', flush=True)
                    return 1
                time.sleep(3)
    if cmd == '':
        # The prototype's form: tables and triggers with whatever credential CRM_DATABASE_URL holds.
        migrate(create_engine(os.environ['CRM_DATABASE_URL']))
        return 0
    print('usage: python -m crm.migrate init|check', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
