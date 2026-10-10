"""Declarative setup proposals. Only human submission activates their configuration."""
from . import secret_service
# The proposal's SHAPE is `setup_schema` (vendored to agent-api, which publishes it on the agent's
# tool); what a proposal MEANS is checked here.
from .setup_schema import InputField, OAuthSpec, SetupSpec  # noqa: F401 — re-exported

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
