"""The organisation and its stable entity types (ADR-0047 decisions 2 and 3).

Run by the migrations path with the admin credential, never by crm-api: object definitions and the
organisation's default policy are configuration, not records, and the runtime role cannot write them.
An individual's personal CRM is the one exception, made at runtime through a function the
migrations path owns (`ensure_personal_org`).
Idempotent — an existing organisation, type or policy is left exactly as an administrator left it.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from . import schema as s

ENTITY_TYPES = ('Person', 'Company', 'Deal', 'Project')


def _ref(target, description):
    return {'type': 'string', 'referenceTo': [target], 'description': description}


#: The four stable entities. Imported organisations bring their own definitions besides these.
DEFINITIONS = {
    'Person': {'type': 'object', 'additionalProperties': False, 'required': ['Name'], 'properties': {
        'Name': {'type': 'string', 'minLength': 1}, 'Email': {'type': 'string'}, 'Title': {'type': 'string'},
        'Phone': {'type': 'string'}, 'Company': _ref('Company', 'The company this person works for'),
        'LastContact': {'type': 'string', 'format': 'date', 'description': 'Date of the last human contact'}}},
    'Company': {'type': 'object', 'additionalProperties': False, 'required': ['Name'], 'properties': {
        'Name': {'type': 'string', 'minLength': 1}, 'Domain': {'type': 'string'},
        'Stage': {'type': 'string', 'description': 'Relationship stage, e.g. Prospect, Customer, Partner'},
        'Owner': {'type': 'string', 'description': 'Person record id of the account owner'},
        'LastContact': {'type': 'string', 'format': 'date'}}},
    'Deal': {'type': 'object', 'additionalProperties': False, 'required': ['Name'], 'properties': {
        'Name': {'type': 'string', 'minLength': 1}, 'Stage': {'type': 'string'},
        'Amount': {'type': 'number'}, 'Currency': {'type': 'string', 'pattern': '^[A-Z]{3}$'},
        'Company': _ref('Company', 'The company the deal is with'), 'CloseDate': {'type': 'string', 'format': 'date'}}},
    'Project': {'type': 'object', 'additionalProperties': False, 'required': ['Name'], 'properties': {
        'Name': {'type': 'string', 'minLength': 1}, 'Status': {'type': 'string'},
        'Company': _ref('Company', 'The company the project is for')}},
    # Desktop D2: the card type family. `x-card` makes a type a card type: its State follows the
    # lifecycle `_global/POLICIES.md` declares for it (`lifecycles.<type>`), it has members, a board
    # (the workspaces that reference it) and a face composed from blocks.v1's face profile.
    'Card': {'type': 'object', 'additionalProperties': False, 'required': ['Title'], 'x-card': True, 'properties': {
        'Title': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'About': {'type': 'string', 'maxLength': 600, 'description': 'What the card is about: page:<workspace>/<path>, '
                  'chat:<id>, workspace:<slug>, meeting:<id> or crm:<record id>. Membership never grants it.'},
        'State': {'type': 'string', 'maxLength': 60, 'description': "A stage of the type's lifecycle"},
        'Next': {'type': 'string', 'maxLength': 200, 'description': 'What happens next'},
        'Waiting': {'type': 'string', 'maxLength': 200, 'description': 'Who or what it is waiting on'},
        'Summary': {'type': 'string', 'maxLength': 500},
        'Due': {'type': 'string', 'format': 'date'},
        'Progress': {'type': 'number', 'minimum': 0, 'maximum': 1, 'x-format': 'percent01'},
        'Checklist': {'type': 'array', 'maxItems': 10, 'items': {'type': 'object', 'additionalProperties': False,
                      'required': ['text'], 'properties': {'text': {'type': 'string', 'maxLength': 80},
                                                           'done': {'type': 'boolean'}}}},
        'Parent': _ref('Card', 'The card this one is a sub-card of'),
        'Face': {'type': 'array', 'maxItems': 8, 'items': {'type': 'object'},
                 'description': "The card's face: blocks.v1 face-profile blocks, sanitised on write"}}},
}

#: A signed-in member's rights when no policy names them (decision 6, a default the founder may
#: strike): they create entities and read and write the ones they own; everything else reaches them
#: through a workspace binding or a policy an administrator writes.
DEFAULT_POLICY = {'active': True, 'objects': {
    kind: {'read': 'own', 'read_fields': ['*'], 'write': 'own', 'write_fields': ['*'], 'create': True}
    for kind in ENTITY_TYPES}}


def bootstrap_org(engine, org, name, default_policy=None):
    org = str(org or '').strip()
    if not org:
        raise ValueError('CRM_ORG_ID is required')
    created = []
    with engine.begin() as c:
        if c.execute(select(s.orgs.c.org_id).where(s.orgs.c.org_id == org)).scalar_one_or_none() is None:
            c.execute(s.orgs.insert().values(org_id=org, name=name or org, default_policy=default_policy or DEFAULT_POLICY,
                                             created_at=datetime.now(timezone.utc).isoformat()))
            created.append('org')
        for kind, definition in DEFINITIONS.items():
            if c.execute(select(s.object_types.c.name).where(s.object_types.c.org_id == org,
                                                             s.object_types.c.name == kind)).scalar_one_or_none() is None:
                c.execute(s.object_types.insert().values(org_id=org, name=kind, definition=definition))
                created.append(kind)
    return created


def ensure_personal_org(engine, org, name):
    """crm-api's runtime path to an individual's personal CRM on its first use (``orgs.py``).

    crm-api's role may not write the organisation, its policy or its definitions (they are
    configuration), so on Postgres this calls ``crm_bootstrap_personal_org``, the function the
    migrations path made for exactly this and lets that role execute (``migrate.py`` step 5). It
    accepts an org of one only. Elsewhere (SQLite, the tests) it is ``bootstrap_org`` itself."""
    from sqlalchemy import text

    from .orgs import PERSONAL_ORG, PERSONAL_POLICY
    org = str(org or '').strip()
    if not PERSONAL_ORG.fullmatch(org):
        raise ValueError("only an individual's own CRM (p-<id>) is made at runtime")
    if engine.dialect.name != 'postgresql':
        return bootstrap_org(engine, org, name, PERSONAL_POLICY)
    with engine.begin() as c:
        return list(c.execute(text('SELECT crm_bootstrap_personal_org(:org, :name)'),
                              {'org': org, 'name': name or org}).scalar_one() or [])
