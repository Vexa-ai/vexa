"""Optional CRM database. Metadata is never installed by another Vexa domain."""
from sqlalchemy import JSON, Boolean, Column, ForeignKeyConstraint, Integer, Index, MetaData, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
Document = JSON().with_variant(JSONB(), 'postgresql')
def org():
    return Column('org_id', String, primary_key=True)
def record_fk():
    return ForeignKeyConstraint(['org_id', 'record_id'], ['crm_records.org_id', 'crm_records.id'])

object_types = Table('crm_object_definitions', metadata, org(),
    Column('name', String, primary_key=True), Column('definition', Document, nullable=False))
records = Table('crm_records', metadata, org(), Column('id', String, primary_key=True),
    Column('object_type', String, nullable=False), Column('owner_id', String, nullable=False),
    Column('revision', Integer, nullable=False), Column('fields', Document, nullable=False),
    Column('narrative', Text, nullable=False), Column('quarantined', Boolean, nullable=False),
    Column('deleted', Boolean, nullable=False),
    # ADR-0047 d3: a user is a person entity with an account attached; a merged record names the
    # record that survives it, and every read of the old id answers the survivor.
    Column('account_id', String, nullable=True), Column('merged_into', String, nullable=True),
    UniqueConstraint('org_id', 'account_id', name='crm_records_one_person_per_account'),
    ForeignKeyConstraint(['org_id', 'object_type'], ['crm_object_definitions.org_id', 'crm_object_definitions.name']))
# Policies are provisioned by import/admin, never by the record writer.
# One policy per org+subject includes membership, per-object fields, and record grants.
policies = Table('crm_access_policies', metadata, org(), Column('subject_id', String, primary_key=True),
    Column('policy', Document, nullable=False))
source_mappings = Table('crm_source_mappings', metadata, org(),
    Column('system', String, primary_key=True), Column('source_org', String, primary_key=True),
    Column('object_type', String, primary_key=True), Column('source_id', String, primary_key=True),
    Column('record_id', String, nullable=False), record_fk())
relationships = Table('crm_relationships', metadata, org(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('kind', String, nullable=False),
    Column('target_domain', String, nullable=False), Column('target_id', String, nullable=False), record_fk())
revisions = Table('crm_revisions', metadata, org(), Column('record_id', String, primary_key=True),
    Column('revision', Integer, primary_key=True), Column('actor_id', String, nullable=False),
    Column('created_at', String, nullable=False), Column('reason', Text, nullable=False),
    Column('before', Document), Column('after', Document, nullable=False),
    Column('evidence', Document, nullable=False),
    # 7 (PRD 0.13.4 D13): a revision that is a DECISION RECORD — a person's gate decision on a card
    # (acknowledge, decline, deliver, confirm, decide, execute, answer, use). ``decision`` names the
    # action, ``credential_class`` the credential it was made with (always ``session``: the
    # terminal's sign-in, never an API key or a delegation), ``nonce`` the server nonce bound to the
    # pending item, ``content_hash`` the sha256 of the canonical card face the signer saw. Empty on
    # every other revision. Append-only like the rest of the row (the trigger guards it).
    Column('decision', String, nullable=False, server_default=''),
    Column('credential_class', String, nullable=False, server_default=''),
    Column('nonce', String, nullable=False, server_default=''),
    Column('content_hash', String, nullable=False, server_default=''),
    record_fk())
# A proposal changes a record, or (record_id NULL, desktop D2) proposes a new card: `patch.create`.
change_proposals = Table('crm_change_proposals', metadata, org(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=True), Column('base_revision', Integer, nullable=False),
    Column('author_id', String, nullable=False), Column('patch', Document, nullable=False),
    Column('evidence', Document, nullable=False), Column('reason', Text, nullable=False),
    Column('status', String, nullable=False), record_fk())
import_runs = Table('crm_import_receipts', metadata, org(), Column('id', String, primary_key=True),
    Column('source_org', String, nullable=False), Column('manifest', Document, nullable=False),
    Column('quarantine', Document, nullable=False), Column('status', String, nullable=False))
outbox = Table('crm_outbox', metadata, org(), Column('id', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('event', Document, nullable=False), Column('delivered', Boolean, nullable=False),
    UniqueConstraint('org_id', 'record_id', 'revision'),
    ForeignKeyConstraint(['org_id', 'record_id', 'revision'],
                         ['crm_revisions.org_id', 'crm_revisions.record_id', 'crm_revisions.revision']))

Index("crm_records_by_type", records.c.org_id, records.c.object_type, records.c.id)
Index("crm_records_by_owner", records.c.org_id, records.c.owner_id, records.c.object_type)

Index("crm_sources_by_record", source_mappings.c.org_id, source_mappings.c.record_id)
Index("crm_links_by_record", relationships.c.org_id, relationships.c.record_id)

# Layout revisions are independent of record data and imported object definitions.
card_layouts = Table('crm_card_layouts', metadata, org(), Column('object_type', String, primary_key=True),
    Column('version', Integer, primary_key=True), Column('layout', Document, nullable=False),
    Column('actor_id', String, nullable=False), Column('reason', Text, nullable=False),
    Column('created_at', String, nullable=False),
    ForeignKeyConstraint(['org_id', 'object_type'], ['crm_object_definitions.org_id', 'crm_object_definitions.name']))

# ── ADR-0047 ──────────────────────────────────────────────────────────────────────────────────
# The organisation owns the registry, the people directory and the policies. One per install for
# now; every row still carries org_id so a cloud can hold many.
orgs = Table('crm_orgs', metadata, org(), Column('name', String, nullable=False),
    Column('default_policy', Document, nullable=False), Column('created_at', String, nullable=False))
# Match identifiers (email:…, domain:…, calendar:…): one record per identifier per organisation,
# so a second person with the same address is a merge candidate, never a silent duplicate.
# ``proof`` (9) says whether an ``email:`` identifier binds a sign-in, and who may have set it:
# ``signin:<account>`` — identity proved the address for that account at its sign-in (the identity
# path writes it, never a CRM writer); ``record`` — given with the record when it was created
# (``crm_change`` create's ``match``), never moved, so the first sign-in identity proves it for may
# take the record while no account holds it; NULL — carried by a CRM merge, or not an address: it
# finds duplicates and binds nobody (``persons.Persons.binds``).
match_ids = Table('crm_match_ids', metadata, org(), Column('identifier', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('proof', String, nullable=True), record_fk())
PROOF_SIGNIN = 'signin:'
PROOF_RECORD = 'record'
# Decision 5: page P in workspace W refers to record R, as of W's commit. Replaced per workspace
# from a full snapshot, so it is rebuildable from git. No foreign key: a page may name an id the
# CRM does not hold (yet), and the reference is still a fact about the page.
page_references = Table('crm_page_references', metadata, org(), Column('workspace', String, primary_key=True),
    Column('page', String, primary_key=True), Column('record_id', String, primary_key=True))
workspace_heads = Table('crm_workspace_heads', metadata, org(), Column('workspace', String, primary_key=True),
    Column('commit', String, nullable=False), Column('updated_at', String, nullable=False))
# The workspace's own roster (policy/members.json), carried on the same fact. Read for the ROLE a
# derived write grant needs; identity's live membership answer is still required for any grant.
workspace_rosters = Table('crm_workspace_rosters', metadata, org(), Column('workspace', String, primary_key=True),
    Column('subject', String, primary_key=True), Column('role', String, nullable=False))
# Decision 6: an admin binds a workspace to a record; its members get derived grants.
workspace_bindings = Table('crm_workspace_bindings', metadata, org(), Column('workspace', String, primary_key=True),
    Column('record_id', String, primary_key=True), Column('created_by', String, nullable=False),
    Column('created_at', String, nullable=False), record_fk())
# Decision 7: every read and write, with the caller's kind and the session's trace.
audit = Table('crm_audit', metadata, org(), Column('id', String, primary_key=True),
    Column('at', String, nullable=False), Column('subject', String, nullable=False),
    Column('caller', String, nullable=False), Column('session', String, nullable=False),
    Column('trace', String, nullable=False), Column('op', String, nullable=False),
    Column('record_id', String, nullable=False), Column('outcome', String, nullable=False),
    # 6 (D4/H9): who the read was for and in which workspace. ``principal`` is ``ws:<slug>`` for a
    # shared (room) caller, else the subject; ``on_behalf_of`` the person a worker acts for (the
    # speaker once identity carries it); ``workspace`` the caller's ceiling, comma-joined. A search
    # names every returned record id in ``record_id``, comma-joined.
    Column('principal', String, nullable=False, server_default=''),
    Column('on_behalf_of', String, nullable=False, server_default=''),
    Column('workspace', String, nullable=False, server_default=''))
schema_version = Table('crm_schema', metadata, Column('version', Integer, primary_key=True))

# ── The desk (desktop phase D1): one person's own state, never shared organisation state ─────
# Placements: which agents and files (cards from D2) are on this person's desk. A mirror of the
# latest layout version's placement set, kept for the read and for "new" (seen_at).
desk = Table('crm_desk', metadata, org(), Column('person', String, primary_key=True),
    Column('ref', String, primary_key=True), Column('placed_at', String, nullable=False),
    Column('seen_at', String, nullable=True))
# Every applied arrangement is a new version (append-only, trigger-guarded like revisions), carrying
# the layout AND the placement set after it, so Undo restores both. Version 0 is the default, no row.
desk_layouts = Table('crm_desk_layouts', metadata, org(), Column('person', String, primary_key=True),
    Column('version', Integer, primary_key=True), Column('name', String, nullable=True),
    Column('layout', Document, nullable=False), Column('placements', Document, nullable=False),
    Column('ops', Document, nullable=False), Column('author', String, nullable=False),
    Column('trace', String, nullable=False), Column('created_at', String, nullable=False))
# What an agent may only suggest: pending until the person applies or dismisses it; 7 days to live.
desk_proposals = Table('crm_desk_proposals', metadata, org(), Column('id', String, primary_key=True),
    Column('person', String, nullable=False), Column('ops', Document, nullable=False),
    Column('reason', Text, nullable=False), Column('author', String, nullable=False),
    Column('trace', String, nullable=False), Column('status', String, nullable=False),
    Column('created_at', String, nullable=False))
# ── Cards (desktop phase D2): sharing is membership on the same record, never a copy ──────────
# `invited` reads the card and waits on the person's desk; `member` reads and writes it. Membership
# grants the card, never what the card is about.
card_members = Table('crm_card_members', metadata, org(), Column('record_id', String, primary_key=True),
    Column('subject', String, primary_key=True), Column('status', String, nullable=False),
    Column('added_by', String, nullable=False), Column('added_at', String, nullable=False), record_fk())
# A person opened this card (its "New" chip clears): their own view, never the card's data.
card_seen = Table('crm_card_seen', metadata, org(), Column('person', String, primary_key=True),
    Column('record_id', String, primary_key=True), Column('seen_at', String, nullable=False), record_fk())
# ── Workspace settings (R3, the CRM `member_of` relation): one person's view of one workspace ──
# Pinned, hidden from home, muted, the notification level, the room order, a level override and the
# collapsed sections. Private to the person, written only by crm-api (member_settings.py), and kept in
# the person's own boundary's CRM. Its own table, not columns on crm_workspace_rosters, because:
# - the roster is a mirror of the workspace's policy/members.json that its snapshot replaces with
#   DELETE + INSERT, which would wipe every person's settings on each commit;
# - the personal and private workspaces have no roster row at all;
# - the roster is the workspace's (every member reads it), while these settings are the person's own.
# No foreign key to the roster for the same reasons; a row for a slug grants nothing.
# `notify` is RETIRED (PRD 0.13.4 S6): kept for existing rows, filled with 'all', never written or read —
# the notification level lives in the person's identity notification policy (one writer).
member_settings = Table('crm_member_settings', metadata, org(), Column('person', String, primary_key=True),
    Column('workspace', String, primary_key=True), Column('pinned', Boolean, nullable=False),
    Column('hidden', Boolean, nullable=False), Column('muted', Boolean, nullable=False),
    Column('notify', String, nullable=False), Column('room_order', Document, nullable=False),
    Column('level', Integer, nullable=True), Column('collapsed', Document, nullable=False),
    Column('updated_at', String, nullable=False))
# ── Field classification (6, PRD 0.13.4 D4; converged model §15) ───────────────────────────────
# One class per object type and field: ``private`` (the record's owner only), ``org`` (the CRM
# policy decides) or ``ws`` (also shareable to the rooms of ``workspaces``). ``$narrative`` and
# ``$match_ids`` are classified like fields. Configuration, like policies and definitions: the
# migrations path seeds the defaults (``field_classes.DEFAULT_CLASSES`` via ``bootstrap``) and an
# administrator changes them; crm-api only reads them. A field with no row takes
# ``field_classes.default_class``, the same rule the seed was written with.
field_classes = Table('crm_field_classes', metadata, org(), Column('object_type', String, primary_key=True),
    Column('field', String, primary_key=True), Column('class', String, nullable=False),
    Column('workspaces', Document, nullable=False), Column('set_by', String, nullable=False),
    Column('set_at', String, nullable=False))
# ── Profiles (7, PRD 0.13.4 D35 founder addition): a profile is a CLAIMED Person record ────────
# One row per Person record that a person claimed (or once claimed). ``claimed_by`` is the account,
# strictly 1:1 (the record is the key; one record per account by the unique constraint). ``fields``
# are the person's own: display name, photo, headline, company, bio, location, time zone, links and
# contact preferences — never the org's fields, which stay on ``crm_records``. ``public_fields`` and
# ``handle`` are the optional public page (``/p/<handle>``), off unless the person switches it on.
# Unclaimed (``claimed_by`` NULL), the same fields are the org's again; ``unclaimed_by`` keeps that
# account from being claimed back automatically on its next sign-in. Not an identity table: the human
# is the record, this is the half of it the human owns.
profiles = Table('crm_profiles', metadata, org(), Column('record_id', String, primary_key=True),
    Column('claimed_by', String, nullable=True), Column('unclaimed_by', String, nullable=True),
    Column('fields', Document, nullable=False), Column('public_fields', Document, nullable=False),
    Column('published', Boolean, nullable=False), Column('handle', String, nullable=True),
    Column('claimed_at', String, nullable=True), Column('updated_at', String, nullable=False),
    UniqueConstraint('org_id', 'claimed_by', name='crm_profiles_one_per_account'),
    UniqueConstraint('handle', name='crm_profiles_handle'), record_fk())
# ── Decision nonces (8, PRD 0.13.4 D13): one server nonce per pending decision ───────────────
# Minted for one person, one card, one revision and the content hash of the face that person saw
# (``card_ops.Cards.nonce``); spent by exactly one decision (``used_at``), expired after
# ``card_ops.NONCE_TTL``. A replay, a nonce minted for someone else, a stale revision or a face that
# changed is refused. Never a grant: the decision still has to be the viewer's to make.
card_nonces = Table('crm_card_nonces', metadata, org(), Column('nonce', String, primary_key=True),
    Column('record_id', String, nullable=False), Column('person', String, nullable=False),
    Column('revision', Integer, nullable=False), Column('content_hash', String, nullable=False),
    Column('created_at', String, nullable=False), Column('expires_at', String, nullable=False),
    Column('used_at', String, nullable=True), record_fk())
SCHEMA_VERSION = 10

Index("crm_refs_by_record", page_references.c.org_id, page_references.c.record_id)
Index("crm_bindings_by_record", workspace_bindings.c.org_id, workspace_bindings.c.record_id)
Index("crm_audit_by_time", audit.c.org_id, audit.c.at)
# 10: the audit expires across every org at once (`migrate.audit_purge_function_sql`).
Index("crm_audit_by_age", audit.c.at)
Index("crm_card_members_by_subject", card_members.c.org_id, card_members.c.subject)
Index("crm_links_by_target", relationships.c.org_id, relationships.c.target_domain, relationships.c.target_id)
Index("crm_desk_proposals_by_person", desk_proposals.c.org_id, desk_proposals.c.person, desk_proposals.c.status)
