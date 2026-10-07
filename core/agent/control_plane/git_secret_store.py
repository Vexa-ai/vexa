"""Git-only credential adapter. The broker is mandatory when configured.

Only the trusted control plane holds the git service signing key. MCP and the
browser have no endpoint that returns values. Existing local entries migrate on
first use; a confirmed remote tombstone prevents resurrecting a deleted key.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
import httpx
from control_plane import secret_store as legacy

SECRETS_DIRNAME = legacy.SECRETS_DIRNAME
NAME = re.compile(r'^(pat/[A-Za-z0-9_.-]{1,128}|deploy/(?:user|ws)-[A-Za-z0-9_.-]{1,120}\.(?:priv|pub))$')

class GitStoreUnavailable(RuntimeError):
    pass

def enabled():
    return bool(os.environ.get('VEXA_GIT_STORE_BROKER_URL'))

def _call(name, action, value=None):
    if not NAME.fullmatch(name) or '..' in name:
        raise ValueError('Invalid Git credential name')
    path='/api/internal/git-secret'
    body=json.dumps({'name':name,'action':action,'value':value},separators=(',',':')).encode()
    actor=name.split('/',1)[1].removesuffix('.priv').removesuffix('.pub')
    try:
        key=Path(os.environ['VEXA_GIT_STORE_KEY_FILE']).read_bytes().strip()
        if len(key)<32:raise ValueError()
        claims={'role':'git','actor':actor,'session':'git-request-'+uuid.uuid4().hex,
                'at':int(time.time()),'nonce':uuid.uuid4().hex,'method':'POST','path':path,
                'body':hashlib.sha256(body).hexdigest()}
        encoded=base64.urlsafe_b64encode(json.dumps(claims,separators=(',',':')).encode()).decode().rstrip('=')
        signature=hmac.new(key,encoded.encode(),hashlib.sha256).hexdigest()
        with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:
            r=client.post(os.environ['VEXA_GIT_STORE_BROKER_URL'].rstrip('/')+path,content=body,
                          headers={'X-Vexa-Assertion':encoded+'.'+signature,'Content-Type':'application/json'})
        if r.status_code!=200:raise ValueError()
        result=r.json()
        if not isinstance(result.get('found'),bool) or result.get('value') is not None and not isinstance(result['value'],str):raise ValueError()
        return result
    except (KeyError,OSError,ValueError,httpx.HTTPError):
        raise GitStoreUnavailable('Git credential store unavailable; retry when Connections is healthy') from None

def get(root,name,*,key_env=''):
    if not enabled():return legacy.get(root,name,key_env=key_env)
    remote=_call(name,'get')
    if remote['found']:return remote.get('value')
    value=legacy.get(root,name,key_env=key_env)
    plaintext=Path(root)/SECRETS_DIRNAME/(name.split('/',1)[1]+'.ghtoken') if name.startswith('pat/') else None
    if value is None and plaintext is not None and plaintext.exists():
        value=plaintext.read_text().strip() or None
    if value is None:
        if legacy.state(root,name,key_env=key_env)==legacy.UNREADABLE:
            raise GitStoreUnavailable('Existing Git credential cannot be decrypted for migration')
        return None
    remote=_call(name,'migrate',value)
    if remote['found'] and remote.get('value')==value:
        legacy.delete(root,name)
        if plaintext is not None:plaintext.unlink(missing_ok=True)
    return remote.get('value')

def put(root,name,value,*,key_env=''):
    if not enabled():return legacy.put(root,name,value,key_env=key_env)
    value=(value or '').strip() or None
    remote=_call(name,'put',value)
    if not remote['found'] or remote.get('value')!=value:
        raise GitStoreUnavailable('Git credential storage confirmation failed')
    legacy.delete(root,name)
    if name.startswith('pat/'):(Path(root)/SECRETS_DIRNAME/(name.split('/',1)[1]+'.ghtoken')).unlink(missing_ok=True)
    return value is not None

def delete(root,name):
    if not enabled():return legacy.delete(root,name)
    existed=get(root,name) is not None
    put(root,name,None)
    return existed
