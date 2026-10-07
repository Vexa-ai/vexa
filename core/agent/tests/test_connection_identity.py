import pytest
from workspaces.shared import entities as E


def test_wrapped_names_are_canonical_and_idempotent(tmp_path):
    for name in ['Megan Blume','[[Megan Blume]]','[[[[Megan Blume]]]]']:
        E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':name,'relation':'works here'}])
    text=(tmp_path/'kg/entities/company/design-studio.md').read_text()
    assert text.count('- [[Megan Blume]] — works here') == 1
    assert '[[[[' not in text


def test_combined_names_are_rejected_without_writes(tmp_path):
    with pytest.raises(E.EntityMalformed,match='separate'):
        E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=['Megan Blume; Juan (design partner)'])
    assert not list(tmp_path.rglob('*.md'))


def test_relationless_nested_duplicate_is_repaired_without_losing_other_relations(tmp_path):
    result=E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':'Megan Blume','relation':'works here'}])
    path=tmp_path/result['path']
    path.write_text(path.read_text().replace('## Connected\n','## Connected\n- [[[[Megan Blume]]]]\n- [[Megan Blume]] — advises\n'))
    E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',connections=[{'name':'[[Megan Blume]]','relation':'works here'}])
    text=path.read_text()
    assert text.count('— works here')==1
    assert '— advises' in text
    assert '[[[[' not in text


def test_generic_link_does_not_erase_distinct_relations(tmp_path):
    args=(tmp_path,'company','Design Studio',['Design work.'],'fixture')
    E.upsert_entity(*args,connections=[{'name':'Megan Blume','relation':'works here'},{'name':'Megan Blume','relation':'advises'}])
    E.upsert_entity(*args,connections=['[[Megan Blume]]'])
    text=(tmp_path/'kg/entities/company/design-studio.md').read_text()
    assert '— works here' in text and '— advises' in text


def test_combined_field_is_rejected_without_writes(tmp_path):
    with pytest.raises(E.EntityMalformed):
        E.upsert_entity(tmp_path,'company','Design Studio',['Design work.'],'fixture',fields={'people':'Megan Blume; Juan (design partner)'})
    assert not list(tmp_path.rglob('*.md'))
