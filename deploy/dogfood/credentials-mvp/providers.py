"""Provider declarations and fixed, read-only verification adapters.

Provider URLs/scopes are operator-reviewed constants, never tool arguments.
OAuth application credentials belong to the operator; user tokens belong in vault.
"""
import os
import vault_client
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

CATALOG = {
    'custom_secret': {'label':'Custom secret','method':'secret','field':'Secret value','permission':'Store privately; optionally use at a human-configured HTTPS endpoint'},
    'fixture': {'label': 'Test service', 'method': 'secret', 'field': 'Test API key', 'permission': 'Authenticate to the isolated test provider'},
    'github': {'label': 'GitHub', 'method': 'secret', 'field': 'Fine-grained personal access token', 'permission': 'Verify your GitHub account. Repository permissions are controlled on GitHub.'},
    'calendar_ics': {'label': 'Calendar feed', 'method': 'secret', 'field': 'Private ICS feed address', 'permission': 'Read calendar events from this private feed'},
    'google_calendar': {'label': 'Google Calendar', 'method': 'oauth', 'family': 'google', 'permission': 'Read calendar events', 'scopes': ['https://www.googleapis.com/auth/calendar.events.readonly']},
    'google_email': {'label': 'Gmail', 'method': 'oauth', 'family': 'google', 'permission': 'Read email and create drafts. Sending is not exposed by Minutes.', 'scopes': ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.compose']},
    'microsoft_calendar': {'label': 'Microsoft Calendar', 'method': 'oauth', 'family': 'microsoft', 'permission': 'Read calendar events', 'scopes': ['offline_access', 'https://graph.microsoft.com/Calendars.Read']},
    'microsoft_email': {'label': 'Microsoft Email', 'method': 'oauth', 'family': 'microsoft', 'permission': 'Read email; no sending', 'scopes': ['offline_access', 'https://graph.microsoft.com/Mail.Read']},
}
AUTHORIZE = {'google': 'https://accounts.google.com/o/oauth2/v2/auth', 'microsoft': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'}
TOKEN = {'google': 'https://oauth2.googleapis.com/token', 'microsoft': 'https://login.microsoftonline.com/common/oauth2/v2.0/token'}
VERIFY = {
    'github': 'https://api.github.com/user',
    'google_calendar': 'https://www.googleapis.com/calendar/v3/calendars/primary/events?maxResults=1',
    'google_email': 'https://gmail.googleapis.com/gmail/v1/users/me/profile',
    'microsoft_calendar': 'https://graph.microsoft.com/v1.0/me/events?$top=1&$select=id',
    'microsoft_email': 'https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=id',
}

class ProviderError(Exception):
    """A sanitized user-facing failure; never retain provider response bodies."""


def config(root, provider):
    family = CATALOG[provider].get('family')
    try:
        if os.environ.get('MVP_OAUTH_CONFIG')=='vault':
            data=vault_client.request(Path(root),os.environ['BAO_ADDR'],'GET','connections/data/operator-'+family)['data']['data']
        else:
            data = json.loads((Path(root) / 'oauth-clients.json').read_text())[family]
        if not data.get('client_id') or not data.get('client_secret'):
            raise ValueError()
        return {'client_id': data['client_id'], 'client_secret': data['client_secret']}
    except (OSError, ValueError, KeyError, TypeError, vault_client.VaultUnavailable):
        raise ProviderError('Provider application is not configured on this deployment') from None


def catalog(root):
    rows = []
    for key, item in CATALOG.items():
        configured = True
        if item['method'] == 'oauth':
            try: config(root, key)
            except ProviderError: configured = False
        rows.append({'id': key, **item, 'configured': configured})
    return rows


def authorize(root, provider, redirect, state, challenge):
    item = CATALOG[provider]
    c = config(root, provider)
    args = {'client_id': c['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
            'scope': ' '.join(item['scopes']), 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256'}
    if item['family'] == 'google': args.update(access_type='offline', prompt='select_account consent')
    return AUTHORIZE[item['family']] + '?' + urlencode(args)


def tokens(root, provider, *, code=None, verifier=None, redirect=None, refresh=None, client=None):
    item = CATALOG[provider]
    c = config(root, provider)
    form = {**c, 'grant_type': 'refresh_token' if refresh else 'authorization_code'}
    if refresh: form['refresh_token'] = refresh
    else: form.update(code=code, code_verifier=verifier, redirect_uri=redirect)
    try:
        if client is None:
            with httpx.Client(timeout=15, follow_redirects=False) as session:
                return tokens(root, provider, code=code, verifier=verifier, redirect=redirect, refresh=refresh, client=session)
        response = client.post(TOKEN[item['family']], data=form)
        if response.status_code != 200: raise ProviderError('Authorization failed; reconnect this account')
        result = response.json()
        access = result.get('access_token')
        if not isinstance(access, str) or not access or result.get('token_type', '').lower() != 'bearer':
            raise ProviderError('Provider did not return a usable authorization')
        # Providers may omit scope on refresh; initial consent must prove the requested grants.
        normalize = lambda s: s.removeprefix('https://graph.microsoft.com/').lower()
        required = {normalize(s) for s in item['scopes'] if s != 'offline_access'}
        granted = {normalize(s) for s in result.get('scope', '').split()}
        if not refresh and not required.issubset(granted):
            raise ProviderError('Required permission was not granted; reconnect this account')
        return {'access_token': access, 'refresh_token': result.get('refresh_token') or refresh,
                'expires_at': time.time() + max(0, int(result.get('expires_in', 0))), 'scope': result.get('scope', '')}
    except (httpx.HTTPError, ValueError, TypeError):
        raise ProviderError('Provider authorization is unavailable; try connecting again') from None


def secret(provider, value):
    value = value.strip()
    if provider == 'calendar_ics':
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ProviderError('Use a valid private HTTPS calendar feed address') from None
        # Explicit public providers only. No user-selected hosts/ports or redirects.
        if (parsed.scheme != 'https' or parsed.hostname not in {'calendar.google.com', 'outlook.office365.com', 'outlook.live.com'}
                or port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ProviderError('Use a private HTTPS feed from Google Calendar or Outlook')
        if not parsed.path.lower().endswith('.ics'):
            raise ProviderError('Use the private ICS feed address, not a calendar webpage')
    if provider == 'github' and (len(value) < 20 or any(c.isspace() for c in value)):
        raise ProviderError('Enter a valid GitHub token in the secure panel')
    return value


def verify(provider, value, operation, *, fixture_url='', client=None):
    """Read-only check. No account, message or event content crosses this boundary."""
    if client is None:
        with httpx.Client(timeout=10, follow_redirects=False) as session:
            return verify(provider, value, operation, fixture_url=fixture_url, client=session)
    try:
        if provider == 'fixture':
            response = client.post(fixture_url + '/verify', headers={'Authorization': 'Bearer ' + value, 'X-Operation-ID': operation})
            return response.status_code == 200, response.json().get('receipt', '') if response.status_code == 200 else ''
        if provider == 'calendar_ics':
            url = secret(provider, value)
            # Bound downloaded feed bytes; never persist/log the URL or event content.
            with client.stream('GET', url) as response:
                if response.status_code != 200: return False, ''
                prefix = b''
                for chunk in response.iter_bytes(chunk_size=4096):
                    prefix += chunk
                    if len(prefix) >= 4096: break
                return b'BEGIN:VCALENDAR' in prefix, ''
        access = value if provider == 'github' else value['access_token']
        with client.stream('GET', VERIFY[provider], headers={'Authorization': 'Bearer ' + access, 'Accept': 'application/json'}) as response:
            return response.status_code == 200, ''
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise ProviderError('Provider verification is unavailable') from None


# On-demand account reads; fixed hosts and methods, never a general HTTP proxy.
def read_account(provider, value, action, *, query='', message_id='', time_min='', time_max='', limit=10, page_token='', client=None):
    import re
    import base64
    from html.parser import HTMLParser
    if action not in {'gmail.search','gmail.read','gmail.thread','calendar.events'} or provider != ('google_calendar' if action=='calendar.events' else 'google_email'):
        raise ProviderError('Operation does not match this connection')
    if not 1<=limit<=20 or len(query)>500:
        raise ProviderError('Invalid read limits')
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False) as session:
            return read_account(provider,value,action,query=query,message_id=message_id,time_min=time_min,time_max=time_max,limit=limit,page_token=page_token,client=session)
    def get(url, params):
        try:
            with client.stream('GET',url,params=params,headers={'Authorization':'Bearer '+value['access_token']}) as response:
                if response.status_code!=200:
                    raise ProviderError({400:'Provider rejected the request arguments; check query, page token and date range',401:'Authorization rejected; reconnect this account',403:'Provider refused access; check granted scopes and API enablement',404:'Requested message or calendar was not found',429:'Provider rate limit reached; retry later'}.get(response.status_code,'Provider temporarily unavailable; retry later'))
                raw=b''
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>2_000_000:raise ProviderError('Provider response too large; narrow the request')
                return json.loads(raw)
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            raise ProviderError('Account read is unavailable') from None
    base='https://gmail.googleapis.com/gmail/v1/users/me/messages'
    def message(mid, full=False, data=None):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',mid):raise ProviderError('Invalid message ID')
        data=data if data is not None else get(base+'/'+mid,{'format':'full' if full else 'metadata'})
        payload=data.get('payload',{})
        headers={h['name'].lower():h.get('value','')[:4000] for h in payload.get('headers',[])}
        result={k:data.get(k) for k in ('id','threadId','internalDate','snippet')}
        result['headers']={k:headers.get(k,'') for k in ('from','to','cc','reply-to','subject','date','message-id','in-reply-to','references')}
        if full:
            plain=[];html=[]
            def parts(part,depth=0):
                if depth>12 or part.get('filename'):return
                raw=part.get('body',{}).get('data','')
                if raw and part.get('mimeType') in {'text/plain','text/html'}:
                    try:text=base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)).decode('utf-8',errors='replace')
                    except ValueError:text=''
                    (plain if part['mimeType']=='text/plain' else html).append(text)
                for child in part.get('parts',[]):parts(child,depth+1)
            parts(payload)
            class Text(HTMLParser):
                def __init__(self):super().__init__();self.text=[];self.hidden=0
                def handle_starttag(self,tag,attrs):
                    if tag in ('script','style'):self.hidden+=1
                def handle_endtag(self,tag):
                    if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
                def handle_data(self,data):
                    if not self.hidden:self.text.append(data)
            body='\n'.join(plain)
            if not plain:
                parser=Text();parser.feed('\n'.join(html));body='\n'.join(parser.text)
            result.update(body=body[:50000],truncated=len(body)>50000,attachments_included=False)
        return result
    if action=='gmail.thread':
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',message_id):raise ProviderError('Invalid thread ID')
        data=get('https://gmail.googleapis.com/gmail/v1/users/me/threads/'+message_id,{'format':'full'})
        try: offset=int(page_token or '0')
        except ValueError: raise ProviderError('Invalid thread offset') from None
        if offset<0:raise ProviderError('Invalid thread offset')
        rows=data.get('messages',[])
        return {'thread_id':data.get('id'), 'messages':[message(m['id'],True,m) for m in rows[offset:offset+limit]],
                'has_more':offset+limit<len(rows),'next_page_token':str(offset+limit) if offset+limit<len(rows) else '',
                'total_messages':len(rows)}
    if action=='gmail.read':return {'message':message(message_id,True)}
    if action=='gmail.search':
        data=get(base,{'q':query,'maxResults':limit,**({'pageToken':page_token} if page_token else {})})
        return {'messages':[message(m['id']) for m in data.get('messages',[])[:limit]],'has_more':bool(data.get('nextPageToken')),'next_page_token':data.get('nextPageToken','')}
    from datetime import datetime
    try:
        lo=datetime.fromisoformat(time_min.replace('Z','+00:00'));hi=datetime.fromisoformat(time_max.replace('Z','+00:00'))
        if not lo.tzinfo or not hi.tzinfo or not 0<(hi-lo).total_seconds()<=366*86400:raise ValueError()
    except ValueError:raise ProviderError('Use a timezone-qualified time range of at most one year') from None
    data=get('https://www.googleapis.com/calendar/v3/calendars/primary/events',{'timeMin':time_min,'timeMax':time_max,'maxResults':limit,'singleEvents':'true','orderBy':'startTime',**({'pageToken':page_token} if page_token else {})})
    return {'events':[{k:e.get(k) for k in ('id','summary','description','start','end','location','htmlLink','status','attendees','organizer','creator','recurringEventId','originalStartTime','updated','attendeesOmitted')} for e in data.get('items',[])[:limit]],'has_more':bool(data.get('nextPageToken')),'next_page_token':data.get('nextPageToken','')}


DRAFT_SCOPE='https://www.googleapis.com/auth/gmail.compose'
def create_gmail_draft(value, recipient, subject, body, *, client=None):
    from email.message import EmailMessage
    from email.policy import SMTP
    from email.utils import parseaddr
    import base64
    if DRAFT_SCOPE not in value.get('scope','').split():raise ProviderError('Draft permission required')
    if any(c in recipient+subject for c in '\r\n') or parseaddr(recipient)[1]!=recipient or '@' not in recipient:
        raise ProviderError('Use one valid recipient and a single-line subject')
    message=EmailMessage(policy=SMTP);message['To']=recipient;message['Subject']=subject;message.set_content(body)
    raw=base64.urlsafe_b64encode(message.as_bytes()).decode()
    if client is None:
        with httpx.Client(timeout=20,follow_redirects=False) as session:
            return create_gmail_draft(value,recipient,subject,body,client=session)
    try:
        response=client.post('https://gmail.googleapis.com/gmail/v1/users/me/drafts',headers={'Authorization':'Bearer '+value['access_token']},json={'message':{'raw':raw}})
        if response.status_code not in (200,201):raise ProviderError('Draft creation failed; check account permissions')
        data=response.json()
        if not data.get('id'):raise ProviderError('Draft outcome unknown; do not retry automatically')
        return {'draft_id':data['id'],'message_id':data.get('message',{}).get('id'),'status':'draft_created','sent':False}
    except (httpx.HTTPError,ValueError):raise ProviderError('Draft outcome unknown; check Gmail Drafts before retrying') from None


def refresh(root, provider, value):
    result=tokens(root,provider,refresh=value['refresh_token'])
    result['scope']=result.get('scope') or value.get('scope','')
    return result



def account_email(provider,value):
    if provider!='google_email' or 'https://www.googleapis.com/auth/gmail.readonly' not in value.get('scope','').split():return ''
    try:
        with httpx.Client(timeout=10,follow_redirects=False) as c:
            response=c.get('https://gmail.googleapis.com/gmail/v1/users/me/profile',headers={'Authorization':'Bearer '+value['access_token']})
            if response.status_code!=200:return ''
            email=response.json().get('emailAddress','')
            return email[:320] if isinstance(email,str) else ''
    except (httpx.HTTPError,ValueError,KeyError):return ''
