import re
"""Isolated credential-flow harness. No production accounts or arbitrary network access."""
from urllib.parse import urlsplit
import base64
import providers
import secret_service
import product_identity
import vault_client
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

os.umask(0o077)
ROOT = Path(os.environ.get('MVP_STATE', '/state'))
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'metadata.sqlite'
ORIGIN = os.environ.get('MVP_ORIGIN', 'http://localhost:18541')
OAUTH_REDIRECT = os.environ.get('MVP_OAUTH_REDIRECT', ORIGIN+'/oauth/callback')
_redirect = urlsplit(OAUTH_REDIRECT)
if _redirect.scheme not in {'https','http'} or _redirect.hostname != urlsplit(ORIGIN).hostname or _redirect.username or _redirect.password or _redirect.query or _redirect.fragment:
    raise RuntimeError('OAuth callback must use the same host as the trusted panel')
LOCK = threading.RLock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exc):
    # Validation errors must not echo credential-bearing input.
    return JSONResponse({'detail':'Invalid request fields'}, 422)


@app.exception_handler(providers.ProviderError)
async def provider_failure(request, exc):
    return JSONResponse({'detail':str(exc)}, 409)


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


with db() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, actor TEXT, session TEXT, role TEXT, expires REAL);
    CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, actor TEXT, session TEXT, label TEXT, status TEXT, version INTEGER DEFAULT 0, created REAL);
    CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT, session TEXT, operation_id TEXT, connection TEXT, action TEXT, outcome TEXT, vault_request_id TEXT, version INTEGER, receipt TEXT);
    CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, actor TEXT, session TEXT, connection TEXT, status TEXT, result TEXT);
    ''')

    if 'provider' not in {r[1] for r in c.execute('PRAGMA table_info(connections)')}:
        c.execute("ALTER TABLE connections ADD COLUMN provider TEXT NOT NULL DEFAULT 'fixture'")
    if 'setup_request' not in {r[1] for r in c.execute('PRAGMA table_info(connections)')}:
        c.execute("ALTER TABLE connections ADD COLUMN setup_request TEXT NOT NULL DEFAULT ''")
    if 'account' not in {r[1] for r in c.execute('PRAGMA table_info(connections)')}:
        c.execute("ALTER TABLE connections ADD COLUMN account TEXT NOT NULL DEFAULT ''")
    if 'setup_spec' not in {r[1] for r in c.execute('PRAGMA table_info(connections)')}:
        c.execute("ALTER TABLE connections ADD COLUMN setup_spec TEXT NOT NULL DEFAULT ''")
    if 'oauth_app_version' not in {r[1] for r in c.execute('PRAGMA table_info(connections)')}:
        c.execute('ALTER TABLE connections ADD COLUMN oauth_app_version INTEGER NOT NULL DEFAULT 0')
    c.execute('CREATE TABLE IF NOT EXISTS oauth_states (state_hash TEXT PRIMARY KEY, actor TEXT, session TEXT, connection TEXT, expires REAL)')


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def mint(actor, session, role):
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (digest(token), actor, session, role, time.time()+86400))
    return token


def identity(request, roles):
    product = getattr(request.state, 'product_identity', None)
    if product:
        if product['role'] not in roles:
            raise HTTPException(403, 'Human setup required')
        return product
    roles=set(roles)
    if 'human' in roles: roles.add('operator')
    token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
    cookie = request.cookies.get('mvp_session', '')
    bearer = bool(token)
    with db() as c:
        row = c.execute('SELECT * FROM sessions WHERE token_hash=?', (digest(token or cookie),)).fetchone()
    if not row or row['expires'] < time.time() or row['role'] not in roles:
        raise HTTPException(401, 'Authentication required')
    if row['role'] in {'human','operator'} and bearer:
        raise HTTPException(401, 'Human setup requires browser session')
    if request.method != 'GET' and not bearer and request.headers.get('origin') != ORIGIN:
        raise HTTPException(403, 'Origin refused')
    return dict(row)


def connection(who, cid):
    with db() as c:
        row = c.execute('SELECT * FROM connections WHERE id=? AND actor=? AND status!="deleted"', (cid, who['actor'])).fetchone()
    if not row:
        raise HTTPException(404, 'Connection not found')
    return dict(row)


def audit(who, cid, action, outcome, *, operation='', vault='', version=0, receipt=''):
    # Audit is committed BEFORE action; a failed insert prevents the action.
    with db() as c:
        c.execute('INSERT INTO audit(at,actor,session,operation_id,connection,action,outcome,vault_request_id,version,receipt) VALUES (?,?,?,?,?,?,?,?,?,?)',
                  (time.time(), who['actor'], who['session'], operation, cid, action, outcome, vault, version, receipt))


def vault(method, path, body=None):
    try:
        return vault_client.request(ROOT,os.environ['BAO_ADDR'],method,path,body)
    except vault_client.VaultUnavailable:
        raise HTTPException(503,'Credential store unavailable') from None


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Setup(Strict):
    label: str = Field(default='Connection', min_length=1, max_length=80)
    provider: str = Field(default='fixture', pattern=r'^(custom_secret|fixture|github|calendar_ics|google_calendar|google_email|microsoft_calendar|microsoft_email)$')


class Submit(Strict):
    value: str = Field(min_length=1, max_length=8192)


class Action(Strict):
    operation_id: str = Field(pattern=r'^[a-zA-Z0-9-]{8,80}$')
    action: str = Field(default='fixture.verify', pattern=r'^(fixture\.verify|connection\.verify)$')


@app.middleware('http')
async def boundaries(request, call_next):
    try:
        request.state.product_identity = product_identity.verify(request, await request.body(), db)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail}, exc.status_code)
    # Do not permit hostile Host values on this loopback-only harness.
    callback_host = request.method=='GET' and request.url.path==_redirect.path and request.headers.get('host')==_redirect.netloc
    if not request.state.product_identity and request.headers.get('host') != urlsplit(ORIGIN).netloc and not callback_host:
        return JSONResponse({'detail':'Host refused'},403)
    response = await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get('/')
def index():
    return FileResponse(Path(__file__).with_name('index.html'))


@app.get('/app.js')
def javascript():
    return FileResponse(Path(__file__).with_name('app.js'), media_type='application/javascript')


@app.get('/style.css')
def stylesheet():
    return FileResponse(Path(__file__).with_name('style.css'), media_type='text/css')


@app.post('/login')
def login(request: Request, body: Submit):
    if request.headers.get('origin') != ORIGIN:
        raise HTTPException(403, 'Origin refused')
    with LOCK, db() as c:
        row=c.execute("SELECT * FROM sessions WHERE token_hash=? AND role IN ('bootstrap','operator_bootstrap')", (digest(body.value),)).fetchone()
        if not row or row['expires']<time.time():
            raise HTTPException(401,'Login link expired')
        c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(body.value),))
    token=mint(row['actor'],row['session'],'operator' if row['role']=='operator_bootstrap' else 'human')
    response=JSONResponse({'ok':True})
    response.set_cookie('mvp_session',token,httponly=True,samesite='lax',max_age=86400,secure=ORIGIN.startswith('https://'))
    return response


@app.get('/api/state')
def state(request: Request):
    who=identity(request,{'human'})
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM connections WHERE actor=? AND status!="deleted" ORDER BY created DESC',(who['actor'],))]
        events=[dict(r) for r in c.execute('SELECT * FROM audit WHERE actor=? ORDER BY seq DESC LIMIT 80',(who['actor'],))]
    return {'actor':who['actor'],'session':who['session'],'connections':rows,'audit':events,'test_only':True,'providers':providers.catalog(ROOT),'operator':who['role']=='operator'}


@app.post('/api/setup')
def setup(request: Request, body: Setup):
    who=identity(request,{'agent','human'})
    cid=uuid.uuid4().hex
    audit(who,cid,'setup.request','requested')
    with db() as c:
        c.execute('INSERT INTO connections(id,actor,session,label,status,created,provider,setup_request) VALUES (?,?,?,?,?,?,?,?)',
                  (cid,who['actor'],who['session'],body.label,'awaiting_user',time.time(),body.provider,uuid.uuid4().hex))
    return {'connection_id':cid,'status':'awaiting_user','setup_url':ORIGIN+'/#connection='+cid}


@app.post('/api/connections/{cid}/request')
def request_setup(cid: str, request: Request):
    who=identity(request,{'human','agent'})
    row=connection(who,cid)
    if row['status'] in {'ready','awaiting_user'}:
        audit(who,cid,'setup.request','requested')
        with db() as c:
            c.execute('UPDATE connections SET setup_request=? WHERE id=? AND actor=? AND status!="deleted"',
                      (uuid.uuid4().hex,cid,who['actor']))
    return {'connection_id':cid,'status':row['status']}


@app.post('/api/connections/{cid}/secret')
def submit(cid: str, request: Request, body: Submit):
    who=identity(request,{'human'})
    row=connection(who,cid)
    if row['provider']=='custom_secret':raise HTTPException(409,'Use the custom secret form')
    if providers.CATALOG[row['provider']]['method'] != 'secret':
        raise HTTPException(409,'Use provider sign-in for this connection')
    value=providers.secret(row['provider'],body.value)
    with LOCK:
        current=connection(who,cid)
        if current['setup_spec'] and (body.setup_request!=current['setup_request'] or current['setup_spec']!=row['setup_spec']):raise HTTPException(409,'Setup changed; review the updated form and save again')
        audit(who,cid,'credential.store','requested')
        result=vault('POST','connections/data/'+cid,{'data':{'value':value}})
        version=result['data']['version']
        audit(who,cid,'credential.store','stored',vault=result.get('request_id',''),version=version)
        with db() as c:
            c.execute('UPDATE connections SET status=?,version=? WHERE id=?',('ready',version,cid))
    return {'connection_id':cid,'status':'ready','version':version}


@app.get('/api/connections/{cid}')
def status(cid: str, request: Request):
    who=identity(request,{'human','agent'})
    row=connection(who,cid)
    return {k:row[k] for k in ('id','label','status','version','provider')}


@app.post('/api/connections/{cid}/execute')
def execute(cid: str, request: Request, body: Action):
    who=identity(request,{'human','agent'})
    with LOCK:
        row=connection(who,cid)
        if row['status']!='ready':
            raise HTTPException(409,'Connection is not ready')
        with db() as c:
            old=c.execute('SELECT * FROM operations WHERE id=?',(body.operation_id,)).fetchone()
            if old:
                if old['actor']!=who['actor'] or old['session']!=who['session'] or old['connection']!=cid:
                    raise HTTPException(409,'Operation ID already used')
                if old['status']!='complete':raise HTTPException(409,'Operation outcome unknown; do not replay automatically')
                return json.loads(old['result'])
            c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)',(body.operation_id,who['actor'],who['session'],cid,'pending',''))
        audit(who,cid,body.action,'requested',operation=body.operation_id,version=row['version'])
        result=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
        audit(who,cid,'credential.read','retrieved',operation=body.operation_id,vault=result.get('request_id',''),version=row['version'])
        value=result['data']['data']['value']
        try:
            if providers.CATALOG[row['provider']]['method']=='oauth' and value['expires_at']<=time.time()+30:
                if not value.get('refresh_token'):
                    raise providers.ProviderError('Authorization expired; reconnect this account')
                value=providers.refresh(ROOT,row['provider'],value)
                refreshed=vault('POST','connections/data/'+cid,{'data':{'value':value}})
                row['version']=refreshed['data']['version']
                audit(who,cid,'credential.refresh','stored',operation=body.operation_id,vault=refreshed.get('request_id',''),version=row['version'])
                with db() as c:
                    c.execute('UPDATE connections SET version=? WHERE id=?',(row['version'],cid))
            accepted,receipt=providers.verify(row['provider'],value,body.operation_id,fixture_url=os.environ.get('FIXTURE_URL',''))
            outcome='success' if accepted else 'refused'
        except providers.ProviderError:
            outcome='unknown';receipt=''
        output={'connection_id':cid,'operation_id':body.operation_id,'outcome':outcome,'provider_receipt':receipt}
        audit(who,cid,body.action,outcome,operation=body.operation_id,version=row['version'],receipt=receipt)
        with db() as c:
            c.execute('UPDATE operations SET status=?,result=? WHERE id=?',('complete',json.dumps(output),body.operation_id))
        return output


# Authorization starts only from the trusted human panel. The agent gets no URL
# carrying state/code/token and cannot complete or submit the human callback.
def pkce(state):
    key=(ROOT/'broker.token').read_bytes()
    verifier=base64.urlsafe_b64encode(hmac.new(key,state.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier,challenge


@app.post('/api/connections/{cid}/authorize')
def begin_authorize(cid: str, request: Request):
    who=identity(request,{'human'})
    row=connection(who,cid)
    custom_oauth=row['provider']=='custom_secret' and bool(json.loads(row['setup_spec'] or '{}').get('oauth'))
    if not custom_oauth and providers.CATALOG[row['provider']]['method']!='oauth':
        raise HTTPException(409,'This connection uses the secure input panel')
    state=('vxc_' if who.get('product') else '')+secrets.token_urlsafe(32)
    redirect=product_redirect() if who.get('product') else OAUTH_REDIRECT
    _,challenge=pkce(state)
    if custom_oauth:
        if not row['oauth_app_version']:raise HTTPException(409,'Save the OAuth application in the secure form first')
        cfg=load_oauth_application(row)
        try:url=service_oauth.authorize(cfg['spec'],cfg,redirect,state,challenge)
        except secret_service.ServiceError as e:raise HTTPException(409,str(e)) from None
    else:url=providers.authorize(ROOT,row['provider'],redirect,state,challenge)
    with db() as c:
        c.execute('DELETE FROM oauth_states WHERE connection=? OR expires<?',(cid,time.time()))
        c.execute('INSERT INTO oauth_states VALUES (?,?,?,?,?)',(digest(state),who['actor'],who['session'],cid,time.time()+600))
    audit(who,cid,'oauth.authorize','requested')
    return {'authorize_url':url}


@app.get('/oauth/callback')
@app.get('/api/auth/callback/google')
def oauth_callback(request: Request, state: str='', code: str='', error: str=''):
    who=identity(request,{'human'})
    with LOCK, db() as c:
        pending=c.execute('SELECT * FROM oauth_states WHERE state_hash=?',(digest(state),)).fetchone()
        if not pending or pending['actor']!=who['actor'] or pending['session']!=who['session'] or pending['expires']<time.time():
            raise HTTPException(403,'Authorization expired or does not belong to this session')
        c.execute('DELETE FROM oauth_states WHERE state_hash=?',(digest(state),))
    cid=pending['connection']
    with LOCK:
        row=connection(who,cid)
        if error or not code:
            audit(who,cid,'oauth.authorize','refused')
            return callback_result(who,cid,'refused')
        try:
            verifier,_=pkce(state)
            redirect=product_redirect() if who.get('product') else OAUTH_REDIRECT
            if row['provider']=='custom_secret':
                cfg=load_oauth_application(row)
                value=service_oauth.exchange(cfg['spec'],cfg,code=code,verifier=verifier,redirect=redirect)
                value['oauth_application']=cfg
            else:value=providers.tokens(ROOT,row['provider'],code=code,verifier=verifier,redirect=redirect)
            result=vault('POST','connections/data/'+cid,{'data':{'value':value}})
            version=result['data']['version']
            account=providers.account_email(row['provider'],value)
            audit(who,cid,'oauth.authorize','stored',vault=result.get('request_id',''),version=version)
            with db() as c:
                c.execute('UPDATE connections SET status=?,version=?,account=? WHERE id=?',('ready',version,account,cid))
        except (providers.ProviderError,HTTPException,secret_service.ServiceError):
            audit(who,cid,'oauth.authorize','refused')
            return callback_result(who,cid,'refused')
    return callback_result(who,cid,'connected')


@app.post('/api/connections/{cid}/disconnect')
def disconnect(cid: str, request: Request):
    who=identity(request,{'human'})
    with LOCK:
        connection(who,cid)
        audit(who,cid,'connection.disconnect','requested')
        with db() as c:
            c.execute('UPDATE connections SET status=? WHERE id=?',('disconnected',cid))
            c.execute('DELETE FROM oauth_states WHERE connection=?',(cid,))
        # Disable broker use immediately. Encrypted vault history is retained until
        # the operator's retention process destroys it. No provider revoke implied.
        audit(who,cid,'connection.disconnect','disabled')
    return {'connection_id':cid,'status':'disconnected'}


@app.post('/api/connections/{cid}/delete')
def delete_connection(cid: str, request: Request):
    who=identity(request,{'human'})
    with LOCK:
        connection(who,cid)
        audit(who,cid,'connection.delete','requested')
        with db() as c:
            c.execute('UPDATE connections SET status=? WHERE id=?',('deleted',cid))
            c.execute('DELETE FROM oauth_states WHERE connection=?',(cid,))
        # Retain audit and encrypted history under the same retention policy as disconnect.
        audit(who,cid,'connection.delete','disabled_and_removed')
    return {'connection_id':cid,'status':'deleted'}


class GoogleApplication(Strict):
    client_id: str = Field(min_length=10,max_length=250,pattern=r'^[a-zA-Z0-9-]+\.apps\.googleusercontent\.com$')
    client_secret: str = Field(min_length=10,max_length=4096)


@app.post('/api/operator/google')
def configure_google(request: Request, body: GoogleApplication):
    who=identity(request,{'operator'})
    audit(who,'operator-google','provider.configure','requested')
    saved=vault('POST','connections/data/operator-google',{'data':{'client_id':body.client_id,'client_secret':body.client_secret}})
    audit(who,'operator-google','provider.configure','stored',vault=saved.get('request_id',''),version=saved['data']['version'])
    return {'configured':True}


def product_redirect():
    value=os.environ.get('VEXA_CONNECTIONS_PRODUCT_REDIRECT','')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path!='/api/auth/callback/google' or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HTTPException(503,'Product OAuth callback is not configured')
    return value


def callback_result(who,cid,status):
    if who.get('product'):
        return JSONResponse({'connection_id':cid,'status':status})
    return RedirectResponse(ORIGIN+'/#connection='+cid+'&authorization='+status,303)


@app.get('/api/connections')
def connection_status(request: Request):
    who=identity(request,{'human','agent'})
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT id,provider,label,status,created,setup_request,account,setup_spec,oauth_app_version FROM connections WHERE actor=? AND status!="deleted" ORDER BY created DESC',(who['actor'],))]
    for row in rows:
        row['setup']=json.loads(row.pop('setup_spec') or '{}')
        row['application_configured']=bool(row.pop('oauth_app_version'))
    return {'connections':rows}


class AccountRead(Strict):
    action: str = Field(pattern=r'^(gmail\.search|gmail\.read|gmail\.thread|calendar\.events)$')
    query: str = Field(default='',max_length=500)
    page_token: str = Field(default='',max_length=2048)
    message_id: str = Field(default='',max_length=128)
    time_min: str = Field(default='',max_length=40)
    time_max: str = Field(default='',max_length=40)
    limit: int = Field(default=10,ge=1,le=20)

@app.post('/api/connections/{cid}/read')
def account_read(cid: str, request: Request, body: AccountRead):
    who=identity(request,{'agent','human'})
    operation=uuid.uuid4().hex
    with LOCK:
        row=connection(who,cid)
        expected='google_calendar' if body.action=='calendar.events' else 'google_email'
        if row['provider']!=expected or row['status']!='ready':raise HTTPException(409,'Matching connection is not ready')
        audit(who,cid,body.action,'requested',operation=operation,version=row['version'])
        try:
            stored=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
            audit(who,cid,'credential.read','retrieved',operation=operation,vault=stored.get('request_id',''),version=row['version'])
            value=stored['data']['data']['value']
            if value['expires_at']<=time.time()+30:
                if not value.get('refresh_token'):raise providers.ProviderError('Authorization expired; reconnect')
                value=providers.refresh(ROOT,row['provider'],value)
                saved=vault('POST','connections/data/'+cid,{'data':{'value':value}})
                row['version']=saved['data']['version']
                with db() as c:c.execute('UPDATE connections SET version=? WHERE id=?',(row['version'],cid))
                audit(who,cid,'credential.refresh','stored',operation=operation,vault=saved.get('request_id',''),version=row['version'])
        except (providers.ProviderError,HTTPException):
            audit(who,cid,body.action,'failed',operation=operation,version=row['version'])
            raise
    # Token rotation is serialized; independent account reads must not hold the global lock.
    try:
        result=providers.read_account(row['provider'],value,**body.model_dump())
        audit(who,cid,body.action,'success',operation=operation,version=row['version'])
        return {'operation_id':operation,'source':row['provider'],'untrusted_content':True,**result}
    except (providers.ProviderError,HTTPException):
        audit(who,cid,body.action,'failed',operation=operation,version=row['version'])
        raise



class GmailDraft(Strict):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{8,80}$')
    recipient: str = Field(min_length=3,max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1,max_length=50000)

with db() as c:
    c.execute('CREATE TABLE IF NOT EXISTS draft_requests (id TEXT PRIMARY KEY, actor TEXT, connection TEXT, fingerprint TEXT, result TEXT)')

@app.post('/api/connections/{cid}/draft')
def gmail_draft(cid: str, request: Request, body: GmailDraft):
    who=identity(request,{'agent','human'})
    with LOCK:
        row=connection(who,cid)
        if row['provider']!='google_email' or row['status']!='ready':raise HTTPException(409,'Gmail connection is not ready')
        # Keyed digest binds a retry to exact input without storing email text.
        fingerprint=hmac.new((ROOT/'broker.token').read_bytes(),json.dumps(body.model_dump(),sort_keys=True).encode(),hashlib.sha256).hexdigest()
        with db() as c:
            prior=c.execute('SELECT * FROM draft_requests WHERE id=?',(body.request_id,)).fetchone()
        if prior:
            if prior['actor']!=who['actor'] or prior['connection']!=cid or prior['fingerprint']!=fingerprint:raise HTTPException(409,'Request ID already used')
            if not prior['result']:raise HTTPException(409,'Draft outcome unknown; check Gmail before retrying')
            return json.loads(prior['result'])
        audit(who,cid,'gmail.draft','requested',operation=body.request_id)
        stored=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
        audit(who,cid,'credential.read','retrieved',operation=body.request_id,vault=stored.get('request_id',''),version=row['version'])
        value=stored['data']['data']['value']
        if providers.DRAFT_SCOPE not in value.get('scope','').split():
            with db() as c:c.execute('UPDATE connections SET setup_request=? WHERE id=?',(secrets.token_urlsafe(18),cid))
            audit(who,cid,'gmail.draft','permission_required',operation=body.request_id)
            return {'status':'permission_required','ui_action':'open_connections','instruction':'Use Enable drafts in Connections and approve the additional Google permission. No draft has been saved. Do not construct a link.'}
        if value['expires_at']<=time.time()+30:
            if not value.get('refresh_token'):raise providers.ProviderError('Authorization expired; reconnect')
            value=providers.refresh(ROOT,row['provider'],value)
            saved=vault('POST','connections/data/'+cid,{'data':{'value':value}})
            row['version']=saved['data']['version']
            with db() as c:c.execute('UPDATE connections SET version=? WHERE id=?',(row['version'],cid))
            audit(who,cid,'credential.refresh','stored',operation=body.request_id,vault=saved.get('request_id',''),version=row['version'])
        with db() as c:c.execute('INSERT INTO draft_requests VALUES (?,?,?,?,?)',(body.request_id,who['actor'],cid,fingerprint,''))
        try:
            result=providers.create_gmail_draft(value,body.recipient,body.subject,body.body)
        except providers.ProviderError:
            audit(who,cid,'gmail.draft','unknown',operation=body.request_id);raise
        with db() as c:c.execute('UPDATE draft_requests SET result=? WHERE id=?',(json.dumps(result),body.request_id))
        audit(who,cid,'gmail.draft','success',operation=body.request_id,version=row['version'])
        return result


class CustomSecret(Strict):
    setup_request: str = ''
    fields: dict[str,str] = Field(default_factory=dict)
    value: str = Field(default='',max_length=65536)
    endpoint: str = Field(default='',max_length=2000)
    header: str = Field(default='Authorization',max_length=64)
    scheme: str = Field(default='bearer',pattern=r'^(bearer|raw)$')
    method: str = Field(default='GET',pattern=r'^(GET|POST)$')

@app.post('/api/connections/{cid}/custom-secret')
def save_custom(cid: str, request: Request, body: CustomSecret):
    who=identity(request,{'human'});row=connection(who,cid)
    if row['provider']!='custom_secret':raise HTTPException(409,'Wrong connection type')
    spec=json.loads(row['setup_spec'] or '{}')
    if spec.get('oauth'):raise HTTPException(409,'Use the secure OAuth application form')
    value=body.value
    if not value and row['status']=='ready':
        stored=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
        audit(who,cid,'credential.read','retrieved',vault=stored.get('request_id',''),version=row['version'])
        saved_config=stored['data']['data']['value']
        proposed=spec or {'endpoint':body.endpoint,'header':body.header,'scheme':body.scheme,'method':body.method}
        if any(saved_config.get(k)!=proposed.get(k) for k in ('endpoint','header','scheme','method')):
            raise HTTPException(409,'Saved credentials cannot be reused for a different service configuration. Create a separate connection.')
        value=saved_config['value']
    try:
        if spec:
            import connection_setup
            spec=connection_setup.validate(spec)
            required={f['name'] for f in spec['fields']}
            if set(body.fields)!=required or any(not v.strip() or len(v)>2000 for v in body.fields.values()):raise secret_service.ServiceError('Fill all requested connection fields')
            config=secret_service.configure(value,spec['endpoint'],spec['header'],spec['scheme'],spec['method'])
            config['fixed_query']={f['name']:body.fields[f['name']] for f in spec['fields'] if f['location']=='query'}
            config['fixed_body']={f['name']:body.fields[f['name']] for f in spec['fields'] if f['location']=='body'}
            if spec['scheme']=='telegram' and 'chat_id' in body.fields and not re.fullmatch(r'-?[0-9]+|@[A-Za-z0-9_]{5,}',body.fields['chat_id']):raise secret_service.ServiceError('Enter the Telegram chat ID or channel username; do not use an email address')
        else:config=secret_service.configure(value,body.endpoint,body.header,body.scheme,body.method)
    except secret_service.ServiceError as e:raise HTTPException(400,str(e)) from None
    with LOCK:
        current=connection(who,cid)
        if current['setup_spec'] and (body.setup_request!=current['setup_request'] or current['setup_spec']!=row['setup_spec']):raise HTTPException(409,'Setup changed; review the updated form and save again')
        audit(who,cid,'credential.store','requested')
        saved=vault('POST','connections/data/'+cid,{'data':{'value':config}})
        version=saved['data']['version']
        audit(who,cid,'credential.store','stored',vault=saved.get('request_id',''),version=version)
        with db() as c:c.execute('UPDATE connections SET status=?,version=? WHERE id=?',('ready',version,cid))
    return {'connection_id':cid,'status':'ready'}

class CustomCall(Strict):
    parameters: dict[str,str] = Field(default_factory=dict)
    body: dict | None = None

@app.post('/api/connections/{cid}/call')
def call_custom(cid: str, request: Request, body: CustomCall):
    who=identity(request,{'agent','human'});row=connection(who,cid)
    if row['provider']!='custom_secret' or row['status']!='ready':raise HTTPException(409,'Service connection is not ready')
    operation=uuid.uuid4().hex
    audit(who,cid,'service.call','requested',operation=operation,version=row['version'])
    stored=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
    audit(who,cid,'credential.read','retrieved',operation=operation,vault=stored.get('request_id',''),version=row['version'])
    try:
        value=stored['data']['data']['value']
        if value.get('oauth_application'):
            # Single-use refresh tokens require a fresh read under the shared lock.
            with LOCK:
                row=connection(who,cid)
                if row['status']!='ready':raise HTTPException(409,'Service connection is not ready')
                stored=vault('GET','connections/data/'+cid+'?version='+str(row['version']))
                value=stored['data']['data']['value'];cfg=value['oauth_application']
                if value.get('expires_at') is not None and value['expires_at']<=time.time()+30:
                    if not value.get('refresh_token'):raise secret_service.ServiceError('Authorization expired; reconnect')
                    value=service_oauth.exchange(cfg['spec'],cfg,refresh=value['refresh_token']);value['oauth_application']=cfg
                    saved=vault('POST','connections/data/'+cid,{'data':{'value':value}})
                    with db() as c:c.execute('UPDATE connections SET version=? WHERE id=?',(saved['data']['version'],cid))
                    audit(who,cid,'credential.refresh','stored',operation=operation,vault=saved.get('request_id',''),version=saved['data']['version'])
                spec=cfg['spec']
                config=secret_service.configure(value['access_token'],spec['endpoint'],spec['header'],spec['scheme'],spec['method'])
                result=secret_service.execute(config,body.parameters,body.body)
        else:result=secret_service.execute(value,body.parameters,body.body)
    except secret_service.ServiceError as e:
        audit(who,cid,'service.call','failed',operation=operation);raise HTTPException(409,str(e)) from None
    audit(who,cid,'service.call','complete',operation=operation)
    return {'operation_id':operation,**result}

import git_storage
git_storage.register(app,root=ROOT,identity=identity,audit=audit,lock=LOCK)

class PreparedSetup(Strict):
    setup: dict

@app.post('/api/connections/{cid}/prepare')
def prepare_connection(cid:str,request:Request,body:PreparedSetup):
    who=identity(request,{'agent','human'});row=connection(who,cid)
    if row['provider']!='custom_secret':raise HTTPException(409,'Only custom service setup can be prepared')
    previous=json.loads(row['setup_spec'] or '{}')
    if previous.get('oauth') and not body.setup.get('oauth'):
        raise HTTPException(409,'This connection uses OAuth. Preserve its OAuth definition; create a separate connection for an API token.')
    import connection_setup
    try:spec=connection_setup.validate(body.setup)
    except (ValueError,secret_service.ServiceError):raise HTTPException(422,'Invalid setup specification; supply a public HTTPS endpoint, supported authentication, and required fields') from None
    with LOCK:
        current=connection(who,cid);previous=json.loads(current['setup_spec'] or '{}')
        if previous.get('oauth') and not spec.get('oauth'):
            raise HTTPException(409,'This connection uses OAuth. Preserve its OAuth definition.')
        if previous==spec:return {'connection_id':cid,'status':current['status'],'setup':spec}
        with db() as c:
            c.execute('UPDATE connections SET setup_spec=?,setup_request=?,oauth_app_version=0 WHERE id=?',(json.dumps(spec),uuid.uuid4().hex,cid))
            c.execute('DELETE FROM oauth_states WHERE connection=?',(cid,))
    return {'connection_id':cid,'status':row['status'],'setup':spec}


import service_oauth

def load_oauth_application(row):
    if not row['oauth_app_version']:raise HTTPException(409,'Save the OAuth application first')
    return vault('GET','connections/data/oauth-app-'+row['id']+'?version='+str(row['oauth_app_version']))['data']['data']['value']

class OAuthApplication(Strict):
    client_id:str=Field(min_length=1,max_length=200)
    client_secret:str=Field(min_length=1,max_length=2000)
    setup_request:str

@app.post('/api/connections/{cid}/oauth-application')
def save_oauth_application(cid:str,request:Request,body:OAuthApplication):
    who=identity(request,{'human'})
    with LOCK:
        row=connection(who,cid);spec=json.loads(row['setup_spec'] or '{}')
        if row['provider']!='custom_secret' or not spec.get('oauth'):raise HTTPException(409,'Prepare an OAuth connection first')
        if row['setup_request']!=body.setup_request:raise HTTPException(409,'Setup changed; review the updated form')
        import connection_setup
        try:spec=connection_setup.validate(spec)
        except (ValueError,secret_service.ServiceError):raise HTTPException(422,'Invalid OAuth definition') from None
        audit(who,cid,'oauth.application','requested')
        result=vault('POST','connections/data/oauth-app-'+cid,{'data':{'value':{'client_id':body.client_id,'client_secret':body.client_secret,'spec':spec}}})
        audit(who,cid,'oauth.application','stored',vault=result.get('request_id',''),version=result['data']['version'])
        with db() as c:
            c.execute("UPDATE connections SET oauth_app_version=?,status='awaiting_user' WHERE id=?",(result['data']['version'],cid))
            c.execute('DELETE FROM oauth_states WHERE connection=?',(cid,))
    return {'connection_id':cid,'status':'awaiting_user'}
