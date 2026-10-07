"""Private product ingress: separate signing keys constrain agent and human authority.

Assertions cover method, exact path/query and body, expire after 30 seconds, and
are single-use. Never mount the terminal key into an agent worker or MCP process.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from fastapi import HTTPException


def verify(request, body, db):
    assertion = request.headers.get('x-vexa-assertion', '')
    if not assertion:
        return None
    try:
        encoded, signature = assertion.split('.')
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if set(claims) != {'role','actor','session','at','nonce','method','path','body'} or claims['method'] not in {'GET','POST'} or not isinstance(claims['path'],str) or not claims['path'].startswith('/api/') or len(claims['path']) > 16384:
            raise ValueError()
        role = claims['role']
        if role not in {'agent', 'human', 'git'}:
            raise ValueError()
        key = Path(os.environ['VEXA_CONNECTIONS_' + role.upper() + '_KEY_FILE']).read_bytes().strip()
        if len(key) < 32 or not hmac.compare_digest(hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest(), signature):
            raise ValueError()
        now = time.time()
        if not now - 30 <= claims['at'] <= now + 5:
            raise ValueError()
        path = request.url.path + ('?' + request.url.query if request.url.query else '')
        if claims['method'] != request.method or claims['path'] != path or claims['body'] != hashlib.sha256(body).hexdigest():
            raise ValueError()
        if any(not isinstance(claims[k], str) or not 1 <= len(claims[k]) <= 160 for k in ('actor', 'session', 'nonce')):
            raise ValueError()
        with db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS assertion_nonces (nonce TEXT PRIMARY KEY, expires REAL)')
            c.execute('DELETE FROM assertion_nonces WHERE expires < ?', (now,))
            c.execute('INSERT INTO assertion_nonces VALUES (?, ?)', (claims['nonce'], now + 60))
        return {'actor': claims['actor'], 'session': claims['session'], 'role': role, 'product': True}
    except Exception:
        raise HTTPException(401, 'Product identity refused') from None
