"""The CRM's migrations path and its own Postgres role (ADR-0047 § Storage; ADR-0048's pattern).

crm-api never holds the instance's admin credential and runs no DDL. The migrations path — compose
`crm-init`, the Helm `crm-schema` Job — runs this module with that credential and:

1. creates (or re-asserts) a LOGIN role, by default ``vexa_crm``, with no admin power and the
   password it is given. Before anything else it checks that the credential may do that
   (``plan_role``) and otherwise stops, naming the grant the database team has to run: CREATEROLE
   to create the role; on Postgres 16+ also the ADMIN option on a role it did not create; or a
   role pre-created with the password it is given, which then needs no role privilege at all;
2. creates the ``crm_*`` tables and the append-only triggers on revisions, card layouts, the audit
   and desk layout versions, and
   records the schema version. The audit is append-only for as long as it is kept: a row older
   than ``CRM_AUDIT_RETENTION_DAYS`` is removed by ``crm_purge_audit()`` and by nothing else
   (``audit_purge_function_sql``);
3. grants that role exactly what the record writer needs — SELECT on every CRM table, and the
   writes below — and nothing on any other table, so it cannot read accounts, tokens or meetings;
4. creates the organisation and its stable entity types if they are missing (`bootstrap`);
5. creates the two functions crm-api calls for an individual's personal CRM (``personal_crm``), and
   lets that role EXECUTE them and nothing more: ``crm_bootstrap_personal_org`` makes one on first
   use, ``crm_erase_personal_org`` erases one on account delete (``erase_plan``). Both are SECURITY
   DEFINER, accept only an org of one (``p-<id>``), and are re-created on every ``init`` from this
   code, so the definitions and the erase plan they carry follow the release.

crm-api then logs in as that role and only CHECKS (``check``): not a superuser, and the schema
version this code knows. It may EXECUTE ``crm_purge_audit()``, which takes no argument: the period
is the one this path wrote into it, so the runtime role can expire the audit on schedule and can
never shorten what is kept (``crm.retention``).

    python -m crm.migrate init    # admin DB_* + CRM_DB_USER / CRM_DB_PASSWORD + CRM_ORG_ID
                                  # (+ CRM_AUDIT_RETENTION_DAYS, default 365, 0 keeps the audit)
    python -m crm.migrate check   # the role's own login (CRM_DATABASE_URL); exits 0 once ready
"""
import json
import os
import re
import sys
import time
from urllib.parse import quote

from sqlalchemy import String, cast, create_engine, select, text, update

from . import schema as s
from .schema import SCHEMA_VERSION, metadata, schema_version

DEFAULT_ROLE = 'vexa_crm'
DOCS = 'https://docs.vexa.ai/deployment-kubernetes#database-privileges-on-an-external-postgres'
#: The role attributes the CRM's role must not hold, as ``pg_roles`` columns → the keyword.
_POWERS = (('rolsuper', 'SUPERUSER'), ('rolcreatedb', 'CREATEDB'), ('rolcreaterole', 'CREATEROLE'),
           ('rolreplication', 'REPLICATION'), ('rolbypassrls', 'BYPASSRLS'))
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
PURGE_AUDIT_FN = 'crm_purge_audit()'

#: How long the audit is kept (``CRM_AUDIT_RETENTION_DAYS``): a year by default, because who read
#: and changed a record is what an incident review or a compliance request asks for months later.
#: 0 keeps it for ever. A period shorter than ``AUDIT_RETENTION_MIN_DAYS`` is refused, so a typo
#: cannot empty the audit.
AUDIT_RETENTION_DEFAULT_DAYS = 365
AUDIT_RETENTION_MIN_DAYS = 30
#: The transaction-local setting ``crm_purge_audit()`` turns on for its own DELETE; the audit's row
#: trigger refuses a DELETE made without it.
AUDIT_EXPIRE_SETTING = 'crm.audit_expiring'
#: ``at`` is written as ``datetime.isoformat()`` in UTC; the cutoff is spelled the same way.
_ISO_UTC = 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
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


def audit_retention_days(env=None):
    """``CRM_AUDIT_RETENTION_DAYS`` as days: unset is the default, 0 keeps the audit for ever."""
    raw = ((os.environ if env is None else env).get('CRM_AUDIT_RETENTION_DAYS') or '').strip()
    if not raw:
        return AUDIT_RETENTION_DEFAULT_DAYS
    try:
        days = int(raw)
    except ValueError:
        raise ValueError('CRM_AUDIT_RETENTION_DAYS must be a whole number of days (0 keeps the audit)') from None
    if days != 0 and not AUDIT_RETENTION_MIN_DAYS <= days <= 36500:
        raise ValueError(f'CRM_AUDIT_RETENTION_DAYS must be 0 (keep) or {AUDIT_RETENTION_MIN_DAYS}..36500 days')
    return days


def _audit_cutoff_sql(days):
    """The moment before which an audit row has expired, spelled like ``at``."""
    return f"to_char((now() - interval '{int(days)} days') AT TIME ZONE 'UTC', '{_ISO_UTC}')"


def audit_purge_function_sql(days):
    """``crm_purge_audit()``: removes the audit rows older than ``days`` and returns how many.

    The period is written into the body here, by the migrations path, so the role that may call
    it cannot choose it. The audit's row trigger (``audit_expire_trigger_sql``) refuses every
    DELETE unless ``AUDIT_EXPIRE_SETTING`` is on — which only this function sets, for its own
    transaction — and, even then, of any row inside the same period."""
    days = int(days)
    return f"""CREATE OR REPLACE FUNCTION crm_purge_audit() RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
  n bigint := 0;
BEGIN
  IF {days} <= 0 THEN
    RETURN 0;
  END IF;
  PERFORM set_config('{AUDIT_EXPIRE_SETTING}', 'on', true);
  DELETE FROM crm_audit WHERE at < {_audit_cutoff_sql(days)};
  GET DIAGNOSTICS n = ROW_COUNT;
  PERFORM set_config('{AUDIT_EXPIRE_SETTING}', '', true);
  RETURN n;
END $fn$"""


def audit_expire_trigger_sql(days):
    """The audit's DELETE guard, row by row: a row goes only inside ``crm_purge_audit()`` and only
    when it is older than the period, which is written here too — so no caller, the owner holding
    the purge's own setting included, removes a row the period still keeps without changing this
    function first. With the period 0 every DELETE is refused."""
    days = int(days)
    return f"""CREATE OR REPLACE FUNCTION crm_audit_expire_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF {days} <= 0 OR COALESCE(current_setting('{AUDIT_EXPIRE_SETTING}', true), '') <> 'on'
     OR OLD.at >= {_audit_cutoff_sql(days)} THEN
    RAISE EXCEPTION 'CRM revisions are append-only';
  END IF;
  RETURN OLD;
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


def _functions(c, audit_days=AUDIT_RETENTION_DEFAULT_DAYS):
    for sql in (bootstrap_function_sql(), erase_function_sql(), audit_purge_function_sql(audit_days)):
        if '%' in sql or '$fn$' in sql.split('$fn$', 1)[1].rsplit('$fn$', 1)[0]:
            raise ValueError('a function body the driver would misread')
        c.exec_driver_sql(sql)


class SchemaNotReady(RuntimeError):
    """crm-api must not serve: no schema yet, an unknown version, or a superuser login."""


class PrivilegeMissing(ValueError):
    """The migration credential may not make or re-assert the CRM's role. The message names the
    statement the database team runs; nothing has been written when it is raised."""


def plan_role(c, role, logs_in=None):
    """What the migration credential does to the CRM's role, decided before anything is written:
    ``'create'``; ``'reassert'`` (a superuser sets every limit and the password again); ``'alter'``
    (LOGIN, NOINHERIT and the password only); or ``'keep'`` (a role the database team pre-created,
    whose password already logs in). Raises ``PrivilegeMissing`` naming the grant otherwise.

    A non-superuser may not name SUPERUSER, REPLICATION or BYPASSRLS in ALTER ROLE at all, even to
    switch them off (Postgres 16+ adds CREATEDB), and from Postgres 16 CREATEROLE reaches only roles
    the credential holds the ADMIN option on, which it gets on the roles it creates."""
    me = c.execute(text("SELECT current_user, rolsuper, rolcreaterole, current_setting('server_version_num')::int "
                        'FROM pg_roles WHERE rolname = current_user')).first()
    if me is None:
        raise PrivilegeMissing("cannot read the migration credential's own role in pg_roles")
    user, superuser, createrole, version = me[0], bool(me[1]), bool(me[2]), int(me[3])
    target = c.execute(text('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, '
                            'rolinherit, rolcanlogin FROM pg_roles WHERE rolname = :r'), {'r': role}).first()
    if superuser:
        return 'create' if target is None else 'reassert'
    q, u = f'"{role}"', f'"{user}"'
    if target is None:
        if createrole:
            return 'create'
        raise PrivilegeMissing(
            f"database user {u} may not create the CRM's role {q}: it lacks CREATEROLE. Have the "
            f'database team run `ALTER ROLE {u} CREATEROLE;`, or pre-create the role with the password '
            f"in the CRM's Secret: `CREATE ROLE {q} LOGIN NOINHERIT PASSWORD '<that password>';`. See {DOCS}")
    powers = [kw for (_, kw), on in zip(_POWERS, target) if on]
    if powers:
        raise PrivilegeMissing(
            f"role {q} holds {', '.join(powers)}; the CRM refuses a role with admin power and database "
            f"user {u} may not remove it. Have the database team run "
            f"`ALTER ROLE {q} {' '.join('NO' + p for p in powers)};`. See {DOCS}")
    admin = version < 160000 or bool(c.execute(
        text("SELECT pg_has_role(current_user, :r, 'MEMBER WITH ADMIN OPTION')"), {'r': role}).first()[0])
    if createrole and admin:
        return 'alter'
    if target[6] and not target[5] and logs_in is not None and logs_in():
        return 'keep'
    need = ' and '.join(([f'`ALTER ROLE {u} CREATEROLE;`'] if not createrole else [])
                        + ([f'`GRANT {q} TO {u} WITH ADMIN OPTION;`'] if not admin else []))
    why = 'Postgres 16+ needs CREATEROLE and the ADMIN option on it' if version >= 160000 else 'it lacks CREATEROLE'
    raise PrivilegeMissing(
        f"database user {u} may not change role {q} ({why}), and the CRM's password does not log in "
        f'as a LOGIN NOINHERIT {q}. Have the database team run {need}, or set the role to the '
        f"Secret's password: `ALTER ROLE {q} LOGIN NOINHERIT PASSWORD '<that password>';`. See {DOCS}")


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
    ``bootstrap.upgrade_card_definition``. 9 adds ``crm_match_ids.proof`` in place and fills it once,
    on the upgrade from an earlier version (``match_proof_9``). 10 adds only an index on the audit's
    time (the expiry across every org); the audit's purge function and its row trigger are re-created
    by ``migrate`` on every run, whatever the version."""
    metadata.create_all(c)
    before = c.execute(text('SELECT version FROM crm_schema')).scalar()
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
        # 9: whether an email identifier binds a sign-in, and who set it (schema.match_ids)
        c.execute(text('ALTER TABLE crm_match_ids ADD COLUMN IF NOT EXISTS proof varchar NULL'))
    if before is not None and int(before) < 9:
        match_proof_9(c)
    for table in metadata.sorted_tables:
        for index in table.indexes:
            index.create(c, checkfirst=True)
    if c.execute(text('SELECT version FROM crm_schema')).first() is None:
        c.execute(schema_version.insert().values(version=SCHEMA_VERSION))
    else:
        c.execute(text('UPDATE crm_schema SET version = :v'), {'v': SCHEMA_VERSION})


def match_proof_9(c):
    """The proof of every ``email:`` identifier an install held before 9, filled once on the upgrade.

    * On a record a CRM merge carried identifiers into (a merge revision with ``merged_into`` and no
      person's ``card``, followed to its survivor): NULL. Which identifiers came with the merge is not
      recorded, so none of that record's addresses binds a sign-in until identity proves one again
      (the account still finds its Person by ``account_id``; its next proven sign-in re-proves it).
    * On a record an account answers to: ``signin:<that account>``, as before 9 every identifier there
      bound it.
    * On any other record: ``record`` (the org's record of someone who has not signed in yet)."""
    r, m, v = s.records, s.match_ids, s.revisions
    merged_into = {(o, i): into for o, i, into in c.execute(select(r.c.org_id, r.c.id, r.c.merged_into))}

    def survivor(org, record_id):
        for _ in range(16):
            into = merged_into.get((org, record_id))
            if not into:
                return record_id
            record_id = into
        return record_id

    carried = set()
    for org, evidence in c.execute(select(v.c.org_id, v.c.evidence).where(
            cast(v.c.evidence, String).like('%merged_into%'))):
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except ValueError:
                continue
        for e in evidence if isinstance(evidence, list) else []:
            if isinstance(e, dict) and e.get('merged_into') and not e.get('card'):
                carried.add((org, survivor(org, str(e['merged_into']))))
    accounts = {(o, i): a for o, i, a in c.execute(select(r.c.org_id, r.c.id, r.c.account_id))}
    links = {}
    for o, identifier, record_id in c.execute(select(m.c.org_id, m.c.identifier, m.c.record_id).where(
            m.c.identifier.like('account:%'))):
        links.setdefault((o, record_id), identifier[len('account:'):])
    for o, identifier, record_id in c.execute(select(m.c.org_id, m.c.identifier, m.c.record_id).where(
            m.c.identifier.like('email:%'), m.c.proof.is_(None))).all():
        key = (o, record_id)
        if key in carried:
            continue
        account = accounts.get(key) or links.get(key)
        proof = s.PROOF_SIGNIN + str(account) if account else s.PROOF_RECORD
        c.execute(update(m).where(m.c.org_id == o, m.c.identifier == identifier).values(proof=proof))


def migrate(engine, *, audit_days=AUDIT_RETENTION_DEFAULT_DAYS):
    """The tables and triggers alone (the tests' and the importer's path)."""
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Migration requires PostgreSQL')
    with engine.begin() as c:
        tables_and_version(c)
        c.execute(text('''CREATE OR REPLACE FUNCTION crm_history_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION 'CRM revisions are append-only';
            END $$'''))
        c.exec_driver_sql(audit_expire_trigger_sql(audit_days))
        for table in APPEND_ONLY:
            c.execute(text(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}'))
            c.execute(text(f'DROP TRIGGER IF EXISTS {table}_expire_only ON {table}'))
            # the audit expires (``crm_purge_audit``): its DELETE is guarded row by row instead
            refused = 'UPDATE OR TRUNCATE' if table == 'crm_audit' else 'UPDATE OR DELETE OR TRUNCATE'
            c.execute(text(f'''CREATE TRIGGER {table}_immutable BEFORE {refused}
                ON {table} FOR EACH STATEMENT EXECUTE FUNCTION crm_history_immutable()'''))
        c.execute(text('''CREATE TRIGGER crm_audit_expire_only BEFORE DELETE ON crm_audit
            FOR EACH ROW EXECUTE FUNCTION crm_audit_expire_only()'''))
        # the personal CRM's two functions (step 5) and the audit's purge; `init` grants EXECUTE
        _functions(c, audit_days)


def init(engine, *, role, password, database, audit_days=AUDIT_RETENTION_DEFAULT_DAYS, logs_in=None):
    """With the admin credential. Idempotent: re-asserts the role's limits and password, creates
    what is missing, re-grants. Checks first that the credential may make or re-assert the role
    (``plan_role``) and writes nothing when it may not."""
    role = check_role(role)
    audit_days = audit_retention_days({'CRM_AUDIT_RETENTION_DAYS': str(audit_days)})
    if not _PASSWORD.fullmatch(password or ''):
        raise ValueError("the CRM's database password must be 24-256 URL-safe characters")
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,62}', database or ''):
        raise ValueError('not a database name')
    with engine.connect() as c:
        plan = plan_role(c, role, logs_in)
    migrate(engine, audit_days=audit_days)
    q = f'"{role}"'
    limits = 'LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
    with engine.begin() as c:
        if plan == 'create':
            c.execute(text(f"CREATE ROLE {q} WITH {limits} PASSWORD '{password}'"))
        elif plan == 'reassert':
            c.execute(text(f"ALTER ROLE {q} WITH {limits} PASSWORD '{password}'"))
        elif plan == 'alter':
            c.execute(text(f"ALTER ROLE {q} WITH LOGIN NOINHERIT PASSWORD '{password}'"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {q}'))
        c.execute(text(f'GRANT USAGE ON SCHEMA public TO {q}'))
        tables = [t.name for t in metadata.sorted_tables]
        c.execute(text(f'GRANT SELECT ON TABLE {", ".join(tables)} TO {q}'))
        for table, rights in WRITES.items():
            c.execute(text(f'GRANT {rights} ON TABLE {table} TO {q}'))
        for fn in (BOOTSTRAP_FN, ERASE_FN, PURGE_AUDIT_FN):
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


def _logs_in(role, password):
    url = 'postgresql+pg8000://{}:{}@{}:{}/{}'.format(
        quote(role, safe=''), quote(password, safe=''), os.environ.get('DB_HOST', 'localhost'),
        os.environ.get('DB_PORT', '5432') or '5432', os.environ.get('DB_NAME', 'vexa'))
    engine = create_engine(url)
    try:
        with engine.connect():
            return True
    except Exception:  # noqa: BLE001 — a wrong password, no LOGIN, no such role
        return False
    finally:
        engine.dispose()


def _pg_message(exc):
    """pg8000 carries the server's fields as a dict (``M`` the message, ``C`` the SQLSTATE)."""
    orig = getattr(exc, 'orig', exc)
    arg = orig.args[0] if orig.args else None
    if isinstance(arg, dict) and arg.get('M'):
        return f"{arg['M']} ({arg.get('C', '')})"
    return type(orig).__name__


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
            audit_days = audit_retention_days()
            init(engine, role=role, password=password, database=os.environ.get('DB_NAME', 'vexa'),
                 audit_days=audit_days, logs_in=lambda: _logs_in(role, password))
        except ValueError as exc:
            print(f'[crm-schema] STOP: {exc}', flush=True)
            return 1
        except Exception as exc:  # noqa: BLE001 — a refusal from Postgres, in its own words
            print(f'[crm-schema] STOP: Postgres refused: {_pg_message(exc)}. See {DOCS}', flush=True)
            return 1
        from .bootstrap import bootstrap_org, upgrade_card_definition
        created = bootstrap_org(engine, org, os.environ.get('CRM_ORG_NAME', '') or org)
        if upgrade_card_definition(engine, org):
            created.append('Card lifecycle properties')
        print(f'[crm-schema] role {role} and the crm_* tables are ready (schema version {SCHEMA_VERSION}; '
              f'grants on those tables only); audit kept {audit_days or "for ever"}'
              f'{" days" if audit_days else ""}; created: {", ".join(created) or "nothing"}', flush=True)
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
        migrate(create_engine(os.environ['CRM_DATABASE_URL']), audit_days=audit_retention_days())
        return 0
    print('usage: python -m crm.migrate init|check', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
