import pytest
from control_plane import git_secret_store as store, legacy_git_store as local, git_credentials
from tests import legacy_git_store_fixture as fixture


@pytest.fixture
def remote(git_broker):
    return git_broker


def test_migrate_confirm_remove_preserve_value(tmp_path, remote):
    fixture.put(tmp_path, 'deploy/user-2.priv', 'fixture-key')
    assert store.get(tmp_path, 'deploy/user-2.priv') == 'fixture-key'
    assert remote['deploy/user-2.priv'] == 'fixture-key'
    assert local.get(tmp_path, 'deploy/user-2.priv') is None
    assert not (tmp_path / '.secrets').exists(), 'the generated key goes once nothing is left to open'


def test_unavailable_never_falls_back_or_deletes(tmp_path, remote, monkeypatch):
    fixture.put(tmp_path, 'pat/2', 'fixture-token')
    def fail(*a): raise store.GitStoreUnavailable('unavailable')
    monkeypatch.setattr(store, '_call', fail)
    with pytest.raises(store.GitStoreUnavailable): store.get(tmp_path, 'pat/2')
    assert local.get(tmp_path, 'pat/2') == 'fixture-token'
    with pytest.raises(store.GitStoreUnavailable): store.put(tmp_path, 'pat/2', 'new')
    assert local.get(tmp_path, 'pat/2') == 'fixture-token'


def test_remote_tombstone_prevents_legacy_resurrection(tmp_path, remote):
    fixture.put(tmp_path, 'pat/2', 'fixture-token')
    (tmp_path / '.secrets/2.ghtoken').write_text('old-plaintext')
    remote['pat/2'] = None
    assert git_credentials.read_github_token(tmp_path, '2') is None


def test_plaintext_migrates_only_after_confirmation(tmp_path, remote):
    p = tmp_path / '.secrets/2.ghtoken'; p.parent.mkdir(); p.write_text('fixture-token')
    assert git_credentials.read_github_token(tmp_path, '2') == 'fixture-token'
    assert not p.exists()
    assert remote['pat/2'] == 'fixture-token'


def test_an_entry_sealed_under_the_operator_key_migrates_with_it(tmp_path, remote, monkeypatch):
    fixture.put(tmp_path, 'pat/2', 'fixture-token', operator_key='fixture-operator-key')
    monkeypatch.setenv('VEXA_SECRETS_KEY', 'fixture-operator-key')
    assert store.get(tmp_path, 'pat/2') == 'fixture-token'
    assert remote['pat/2'] == 'fixture-token'


def test_an_entry_the_key_cannot_open_is_a_refusal_and_is_kept(tmp_path, remote, monkeypatch):
    held = fixture.put(tmp_path, 'pat/2', 'fixture-token', operator_key='fixture-operator-key')
    monkeypatch.setenv('VEXA_SECRETS_KEY', 'a-different-key')
    with pytest.raises(store.GitStoreUnavailable): store.get(tmp_path, 'pat/2')
    assert held.exists() and 'pat/2' not in remote


def test_new_save_and_revoke_use_remote(tmp_path, remote):
    assert git_credentials.set_github_token(tmp_path, '2', 'fixture-token')
    assert remote['pat/2'] == 'fixture-token'
    assert not (tmp_path / '.secrets').exists()
    assert not git_credentials.set_github_token(tmp_path, '2', None)
    assert git_credentials.read_github_token(tmp_path, '2') is None
