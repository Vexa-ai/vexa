import pytest
from control_plane import git_secret_store as store, secret_store as local, git_credentials

@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('VEXA_GIT_STORE_BROKER_URL','http://fixture')
    values={}
    def call(name,action,value=None):
        if action=='put' or action=='migrate' and name not in values:values[name]=value
        return {'found':name in values,'value':values.get(name)}
    monkeypatch.setattr(store,'_call',call)
    return values

def test_migrate_confirm_remove_preserve_value(tmp_path,remote):
    local.put(tmp_path,'deploy/user-2.priv','fixture-key')
    assert store.get(tmp_path,'deploy/user-2.priv')=='fixture-key'
    assert remote['deploy/user-2.priv']=='fixture-key'
    assert local.get(tmp_path,'deploy/user-2.priv') is None

def test_unavailable_never_falls_back_or_deletes(tmp_path,remote,monkeypatch):
    local.put(tmp_path,'pat/2','fixture-token')
    def fail(*a):raise store.GitStoreUnavailable('unavailable')
    monkeypatch.setattr(store,'_call',fail)
    with pytest.raises(store.GitStoreUnavailable):store.get(tmp_path,'pat/2')
    assert local.get(tmp_path,'pat/2')=='fixture-token'
    with pytest.raises(store.GitStoreUnavailable):store.put(tmp_path,'pat/2','new')
    assert local.get(tmp_path,'pat/2')=='fixture-token'

def test_remote_tombstone_prevents_legacy_resurrection(tmp_path,remote):
    local.put(tmp_path,'pat/2','fixture-token')
    (tmp_path/'.secrets/2.ghtoken').write_text('old-plaintext')
    remote['pat/2']=None
    assert git_credentials.read_github_token(tmp_path,'2') is None

def test_plaintext_migrates_only_after_confirmation(tmp_path,remote):
    p=tmp_path/'.secrets/2.ghtoken';p.parent.mkdir();p.write_text('fixture-token')
    assert git_credentials.read_github_token(tmp_path,'2')=='fixture-token'
    assert not p.exists()
    assert remote['pat/2']=='fixture-token'

def test_new_save_and_revoke_use_remote(tmp_path,remote):
    assert git_credentials.set_github_token(tmp_path,'2','fixture-token')
    assert remote['pat/2']=='fixture-token'
    assert not (tmp_path/'.secrets').exists()
    assert not git_credentials.set_github_token(tmp_path,'2',None)
    assert git_credentials.read_github_token(tmp_path,'2') is None
