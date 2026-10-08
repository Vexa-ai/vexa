"""Durable import jobs: scoped status, duplicate suppression and explicit interruption."""
import threading
import time
import json
import pytest
from fastapi import HTTPException
from control_plane import workspace_import as jobs


def finish(root, subject, operation_id):
    for _ in range(200):
        result = jobs.status(root, subject, operation_id)
        if result['status'] not in ('queued', 'running'):
            return result
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_slow_operation_is_deduplicated_and_subject_scoped(tmp_path):
    release = threading.Event(); calls = []
    def operation():
        calls.append(1)
        assert release.wait(3)
        return {'workspace': 'repo', 'nested': False}
    first = jobs.start(tmp_path, 'u1', 'git@github.com:acme/repo.git', 'main', operation)
    again = jobs.start(tmp_path, 'u1', 'git@github.com:acme/repo.git', 'main', operation)
    assert again['operation_id'] == first['operation_id']
    assert jobs.status(tmp_path, 'u1', first['operation_id'])['status'] in ('queued', 'running')
    with pytest.raises(HTTPException) as denied:
        jobs.status(tmp_path, 'u2', first['operation_id'])
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as conflict:
        jobs.start(tmp_path, 'u1', 'git@github.com:acme/other.git', 'main', operation)
    assert conflict.value.status_code == 409
    release.set()
    assert finish(tmp_path, 'u1', first['operation_id'])['result']['workspace'] == 'repo'
    assert calls == [1]


def test_failed_import_can_retry_after_credentials_are_fixed(tmp_path):
    def fail(): raise HTTPException(403, 'Repository access denied')
    first = jobs.start(tmp_path, 'u1', 'repo', 'main', fail)
    assert finish(tmp_path, 'u1', first['operation_id'])['status'] == 'failed'
    second = jobs.start(tmp_path, 'u1', 'repo', 'main', lambda: {'workspace': 'repo'})
    assert finish(tmp_path, 'u1', second['operation_id'])['status'] == 'completed'


def test_interrupted_operation_is_not_silently_replayed(tmp_path):
    folder = tmp_path / '.imports' / 'u1'; folder.mkdir(parents=True)
    import hashlib
    key = hashlib.sha256(b'repo\nmain').hexdigest()[:32]
    (folder / f'{key}.json').write_text(json.dumps({'operation_id': key, 'status': 'running'}))
    result = jobs.start(tmp_path, 'u1', 'repo', 'main', lambda: pytest.fail('replayed'))
    assert result['status'] == 'interrupted'


def test_github_https_switches_to_ssh_only_when_the_import_uses_a_deploy_key():
    assert jobs.repository_url('https://github.com/acme/private', use_deploy_key=True) == 'git@github.com:acme/private.git'
    assert jobs.repository_url('https://github.com/acme/private', use_deploy_key=False) == 'https://github.com/acme/private.git'
    assert jobs.ssh_form('https://github.com/acme/private.git') == 'git@github.com:acme/private.git'
    assert jobs.ssh_form('https://gitlab.example.com/acme/private.git') is None


def _import_with_fake_clone(tmp_path, monkeypatch, refuse_https=False):
    """Import `octocat/Spoon-Knife` as u_jane, who has minted her own deploy key. The clone is faked:
    it records every URL it is asked for and, with ``refuse_https``, answers an https clone the way
    git does when a repository needs a credential."""
    from tests.test_workspace_manage_routes import _client, _seed_primary
    from control_plane import deploy_keys, workspace_attach
    _seed_primary(tmp_path, 'u_jane')
    deploy_keys.ensure(tmp_path, deploy_keys.workspace_key(subject='u_jane'))
    asked = []

    def fake_clone(repo, ref, dest, token=None, **kwargs):
        asked.append(repo)
        if refuse_https and repo.startswith('https://'):
            raise workspace_attach.CloneError('fatal: could not read Username for https://github.com: terminal prompts disabled')
        dest.mkdir(parents=True)
        (dest / 'README.md').write_text('spoon\n')

    monkeypatch.setattr(workspace_attach, '_git_clone', fake_clone)
    client = _client(tmp_path)
    response = client.post('/api/workspace/import', headers={'X-User-Id': 'u_jane'},
                           json={'repo': 'https://github.com/octocat/Spoon-Knife'})
    assert response.status_code == 202, response.text
    return finish(tmp_path, 'u_jane', response.json()['operation_id']), asked


def test_a_person_with_a_deploy_key_still_imports_a_public_repository_over_https(tmp_path, monkeypatch):
    result, asked = _import_with_fake_clone(tmp_path, monkeypatch)
    assert result['status'] == 'completed', result
    assert asked == ['https://github.com/octocat/Spoon-Knife.git']
    assert result['result']['repo'] == 'https://github.com/octocat/Spoon-Knife.git'


def test_an_https_clone_refused_for_a_credential_retries_with_the_deploy_key(tmp_path, monkeypatch):
    result, asked = _import_with_fake_clone(tmp_path, monkeypatch, refuse_https=True)
    assert result['status'] == 'completed', result
    assert asked == ['https://github.com/octocat/Spoon-Knife.git', 'git@github.com:octocat/Spoon-Knife.git']
    assert result['result']['repo'] == 'git@github.com:octocat/Spoon-Knife.git'


def test_import_http_and_mcp_manifest_expose_same_operation(tmp_path, monkeypatch):
    from tests.test_workspace_manage_routes import _client, _seed_primary
    from tests.test_workspace_attach import _make_repo
    import pathlib
    _seed_primary(tmp_path, 'u_jane')
    origin = _make_repo(tmp_path / 'original', 'RAW', compliant=False)
    from control_plane import workspace_attach
    clone = workspace_attach._git_clone
    monkeypatch.setattr(workspace_attach, '_git_clone', lambda repo, ref, dest, token=None, **kwargs: clone(origin, ref, dest, token, **kwargs))
    client = _client(tmp_path)
    response = client.post('/api/workspace/import', headers={'X-User-Id':'u_jane'}, json={'repo': 'https://github.com/example/plain'})
    assert response.status_code == 202
    operation_id = response.json()['operation_id']
    result = finish(tmp_path, 'u_jane', operation_id)
    assert result['status'] == 'completed', result
    assert result['result']['nested'] is False
    slug = result['result']['workspace']
    identity = client.get('/api/workspaces/by-slug/'+slug, headers={'X-User-Id':'u_jane'})
    assert identity.json()['access'] == 'readable'
    assert identity.json()['name'] == 'plain'
    assert client.get('/api/workspaces/by-slug/'+slug, headers={'X-User-Id':'another'}).json()['access'] != 'readable'
    assert client.get('/api/workspace/file', params={'slug':slug, 'path':'MARK'}, headers={'X-User-Id':'u_jane'}).json()['content'] == 'RAW'
    assert client.get('/api/workspace/file', params={'slug':slug, 'path':'MARK'}, headers={'X-User-Id':'another'}).status_code in (403,404)
    assert client.get('/api/workspace/import/'+operation_id+'/status', headers={'X-User-Id':'another'}).status_code == 404
    manifest = json.loads((pathlib.Path(__file__).parents[1] / 'mcp.tools.v1.json').read_text())
    names = {t['name']:t for t in manifest['tools']}
    assert names['workspace_import']['route']['path'] == '/api/workspace/import'
    assert 'token' not in names['workspace_import']['arguments']
    assert names['workspace_import_status']['route']['path'] == '/api/workspace/import/{operation_id}/status'


def test_only_completed_import_changes_chat_focus():
    from llm.claude_code import _workspace_focus
    assert _workspace_focus(json.dumps({'status':'running','result':{'workspace':'repo'}})) is None
    assert _workspace_focus(json.dumps({'status':'completed','result':{'workspace':'repo','name':'Repository'}}))['workspace'] == 'repo'


def test_import_can_reuse_an_owned_workspace_key_without_widening_access(tmp_path, monkeypatch):
    from tests.test_workspace_manage_routes import _client, _seed_primary
    from tests.test_workspace_attach import _make_repo
    from control_plane import workspace_attach
    _seed_primary(tmp_path, 'u_jane')
    origin = _make_repo(tmp_path / 'original', 'RAW', compliant=False)
    clone = workspace_attach._git_clone
    monkeypatch.setattr(workspace_attach, '_git_clone', lambda repo, ref, dest, token=None, **kwargs: clone(origin, ref, dest, token, **kwargs))
    client = _client(tmp_path)
    headers = {'X-User-Id': 'u_jane'}
    source = client.post('/api/workspace/shared/new', headers=headers, json={'name':'Credential source'}).json()['workspace_id']
    key = client.post(f'/api/workspace/{source}/deploy-key', headers=headers, json={}).json()
    payload = {'repo':'https://github.com/example/plain', 'credential_workspace':source}
    denied = client.post('/api/workspace/import', headers={'X-User-Id':'another'}, json=payload)
    assert denied.status_code == 403
    response = client.post('/api/workspace/import', headers=headers, json=payload)
    assert response.status_code == 202
    result = finish(tmp_path, 'u_jane', response.json()['operation_id'])
    assert result['status'] == 'completed', result
    assert result['repo'].startswith('git@github.com:')
    slug = result['result']['workspace']
    assert slug != source
    bound = client.get(f'/api/workspace/{slug}/deploy-key', headers=headers).json()
    assert bound['fingerprint'] == key['fingerprint']
    assert bound['public_key'] == key['public_key']
