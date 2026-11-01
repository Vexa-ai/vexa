"""An install extends its org's definitions with its own fields through the migrations path
(``CRM_DEFINITIONS_FILE`` → ``bootstrap.extend_definitions``): additive, idempotent, validated, one
transaction; the product's own fields and privacy classes are not the file's to set.

Offline: SQLite. No containers.
"""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from crm import migrate
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
    assert extend_definitions(engine, ORG, ext) == (['Company.SizeTier', 'Person.Sources'], [])
    assert _props(engine, 'Company')['SizeTier']['maxLength'] == 40
    assert 'Name' in _props(engine, 'Company')                         # nothing removed
    assert extend_definitions(engine, ORG, ext) == ([], [])            # idempotent, nothing reported


def test_a_changed_install_field_is_reported_and_left_as_it_is(engine):
    extend_definitions(engine, ORG, {'Company': {'SizeTier': {'type': 'string', 'maxLength': 40}}})
    assert extend_definitions(engine, ORG, {'Company': {'SizeTier': {'type': 'integer'}}}) == ([], ['Company.SizeTier'])
    assert _props(engine, 'Company')['SizeTier'] == {'type': 'string', 'maxLength': 40}


@pytest.mark.parametrize('kind, field', [('Company', 'Name'), ('Person', 'LastContact'), ('Card', 'Kind'),
                                         ('Card', 'NextActor'), ('Card', 'Title')])
def test_a_field_the_product_defines_is_refused(engine, kind, field):
    before = _props(engine, kind)[field]
    with pytest.raises(ValueError, match='the product defines this field'):
        extend_definitions(engine, ORG, {kind: {field: {'type': ['string', 'null']}}})
    assert _props(engine, kind)[field] == before


@pytest.mark.parametrize('prop', [
    {'type': 'string', 'x-class': {'ws': ['team']}},                   # a workspace share
    {'type': 'string', 'x-class': 'ws'},
    {'type': 'string', 'x-class': 'public'},                           # not a class the product knows
    {'type': 'string', 'x-source': 'linkedin'},                        # not a source the product knows
    {'type': 'string', 'x-source': 'email', 'x-class': 'org'},         # email-derived made org-readable
])
def test_a_privacy_class_the_file_may_not_set_is_refused(engine, prop):
    with pytest.raises(ValueError, match='x-class|x-source'):
        extend_definitions(engine, ORG, {'Company': {'Notes2': prop}})
    assert 'Notes2' not in _props(engine, 'Company')


def test_a_private_name_cannot_be_declared_org(engine):
    with pytest.raises(ValueError, match='x-class org'):
        extend_definitions(engine, ORG, {'Person': {'LastEmailAt': {'type': 'string', 'x-class': 'org'}}})


def test_the_privacy_a_file_may_declare_is_accepted(engine):
    ext = {'Company': {'Secret': {'type': 'string', 'x-class': 'private'},
                       'Plain': {'type': 'string', 'x-class': 'org'},
                       'Mailed': {'type': 'string', 'x-source': 'email'}}}
    assert extend_definitions(engine, ORG, ext)[0] == ['Company.Secret', 'Company.Plain', 'Company.Mailed']


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


def test_a_later_type_failing_rolls_back_an_earlier_one(engine):
    before = _props(engine, 'Company')
    with pytest.raises(ValueError):
        extend_definitions(engine, ORG, {'Company': {'SizeTier': {'type': 'string'}},
                                         'Person': {'Bad': 'string'}})
    assert _props(engine, 'Company') == before


# ── `python -m crm.migrate init` with the file: Postgres's part stubbed, the rest real (SQLite) ──
@pytest.fixture
def init_env(engine, monkeypatch):
    monkeypatch.setenv('CRM_DB_PASSWORD', 'x' * 24)
    monkeypatch.setenv('CRM_ORG_ID', ORG)
    monkeypatch.delenv('CRM_DEFINITIONS_FILE', raising=False)
    monkeypatch.setattr(migrate, 'create_engine', lambda *a, **k: engine)
    monkeypatch.setattr(migrate, 'init', lambda *a, **k: None)
    return engine


def test_init_without_the_file_adds_nothing(init_env, capsys):
    before = _props(init_env, 'Company')
    assert migrate.main(['x', 'init']) == 0
    assert 'CRM_DEFINITIONS_FILE' not in capsys.readouterr().out
    assert _props(init_env, 'Company') == before


def test_init_with_a_missing_file_stops(init_env, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('CRM_DEFINITIONS_FILE', str(tmp_path / 'absent.json'))
    assert migrate.main(['x', 'init']) == 1
    assert 'STOP: CRM_DEFINITIONS_FILE' in capsys.readouterr().out


def test_init_with_bad_json_stops(init_env, monkeypatch, tmp_path, capsys):
    f = tmp_path / 'defs.json'
    f.write_text('{"Company": {', encoding='utf-8')
    monkeypatch.setenv('CRM_DEFINITIONS_FILE', str(f))
    assert migrate.main(['x', 'init']) == 1
    assert 'STOP: CRM_DEFINITIONS_FILE' in capsys.readouterr().out


def test_init_with_an_invalid_schema_stops(init_env, monkeypatch, tmp_path, capsys):
    f = tmp_path / 'defs.json'
    f.write_text(json.dumps({'Company': {'X': {'type': 'not-a-type'}}}), encoding='utf-8')
    monkeypatch.setenv('CRM_DEFINITIONS_FILE', str(f))
    assert migrate.main(['x', 'init']) == 1
    assert 'STOP: CRM_DEFINITIONS_FILE: SchemaError' in capsys.readouterr().out


def test_init_adds_then_reports_a_difference(init_env, monkeypatch, tmp_path, capsys):
    f = tmp_path / 'defs.json'
    f.write_text(json.dumps({'Company': {'SizeTier': {'type': 'string'}}}), encoding='utf-8')
    monkeypatch.setenv('CRM_DEFINITIONS_FILE', str(f))
    assert migrate.main(['x', 'init']) == 0
    assert 'fields Company.SizeTier' in capsys.readouterr().out
    f.write_text(json.dumps({'Company': {'SizeTier': {'type': 'integer'}}}), encoding='utf-8')
    assert migrate.main(['x', 'init']) == 0
    out = capsys.readouterr().out
    assert 'Company.SizeTier differs from the stored definition; left as is' in out
    assert 'left as is (the file differs from the stored definition): Company.SizeTier' in out
    assert _props(init_env, 'Company')['SizeTier'] == {'type': 'string'}
