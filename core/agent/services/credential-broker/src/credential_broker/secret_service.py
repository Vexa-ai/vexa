"""Human-configured HTTPS credential use. DNS is validated and pinned per request.

A refusal the person can act on (a bad endpoint, a private address, a redirect) is `ServiceError`.
A service that cannot be reached or answers unreadably is `faults.UpstreamFault` (source `service`).
"""
import base64,http.client,json,re,socket,ssl,time
from urllib.parse import urlsplit,quote
from .faults import UpstreamFault
from .ssrf import is_blocked_ip

class ServiceError(Exception):pass

USER_AGENT='vexa-credential-broker'

def validate_endpoint(endpoint):
    try:
        u=urlsplit(endpoint);port=u.port
        if u.scheme!='https' or not u.hostname or port not in (None,443) or u.username or u.password or u.fragment or u.query:raise ValueError()
        if not u.path.startswith('/') or '..' in u.path or '%' in u.path or '\\' in endpoint:raise ValueError()
        return u
    except ValueError:raise ServiceError('Use an exact public HTTPS endpoint without query, fragment, or credentials') from None

#: An authentication prefix is one HTTP token (RFC 9110 `tchar`), such as Bot, Token or SSWS: the
#: header becomes `<prefix> <credential>`. No space, colon or control character can ride in it.
PREFIX=re.compile(r"[A-Za-z0-9!#$&'*+.^_`|~-]{1,32}")
SCHEMES=('bearer','raw','telegram','prefix')

def configure(secret, endpoint, header, scheme, method, prefix=''):
    if prefix and scheme!='prefix':raise ServiceError('An authentication prefix needs scheme prefix')
    if scheme=='prefix' and not PREFIX.fullmatch(prefix or ''):raise ServiceError('Use an authentication prefix of letters, digits or dashes, such as Bot or Token')
    if not secret or len(secret)>65536:raise ServiceError('Enter a secret value')
    # Storage accepts any text. Execution requires a safe HTTP header value.
    if endpoint:
        validate_endpoint(endpoint)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',header) or header.lower() in {'host','cookie','content-length','connection','transfer-encoding','proxy-authorization'}:raise ServiceError('Use an authentication header such as Authorization or X-API-Key')
        if scheme=='telegram':
            if endpoint not in {'https://api.telegram.org/bot{secret}/sendMessage','https://api.telegram.org/bot{secret}/getMe','https://api.telegram.org/bot{secret}/getUpdates'} or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',secret):raise ServiceError('Invalid Telegram bot token or endpoint')
            if method!=('POST' if endpoint.endswith('/sendMessage') else 'GET'):raise ServiceError('Invalid Telegram method')
        if scheme not in SCHEMES or method not in ('GET','POST'):raise ServiceError('Unsupported authentication or method')
        if any(ord(c)<32 or ord(c)>126 for c in secret):raise ServiceError('HTTP authentication needs a single-line ASCII credential; omit the endpoint to store other secret types')
    out={'value':secret,'endpoint':endpoint,'header':header,'scheme':scheme,'method':method}
    if scheme=='prefix':out['prefix']=prefix
    return out

def authorization(c):
    """The credential header's value: `Bearer <v>`, `<prefix> <v>`, or the bare credential."""
    if c['scheme']=='bearer':return 'Bearer '+c['value']
    if c['scheme']=='prefix':return c['prefix']+' '+c['value']
    return c['value']

def public_addresses(host):
    try:
        ips=list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
        if not ips or any(is_blocked_ip(ip) for ip in ips):raise ServiceError('Service must resolve only to public addresses')
        return ips
    except socket.gaierror as e:
        # A name that does not exist is the configuration's to fix; a resolver that did not answer is an outage.
        if e.errno==socket.EAI_NONAME:raise ServiceError('Service host name does not resolve; check the configured endpoint') from None
        raise UpstreamFault('service','unreachable','Service address could not be resolved; retry later') from None
    except OSError:raise UpstreamFault('service','unreachable','Service address could not be resolved; retry later') from None
    except ValueError:raise ServiceError('Service address unavailable') from None

#: THE HOPS ARE ORDERED (Vexa-ai/vexa#1783, frictions 2026-10-13): one call to a connected service,
#: retry included, ends within this deadline — well inside agent-api's 25 s budget for the broker,
#: which ends inside the gateway's 30 s forward — so the broker, the hop that waited, says the
#: service was slow (`LATE`) instead of an outer hop answering a bare "upstream timeout".
SERVICE_DEADLINE_S=12.0
CONNECT_TIMEOUT_S=5.0
#: The pause before the one repeat, and the least time a repeat needs to be worth starting.
_RETRY_PAUSE_S=0.5
_MIN_ATTEMPT_S=2.0
LATE=f'Service did not answer within {int(SERVICE_DEADLINE_S)} s; do not automatically retry a POST'

def _now():return time.monotonic()
def _retry_sleep(seconds):time.sleep(seconds)

class _Unsent(Exception):
    """No connection opened: nothing reached the service, so sending again is safe for any method."""

def _bound(conn,deadline):
    left=deadline-_now()
    if left<=0:raise socket.timeout('service deadline')
    if getattr(conn,'sock',None) is not None:conn.sock.settimeout(left)

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip):super().__init__(host,443,timeout=CONNECT_TIMEOUT_S,context=ssl.create_default_context());self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,443),timeout=self.timeout)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)

def execute(config, parameters, body=None):
    c=configure(config['value'],config.get('endpoint',''),config.get('header','Authorization'),config.get('scheme','bearer'),config.get('method','GET'),config.get('prefix',''))
    if not c['endpoint']:raise ServiceError('Configure an HTTPS endpoint in the secure panel before use')
    u=validate_endpoint(c['endpoint']);ips=public_addresses(u.hostname)
    from urllib.parse import urlencode
    if len(parameters)>30 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>2000 for k,v in parameters.items()):raise ServiceError('Invalid query parameters')
    if c['method']=='GET' and body is not None:raise ServiceError('This connection allows GET only')
    for k in config.get('fixed_body',{}):
        if k in parameters:raise ServiceError('Saved body fields cannot be supplied as query parameters')
    for k in config.get('fixed_query',{}):
        if body and k in body:raise ServiceError('Saved query fields cannot be supplied in the body')
    parameters=dict(parameters)
    body=dict(body) if body is not None else None
    for dest,fixed in ((parameters,config.get('fixed_query',{})),):
        for k,v in fixed.items():
            if k in dest and dest[k]!=v:raise ServiceError('A saved connection field cannot be overridden; update it in Connections')
            dest[k]=v
    if config.get('fixed_body'):
        body=body or {}
        for k,v in config['fixed_body'].items():
            if k in body and body[k]!=v:raise ServiceError('A saved connection field cannot be overridden; update it in Connections')
            body[k]=v
    raw=json.dumps(body).encode() if body is not None else None
    if raw and len(raw)>65536:raise ServiceError('Request body too large')
    # A User-Agent first: several public APIs (GitHub among them) refuse a request without one.
    headers={'User-Agent':USER_AGENT,c['header']:authorization(c),'Accept':'application/json','Content-Type':'application/json'}
    path=u.path
    if c['scheme']=='telegram':
        headers.pop(c['header'],None)
        path=path.replace('{secret}',c['value'])
    target=path+('?' + urlencode(parameters) if parameters else '')
    deadline=_now()+SERVICE_DEADLINE_S

    def attempt():
        conn=PinnedHTTPS(u.hostname,ips[0])
        try:
            try:conn.connect()
            except OSError as e:raise _Unsent() from e
            _bound(conn,deadline)
            conn.request(c['method'],target,body=raw,headers=headers)
            _bound(conn,deadline)
            response=conn.getresponse()
            if 300<=response.status<400:raise ServiceError('Redirect refused; configure the final service endpoint')
            data=b''
            while len(data)<=262144:
                _bound(conn,deadline)
                want=min(65536,262145-len(data));chunk=response.read(want);data+=chunk
                if len(chunk)<want:break
            if len(data)>262144:raise ServiceError('Response too large')
            return response.status,data
        finally:conn.close()

    # Sent again once, only when that is safe and fits in the deadline: any method when no
    # connection opened; a GET after any other failure. A POST that may have arrived is never repeated.
    tries=0
    while True:
        try:
            status,data=attempt();break
        except _Unsent:
            failure,safe='unreachable',True
        except socket.timeout:
            failure,safe='late',c['method']=='GET'
        except OSError:
            failure,safe='unreachable',c['method']=='GET'
        except http.client.HTTPException:
            failure,safe='bad_answer',c['method']=='GET'
        if safe and tries==0 and deadline-_now()-_RETRY_PAUSE_S>=_MIN_ATTEMPT_S:
            tries+=1;_retry_sleep(_RETRY_PAUSE_S);continue
        if failure=='late':raise UpstreamFault('service','unreachable',LATE) from None
        if failure=='bad_answer':raise UpstreamFault('service','bad_answer','Service answered with an unreadable response; do not automatically retry a POST') from None
        raise UpstreamFault('service','unreachable','Service could not be reached; do not automatically retry a POST') from None
    text=data.decode('utf-8',errors='replace')
    for sensitive in (json.dumps(c['value'])[1:-1],c['value'],quote(c['value'],safe=''),base64.b64encode(c['value'].encode()).decode()):
        text=text.replace(sensitive,'[credential redacted]')
    try:content=json.loads(text)
    except ValueError:content=text
    return {'http_status':status,'content':content,'untrusted_content':True}
