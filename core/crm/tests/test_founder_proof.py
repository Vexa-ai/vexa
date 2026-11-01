"""The founder's writes need his Cloudflare Access proof (founder 1 Nov 2026, Q7 «A», option 3).

A revision under the founder's subject is refused unless the request carried a verified Access JWT; agents, the stage
rule and sensors are unaffected; with no configuration nothing changes. Offline: SQLite, a locally made RSA key.
"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from crm import founder_proof as fp
from crm.store import Refused

from test_card_field_writers import CEO, ORG, THE_FOUNDER, cards, plain, store  # noqa: F401 — fixtures

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CFG = {"subject": THE_FOUNDER.subject, "team_domain": "https://team.cloudflareaccess.com", "aud": "app-aud",
       "emails": ["dmitry@vexa.ai"]}


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class StaticCerts(fp.Certs):
    def __init__(self):
        super().__init__("x", fetch=lambda: {})
        self.keys, self.at = {"k1": KEY.public_key()}, time.time()

    def get(self, kid, now):
        return self.keys.get(kid)


def token(key=KEY, **over):
    now = int(time.time())
    claims = {"iss": CFG["team_domain"], "aud": [CFG["aud"]], "email": "dmitry@vexa.ai", "exp": now + 600, "iat": now}
    claims.update(over)
    h = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    p = b64(json.dumps(claims).encode())
    return f"{h}.{p}.{b64(key.sign(f'{h}.{p}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


def test_verify():
    c = StaticCerts()
    assert fp.verify(token(), CFG, certs=c)
    for bad in (token(key=OTHER), token(email="mallory@vexa.ai"), token(aud=["x"]), token(exp=1),
                token(iss="https://evil.cloudflareaccess.com"), "", None, "a.b.c"):
        assert not fp.verify(bad, CFG, certs=c)


def test_a_founder_write_needs_the_proof_and_nobody_else_is_touched(cards, store, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRM_FOUNDER_PROOF", json.dumps(CFG))
    card = plain(cards)                                               # an agent's write: no proof asked
    tok = fp.PROVEN.set(False)
    try:
        with pytest.raises(Refused) as e:
            store.change(ORG, THE_FOUNDER, card["id"], card["revision"], {"Priority": 1}, reason="order")
        assert e.value.body["reason"] == "founder_proof"
        fp.PROVEN.set(True)
        store.change(ORG, THE_FOUNDER, card["id"], card["revision"], {"Priority": 1}, reason="order")
    finally:
        fp.PROVEN.reset(tok)


def test_off_when_not_configured(cards, store, monkeypatch):  # noqa: F811
    monkeypatch.delenv("CRM_FOUNDER_PROOF", raising=False)
    card = plain(cards)
    store.change(ORG, THE_FOUNDER, card["id"], card["revision"], {"Priority": 2}, reason="order")
