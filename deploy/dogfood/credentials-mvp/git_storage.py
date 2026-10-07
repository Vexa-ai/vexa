"""Private Git credential RPC; accepts only the separately signed git service role."""
import os
import uuid
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
import vault_client

class GitSecret(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(pattern=r'^(pat/[A-Za-z0-9_.-]{1,128}|deploy/(user|ws)-[A-Za-z0-9_.-]{1,120}\.(priv|pub))$')
    action: Literal['get','put','migrate']
    value: str | None=Field(default=None,max_length=32768)

def register(app, *, root, identity, audit, lock):
    @app.post('/api/internal/git-secret')
    def git_secret(request: Request, body: GitSecret):
        who=identity(request,{'git'})
        owner=body.name.split('/',1)[1].removesuffix('.priv').removesuffix('.pub')
        if '..' in body.name or who['actor']!=owner:
            raise HTTPException(403,'Git credential scope refused')
        path='connections/data/git/'+body.name
        def call(method,payload=None,missing=False):
            try:return vault_client.request(root,os.environ['BAO_ADDR'],method,path,payload,allow_missing=missing)
            except vault_client.VaultUnavailable:raise HTTPException(503,'Git credential store unavailable') from None
        operation=uuid.uuid4().hex
        with lock:
            audit(who,body.name,'git.credential.'+body.action,'requested',operation=operation)
            stored=call('GET',missing=True)
            if body.action=='put' or body.action=='migrate' and stored is None:
                # A single broker lock serializes migration with writes/revocation. CAS
                # also refuses an external writer racing the read.
                version=stored['data']['metadata']['version'] if stored else 0
                call('POST',{'options':{'cas':version},'data':{'value':body.value}})
                stored=call('GET')
            audit(who,body.name,'git.credential.'+body.action,'success',operation=operation,
                  vault=(stored or {}).get('request_id',''),
                  version=stored['data']['metadata']['version'] if stored else 0)
            return {'found':stored is not None,'value':stored['data']['data'].get('value') if stored else None}
