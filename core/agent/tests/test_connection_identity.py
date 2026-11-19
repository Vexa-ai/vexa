import pytest
from workspaces.shared import entities as E


def test_wrapped_names_are_canonical_and_idempotent(tmp_path):
    for name in ['Dana Pike','[[Dana Pike]]','[[[[Dana Pike]]]]']:
        E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':name,'relation':'works here'}])
    text=(tmp_path/'kg/entities/company/design-studio.md').read_text()
    assert text.count('- [[Dana Pike]] — works here') == 1
    assert '[[[[' not in text


def test_combined_names_are_rejected_without_writes(tmp_path):
    with pytest.raises(E.EntityMalformed,match='separate'):
        E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=['Dana Pike; Juan (design partner)'])
    assert not list(tmp_path.rglob('*.md'))


def test_relationless_nested_duplicate_is_repaired_without_losing_other_relations(tmp_path):
    result=E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':'Dana Pike','relation':'works here'}])
    path=tmp_path/result['path']
    path.write_text(path.read_text().replace('## Connected\n','## Connected\n- [[[[Dana Pike]]]]\n- [[Dana Pike]] — advises\n'))
    E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':'[[Dana Pike]]','relation':'works here'}])
    text=path.read_text()
    assert text.count('— works here')==1
    assert '— advises' in text
    assert '[[[[' not in text


def test_generic_link_does_not_erase_distinct_relations(tmp_path):
    args=(tmp_path,'company','Design Studio',['Design work.'],'fixture')
    E.upsert_entity(*args,connections=[{'name':'Dana Pike','relation':'works here'},{'name':'Dana Pike','relation':'advises'}])
    E.upsert_entity(*args,connections=['[[Dana Pike]]'])
    text=(tmp_path/'kg/entities/company/design-studio.md').read_text()
    assert '— works here' in text and '— advises' in text


def test_a_combined_field_is_filed_as_written_and_links_each_name(tmp_path):
    """A FIELD is prose (Vexa-ai/vexa#1783): refusing the whole call made the agent strip its own
    write-back. `connections` (above) stays one-name-per-entry; a field's names are each linked."""
    r=E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',fields={'people':'Dana Pike; Juan (design partner)'})
    text=(tmp_path/r['path']).read_text()
    assert 'Dana Pike; Juan (design partner)' in text
    assert '[[Dana Pike]]' in text and '[[Juan]]' in text
    assert '[[Dana Pike; Juan' not in text
