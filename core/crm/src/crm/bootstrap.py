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
from .field_classes import default_classes

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

#: PRD 0.13.4 D11: the card as the one unit of obligation. Its KIND picks a seeded lifecycle template
#: (``card_templates.py``); every open card of a kind has exactly one structured next actor (a person
#: id, replacing free-text ``Waiting`` for routing) and a ``NextCheckAt`` the flows worker wakes on;
#: ``Chain`` links blockers; ``Origin`` names the chat or message it came from. ``Handoff`` is the
#: two-phase custody of the current gate (D12): ``offered`` to the next actor until they
#: ``acknowledged`` it. ``Answer``/``Evidence``/``Receipt`` are what a gate needs (a question's
#: answer, a delivery's evidence refs, an approval's effect receipt). Added to an existing org's
#: Card definition additively (``upgrade_card_definition``).
_NULL_STR = lambda n, d: {'type': ['string', 'null'], 'maxLength': n, 'description': d}  # noqa: E731
CARD_LIFECYCLE_PROPERTIES = {
    'Kind': {'type': ['string', 'null'], 'enum': ['ask', 'approval', 'question', 'commitment', 'invitation',
                                                  'policy_change', None],
             'description': 'What obligation this card is: it picks the seeded lifecycle template'},
    'NextActor': _NULL_STR(128, 'The person id whose move it is (the one next actor of an open card)'),
    'Requester': _NULL_STR(128, 'The person id waiting on the card: who confirms a delivery or uses an answer'),
    'NextCheckAt': _NULL_STR(40, 'ISO time the flows worker next checks the card (D11 invariant)'),
    'Handoff': {'type': ['string', 'null'], 'enum': ['offered', 'acknowledged', None],
                'description': 'Custody of the current gate: offered to the next actor, then acknowledged (D12)'},
    'HandoffSince': _NULL_STR(40, 'When the current handoff phase began'),
    'Chain': {'type': 'object', 'additionalProperties': False, 'properties': {
        'blocks': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'maxLength': 128}},
        'blocked_by': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'maxLength': 128}}},
        'description': 'Cards this one blocks and is blocked by'},
    'Origin': _NULL_STR(300, 'Where the card came from: session:<id> or message:<session>/<message id>'),
    'Answer': _NULL_STR(500, "A question's answer, given at the answer gate"),
    'Evidence': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string', 'maxLength': 300},
                 'description': 'References a delivery cites (a page, a meeting, a commit)'},
    'Receipt': _NULL_STR(300, "The effect receipt an approval's executed gate needs"),
}
DEFINITIONS['Card']['properties'].update(CARD_LIFECYCLE_PROPERTIES)

#: A signed-in member's rights when no policy names them (decision 6, a default the founder may
#: strike): they create entities and read and write the ones they own; everything else reaches them
#: through a workspace binding or a policy an administrator writes.
DEFAULT_POLICY = {'active': True, 'objects': {
    kind: {'read': 'own', 'read_fields': ['*'], 'write': 'own', 'write_fields': ['*'], 'create': True}
    for kind in ENTITY_TYPES}}


#: The seeded field classes of the stable entity types (``field_classes.default_classes``): contact
#: history and email-derived data private, company basics and the rest org.
DEFAULT_CLASSES = default_classes({k: DEFINITIONS[k] for k in ENTITY_TYPES})


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
        # D4: the field classification of the stable types, as seeded policy an administrator may
        # change. Only missing rows are written; a class someone set is never overwritten.
        fc = s.field_classes
        have = {(k, f) for k, f in c.execute(select(fc.c.object_type, fc.c.field).where(fc.c.org_id == org)).all()}
        now = datetime.now(timezone.utc).isoformat()
        seeded = 0
        for row in DEFAULT_CLASSES:
            if (row['object_type'], row['field']) not in have:
                c.execute(fc.insert().values(org_id=org, set_by='seed', set_at=now, **row))
                seeded += 1
        if seeded:
            created.append('field_classes')
    return created


def upgrade_card_definition(engine, org):
    """Add the D11 lifecycle properties to an org's existing ``Card`` definition — additively: a
    property an administrator already defined is left exactly as it is, nothing is removed, and an
    org with no Card type is untouched. Run by the migrations path after ``bootstrap_org``."""
    with engine.begin() as c:
        row = c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == org, s.object_types.c.name == 'Card')).scalar_one_or_none()
        if not isinstance(row, dict):
            return False
        props = dict(row.get('properties') or {})
        missing = {k: v for k, v in CARD_LIFECYCLE_PROPERTIES.items() if k not in props}
        if not missing:
            return False
        c.execute(s.object_types.update().where(s.object_types.c.org_id == org, s.object_types.c.name == 'Card')
                  .values(definition={**row, 'properties': {**props, **missing}}))
        return True


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
