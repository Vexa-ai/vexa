"""An install extends its org's definitions with its own fields through the migrations path
(``CRM_DEFINITIONS_FILE`` → ``bootstrap.extend_definitions``): additive, idempotent, validated.

Offline: SQLite. No containers.
"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org, extend_definitions

ORG = 'acme'


@pytest.fixture
def engine():
    e = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    s.metadata.create_all(e)
    bootstrap_org(e, ORG, 'Acme')
    return e


def _props(engine, kind):
    with engine.connect() as c:
        return c.execute(select(s.object_types.c.definition).where(
            s.object_types.c.org_id == ORG, s.object_types.c.name == kind)).scalar_one()['properties']


def test_adds_new_fields_and_is_idempotent(engine):
    ext = {'Company': {'SizeTier': {'type': ['string', 'null'], 'maxLength': 40}},
           'Person': {'Sources': {'type': ['object', 'null']}}}
    assert extend_definitions(engine, ORG, ext) == ['Company.SizeTier', 'Person.Sources']
    assert _props(engine, 'Company')['SizeTier']['maxLength'] == 40
    assert 'Name' in _props(engine, 'Company')                         # nothing removed
    assert extend_definitions(engine, ORG, ext) == []                  # idempotent


def test_an_existing_field_is_left_as_it_is(engine):
    before = _props(engine, 'Company')['Name']
    assert extend_definitions(engine, ORG, {'Company': {'Name': {'type': 'integer'}}}) == []
    assert _props(engine, 'Company')['Name'] == before


@pytest.mark.parametrize('ext', [
    {'Nope': {'X': {'type': 'string'}}},                               # a type the org lacks
    {'Company': {'lower': {'type': 'string'}}},                        # not a field name
    {'Company': {'X': 'string'}},                                      # not a property object
    ['Company'],
])
def test_refuses_what_is_not_an_additive_field(engine, ext):
    with pytest.raises(ValueError):
        extend_definitions(engine, ORG, ext)


def test_refuses_an_invalid_json_schema(engine):
    from jsonschema.exceptions import SchemaError
    with pytest.raises(SchemaError):
        extend_definitions(engine, ORG, {'Company': {'X': {'type': 'not-a-type'}}})
    assert 'X' not in _props(engine, 'Company')
