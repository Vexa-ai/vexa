"""Agent-facing connection requests/status. Human OAuth routes are never proxied here."""
import os
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from control_plane import broker_client

class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['google_email', 'google_calendar', 'custom_secret', 'github']
    label: str = Field(default='',max_length=80)
    new_account: bool = False
    setup: dict | None = None


class AccountRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str = Field(default='',pattern=r'^(|[a-f0-9]{32})$')
    action: Literal['gmail.search','gmail.read','gmail.thread','calendar.events']
    query: str = Field(default='',max_length=500)
    page_token: str = Field(default='',max_length=2048)
    message_id: str = Field(default='',max_length=128)
    time_min: str = Field(default='',max_length=40)
    time_max: str = Field(default='',max_length=40)
    limit: int = Field(default=10,ge=1,le=20)


class GmailDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id: str = Field(default='',pattern=r'^(|[a-f0-9]{32})$')
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{8,80}$')
    recipient: str = Field(min_length=3,max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1,max_length=50000)


class CustomCall(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    parameters: dict[str,str] = Field(default_factory=dict)
    body: dict | None = None


def call_broker(actor, method, path, payload=None):
    """One broker request as the agent role (credential-broker.v1), signed by the shared client.

    A refusal the person can act on (400/404/409/422) passes through with the broker's fixed
    sentence. Anything else is a typed fault, logged by broker_client, and answered 503."""
    try:
        response = broker_client.request(
            base_url=os.environ.get('VEXA_CONNECTIONS_BROKER_URL', ''),
            key_file=os.environ.get('VEXA_CONNECTIONS_AGENT_KEY_FILE', ''),
            role='agent', actor=actor, method=method, path=path, payload=payload)
        if response.status_code == 200:
            return broker_client.json_of(response, role='agent', method=method, path=path)
        if response.status_code in (400, 404, 409, 422):
            detail = broker_client.json_of(response, role='agent', method=method, path=path).get('detail')
            raise HTTPException(response.status_code, detail if isinstance(detail, str) else 'Invalid connection request')
        raise broker_client.fault('http_%d' % response.status_code, role='agent', method=method, path=path,
                                  status=response.status_code)
    except broker_client.BrokerFault as exc:
        if exc.kind == 'config':
            raise HTTPException(503, 'Connections are not configured on this deployment') from None
        raise HTTPException(503, 'Connection service unavailable') from None


class ResearchReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(max_length=300)
    paths: list[str] = Field(default_factory=list, max_length=100)
    excluded: str = Field(default='', max_length=80)
    reason: str = Field(default='', max_length=1000)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['status', 'start', 'next', 'ack']
    connection_ids: list[str] = Field(default_factory=list, max_length=30)
    batch_id: str = Field(default='', max_length=40)
    receipts: list[ResearchReceipt] = Field(default_factory=list, max_length=5)


def build(*, subject_of, wsr=None, **_):
    router=APIRouter()

    @router.post('/api/connections/request')
    def request_connection(request: Request, body: ConnectionRequest):
        """Request Gmail or Calendar consent in the trusted Minutes Connections panel.

        Never ask for credentials in chat. This creates a pending request, not a
        connection. The user must consent; call connections_status afterward.
        """
        actor=subject_of(request)
        if body.provider=='github':
            return {'connection_id':'git','provider':'github','status':'setup_available',
                    'ui_action':'open_connections','instruction':'The Connections panel opens Git repositories setup. The user enters a token privately; SSH public deploy keys remain available in repository attach. Never request a token in chat.'}
        if body.provider=='custom_secret' and not body.label.strip():
            raise HTTPException(422,'Name the service in label before requesting its connection')
        label=body.label.strip() or ({'google_email':'Gmail','google_calendar':'Google Calendar','custom_secret':'Custom secret'}[body.provider])
        existing=call_broker(actor,'GET','/api/connections')['connections']
        result=next(({'connection_id':c['id'],'status':c['status']} for c in existing
                     if not body.new_account and c['provider']==body.provider and (not body.label or c['label']==label) and c['status'] in {'ready','awaiting_user'}),None)
        if result is not None:
            result=call_broker(actor,'POST','/api/connections/'+result['connection_id']+'/request')
        if result is None:
            result=call_broker(actor,'POST','/api/setup',{'provider':body.provider,'label':label})
        if body.setup is not None:
            call_broker(actor,'POST','/api/connections/'+result['connection_id']+'/prepare',{'setup':body.setup})
        return {'connection_id':result['connection_id'], 'provider':body.provider,
                'status':'awaiting_user' if body.setup is not None else result['status'], 'ui_action':'open_connections',
                'instruction':'A setup request was delivered to Minutes; do not claim the panel is visible until the user confirms. The user must review and approve the proposed configuration. OAuth definitions show client ID/client secret and consent; API-key definitions show the secret field. An existing ready credential does not prove this new setup is approved. Do not construct links or call workspace_view for this request. Never paste credentials in chat. Call connections_status after consent; a request is not a connected account.'}

    @router.get('/api/connections')
    def connections_status(request: Request):
        """Read your connection metadata only. Ready means consent was stored, not mail/calendar sync."""
        return call_broker(subject_of(request),'GET','/api/connections')

    @router.post('/api/connections/read')
    def read_account(request: Request, body: AccountRead):
        actor=subject_of(request)
        provider='google_calendar' if body.action=='calendar.events' else 'google_email'
        rows=call_broker(actor,'GET','/api/connections')['connections']
        ready=[c for c in rows if c['provider']==provider and c['status']=='ready' and (not body.connection_id or c['id']==body.connection_id)]
        if len(ready)!=1:
            raise HTTPException(409,'Choose a ready connection_id from connections_status; multiple accounts require an explicit selection')
        return call_broker(actor,'POST','/api/connections/'+ready[0]['id']+'/read',body.model_dump(exclude={'connection_id'}))

    @router.post('/api/connections/gmail/draft')
    def draft(request: Request, body: GmailDraft):
        actor=subject_of(request)
        rows=call_broker(actor,'GET','/api/connections')['connections']
        ready=[c for c in rows if c['provider']=='google_email' and c['status']=='ready' and (not body.connection_id or c['id']==body.connection_id)]
        if len(ready)!=1:raise HTTPException(409,'Choose a ready Gmail connection_id from connections_status')
        return call_broker(actor,'POST','/api/connections/'+ready[0]['id']+'/draft',body.model_dump(exclude={'connection_id'}))

    @router.post('/api/connections/service/call')
    def service_call(request: Request, body: CustomCall):
        return call_broker(subject_of(request),'POST','/api/connections/'+body.connection_id+'/call',body.model_dump(exclude={'connection_id'}))

    @router.post('/api/onboarding/research')
    def research(request: Request, body: ResearchRequest):
        from control_plane.onboarding_research import Research, ResearchError
        actor = subject_of(request)
        if wsr is None:
            raise HTTPException(503, 'Private workspace is unavailable')
        selected = []
        if body.action == 'start':
            rows = call_broker(actor, 'GET', '/api/connections')['connections']
            ids = set(body.connection_ids)
            selected = [c for c in rows if c['id'] in ids and c['status'] == 'ready' and c['provider'] in {'google_email','google_calendar'}]
            if not ids or len(selected) != len(ids):
                raise HTTPException(409, 'Select ready email/calendar accounts owned by this user')
        def read(cid, payload):
            return call_broker(actor, 'POST', '/api/connections/'+cid+'/read', payload)
        try:
            return Research(wsr.workspace_dir(actor), read).run(body.action, connections=selected,
                batch_id=body.batch_id, receipts=[r.model_dump() for r in body.receipts])
        except ResearchError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
