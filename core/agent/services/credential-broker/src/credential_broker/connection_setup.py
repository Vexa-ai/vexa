"""Declarative setup proposals. Only human submission activates their configuration."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from . import secret_service

class InputField(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,39}$')
    label:str=Field(min_length=1,max_length=80)
    location:Literal['query','body']='body'

class OAuthSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    authorization_url:str=Field(max_length=2000)
    token_url:str=Field(max_length=2000)
    scopes:list[str]=Field(min_length=1,max_length=30)
    token_auth:Literal['client_secret_post','client_secret_basic']='client_secret_post'

class SetupSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    oauth:OAuthSpec|None=None
    documentation_url:str=Field(default='',max_length=2000)
    endpoint:str=Field(default='',max_length=2000)
    header:str=Field(default='Authorization',max_length=64)
    scheme:Literal['bearer','raw','telegram']='bearer'
    method:Literal['GET','POST']='GET'
    secret_label:str=Field(default='API token',min_length=1,max_length=80)
    fields:list[InputField]=Field(default_factory=list,max_length=10)


def validate(raw):
    s=SetupSpec.model_validate(raw)
    if s.oauth:
        if s.fields:raise ValueError('OAuth call arguments belong in each service call, not application setup')
        for url in (s.oauth.authorization_url,s.oauth.token_url):secret_service.validate_endpoint(url)
        if s.scheme!='bearer' or s.header!='Authorization':raise ValueError('OAuth uses Bearer authorization')
        if any(not scope or len(scope)>300 or any(c.isspace() for c in scope) for scope in s.oauth.scopes):raise ValueError('Invalid OAuth scopes')
    if s.scheme=='telegram':
        if s.endpoint not in ['https://api.telegram.org/bot{secret}/sendMessage','https://api.telegram.org/bot{secret}/getMe','https://api.telegram.org/bot{secret}/getUpdates']:
            raise ValueError('Choose a supported Telegram bot endpoint')
        expected='POST' if s.endpoint.endswith('/sendMessage') else 'GET'
        if s.method!=expected:raise ValueError('Telegram method does not match endpoint')
        if expected=='POST' and not any(f.name=='chat_id' and f.location=='body' for f in s.fields):raise ValueError('Telegram sendMessage requires a chat_id field filled by the user')
    else:
        secret_service.configure('validation-only',s.endpoint,s.header,s.scheme,s.method)
    if not s.endpoint:raise ValueError('Prepare the HTTPS endpoint before requesting setup')
    if len({f.name for f in s.fields})!=len(s.fields):raise ValueError('Field names must be unique')
    if s.method=='GET' and any(f.location=='body' for f in s.fields):raise ValueError('GET fields must use query placement')
    return s.model_dump()
