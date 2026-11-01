"""The founder's writes carry his proof (founder 1 Nov 2026, Q7 «A», option 3): a write recorded under the founder's
subject is accepted only when the request carried a valid Cloudflare Access JWT for an allowed email
(``X-Founder-Proof``), verified here against the Access team's signing keys. His CRM credential alone is then useless
to anyone who holds a copy: nobody but Cloudflare can sign that token.

Configured per install (off when unset): ``CRM_FOUNDER_PROOF`` = JSON
``{"subject": "<founder subject>", "team_domain": "https://<team>.cloudflareaccess.com", "aud": "<Access app AUD>",
"emails": ["<allowed email>"]}``. The JWT is never stored, logged or returned. LIMIT: a captured token can be replayed
until it expires (the Access session length, 24 h by default).
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.request
from contextvars import ContextVar

LEEWAY = 30
#: Set by the API for the one request whose caller presented a verified proof.
PROVEN: ContextVar = ContextVar("crm_founder_proven", default=False)


def config() -> dict | None:
    raw = os.environ.get("CRM_FOUNDER_PROOF", "").strip()
    if not raw:
        return None
    cfg = json.loads(raw)
    return cfg if cfg.get("subject") and cfg.get("team_domain") and cfg.get("aud") and cfg.get("emails") else None


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _int(s: str) -> int:
    return int.from_bytes(_b64(s), "big")


class Certs:
    """The team's signing keys by kid; fetched lazily, refreshed after an hour or on an unknown kid (≤ once / 30 s)."""

    def __init__(self, url: str, fetch=None):
        self.url, self._fetch = url, fetch or self._http
        self.keys: dict = {}
        self.at = 0.0
        self.lock = threading.Lock()

    def _http(self) -> dict:
        with urllib.request.urlopen(self.url, timeout=10) as r:  # noqa: S310 — the configured Access team
            return json.load(r)

    def get(self, kid: str, now: float):
        from cryptography.hazmat.primitives.asymmetric import rsa
        with self.lock:
            if now - self.at > 3600 or (kid not in self.keys and now - self.at > 30):
                try:
                    doc = self._fetch()
                    keys = {k["kid"]: rsa.RSAPublicNumbers(_int(k["e"]), _int(k["n"])).public_key()
                            for k in doc.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
                    if keys:
                        self.keys, self.at = keys, now
                except Exception:  # noqa: BLE001 — network: keep the cached keys
                    pass
            return self.keys.get(kid)


_CERTS: dict = {}


def verify(token: str | None, cfg: dict, now: float | None = None, certs: Certs | None = None) -> bool:
    """True when ``token`` is an RS256 Access JWT signed by the team, for the app's audience, unexpired, for an
    allowed email. Never raises; never logs the token."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    now = time.time() if now is None else now
    if not token:
        return False
    try:
        h64, p64, s64 = token.split(".")
        head, claims = json.loads(_b64(h64)), json.loads(_b64(p64))
    except Exception:  # noqa: BLE001
        return False
    if head.get("alg") != "RS256":
        return False
    iss = cfg["team_domain"].rstrip("/")
    certs = certs or _CERTS.setdefault(iss, Certs(iss + "/cdn-cgi/access/certs"))
    key = certs.get(str(head.get("kid")), now)
    if key is None:
        return False
    try:
        key.verify(_b64(s64), f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        return False
    aud = claims.get("aud")
    return (claims.get("iss") == iss and cfg["aud"] in (aud if isinstance(aud, list) else [aud])
            and isinstance(claims.get("exp"), (int, float)) and claims["exp"] >= now - LEEWAY
            and not (isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + LEEWAY)
            and str(claims.get("email", "")).lower() in {e.lower() for e in cfg["emails"]})


def require(actor) -> None:
    """Called on every revision: a write under the founder's subject needs this request's verified proof."""
    cfg = config()
    if cfg and str(actor) == str(cfg["subject"]) and not PROVEN.get():
        from .store import Refused
        raise Refused({"reason": "founder_proof",
                       "instruction": "A write as the founder needs his Cloudflare Access proof (X-Founder-Proof): "
                                      "press it on the factory board through factory.vexa.ai."})
