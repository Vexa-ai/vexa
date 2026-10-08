"""Human-configured HTTPS credential use. DNS is validated and pinned per request."""
import base64,http.client,ipaddress,json,re,socket,ssl
from urllib.parse import urlsplit,quote

class ServiceError(Exception):pass

def validate_endpoint(endpoint):
    try:
        u=urlsplit(endpoint);port=u.port
        if u.scheme!='https' or not u.hostname or port not in (None,443) or u.username or u.password or u.fragment or u.query:raise ValueError()
        if not u.path.startswith('/') or '..' in u.path or '%' in u.path or '\\' in endpoint:raise ValueError()
        return u
    except ValueError:raise ServiceError('Use an exact public HTTPS endpoint without query, fragment, or credentials') from None

def configure(secret, endpoint, header, scheme, method):
    if not secret or len(secret)>65536:raise ServiceError('Enter a secret value')
    # Storage accepts any text. Execution requires a safe HTTP header value.
    if endpoint:
        validate_endpoint(endpoint)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',header) or header.lower() in {'host','cookie','content-length','connection','transfer-encoding','proxy-authorization'}:raise ServiceError('Use an authentication header such as Authorization or X-API-Key')
        if scheme=='telegram':
            if endpoint not in {'https://api.telegram.org/bot{secret}/sendMessage','https://api.telegram.org/bot{secret}/getMe','https://api.telegram.org/bot{secret}/getUpdates'} or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',secret):raise ServiceError('Invalid Telegram bot token or endpoint')
            if method!=('POST' if endpoint.endswith('/sendMessage') else 'GET'):raise ServiceError('Invalid Telegram method')
        if scheme not in ('bearer','raw','telegram') or method not in ('GET','POST'):raise ServiceError('Unsupported authentication or method')
        if any(ord(c)<32 or ord(c)>126 for c in secret):raise ServiceError('HTTP authentication needs a single-line ASCII credential; omit the endpoint to store other secret types')
    return {'value':secret,'endpoint':endpoint,'header':header,'scheme':scheme,'method':method}

def public_addresses(host):
    try:
        ips=list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
        if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast or ipaddress.ip_address(ip).is_reserved for ip in ips):raise ServiceError('Service must resolve only to public addresses')
        return ips
    except (OSError,ValueError):raise ServiceError('Service address unavailable') from None

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip):super().__init__(host,443,timeout=15,context=ssl.create_default_context());self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,443),timeout=self.timeout)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)

def execute(config, parameters, body=None):
    c=configure(config['value'],config.get('endpoint',''),config.get('header','Authorization'),config.get('scheme','bearer'),config.get('method','GET'))
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
    headers={c['header']:('Bearer ' if c['scheme']=='bearer' else '')+c['value'],'Accept':'application/json','Content-Type':'application/json'}
    path=u.path
    if c['scheme']=='telegram':
        headers.pop(c['header'],None)
        path=path.replace('{secret}',c['value'])
    conn=PinnedHTTPS(u.hostname,ips[0])
    try:
        conn.request(c['method'],path+('?' + urlencode(parameters) if parameters else ''),body=raw,headers=headers)
        response=conn.getresponse()
        if 300<=response.status<400:raise ServiceError('Redirect refused; configure the final service endpoint')
        data=response.read(262145)
        if len(data)>262144:raise ServiceError('Response too large')
        text=data.decode('utf-8',errors='replace')
        for sensitive in (json.dumps(c['value'])[1:-1],c['value'],quote(c['value'],safe=''),base64.b64encode(c['value'].encode()).decode()):
            text=text.replace(sensitive,'[credential redacted]')
        try:content=json.loads(text)
        except ValueError:content=text
        return {'http_status':response.status,'content':content,'untrusted_content':True}
    except (OSError,http.client.HTTPException):raise ServiceError('Service request failed; do not automatically retry a POST') from None
    finally:conn.close()
