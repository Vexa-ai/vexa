"""Declarative OAuth authorization-code adapter; no provider-specific routes."""
import base64, http.client, json, time
from urllib.parse import urlencode, quote
from . import secret_service as transport
from .faults import UpstreamFault


def exchange(spec, app, *, code=None, verifier=None, redirect=None, refresh=None):
    oauth=spec['oauth'];u=transport.validate_endpoint(oauth['token_url'])
    ips=transport.public_addresses(u.hostname)
    form={'grant_type':'refresh_token' if refresh else 'authorization_code'}
    if refresh:form['refresh_token']=refresh
    else:form.update(code=code,code_verifier=verifier,redirect_uri=redirect)
    headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','User-Agent':transport.USER_AGENT}
    if oauth.get('token_auth','client_secret_post')=='client_secret_basic':
        pair=quote(app['client_id'],safe='')+':'+quote(app['client_secret'],safe='')
        headers['Authorization']='Basic '+base64.b64encode(pair.encode()).decode()
    else:form.update(client_id=app['client_id'],client_secret=app['client_secret'])
    conn=transport.PinnedHTTPS(u.hostname,ips[0])
    try:
        conn.request('POST',u.path,body=urlencode(form).encode(),headers=headers)
        response=conn.getresponse()
        if response.status==429 or response.status>=500:
            raise UpstreamFault('service','rate_limited' if response.status==429 else 'bad_answer','OAuth token endpoint is unavailable; try again later')
        if response.status!=200:raise transport.ServiceError('OAuth exchange failed; check application credentials, registered redirect and consent')
        raw=response.read(65537)
        if len(raw)>65536:raise transport.ServiceError('OAuth response too large')
        result=json.loads(raw)
        if not isinstance(result.get('access_token'),str) or not result['access_token'] or result.get('token_type','').lower()!='bearer':raise ValueError()
        if result.get('scope') is not None and not set(oauth['scopes']).issubset(set(result['scope'].split())):
            raise transport.ServiceError('Required permissions were not granted; reconnect')
        # Some providers omit scope from the token response. Do not invent grants.
        return {'access_token':result['access_token'],'refresh_token':result.get('refresh_token') or refresh,
                'expires_at':time.time()+max(0,int(result['expires_in'])) if 'expires_in' in result else None,
                'granted_scope':result.get('scope')}
    except OSError:
        raise UpstreamFault('service','unreachable','OAuth token endpoint could not be reached; try again later') from None
    except (http.client.HTTPException,ValueError,TypeError,KeyError):
        raise UpstreamFault('service','bad_answer','OAuth token endpoint answered with an unusable response; try again later') from None
    finally:conn.close()


def authorize(spec, app, redirect, state, challenge):
    oauth=spec['oauth'];u=transport.validate_endpoint(oauth['authorization_url'])
    transport.public_addresses(u.hostname)
    return oauth['authorization_url']+'?'+urlencode({'response_type':'code','client_id':app['client_id'],
        'redirect_uri':redirect,'scope':' '.join(oauth['scopes']),'state':state,
        'code_challenge':challenge,'code_challenge_method':'S256'})
