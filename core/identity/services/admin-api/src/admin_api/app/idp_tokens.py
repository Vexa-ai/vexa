"""idp_tokens.py — a sign-in's ID token, verified by identity itself (Vexa-ai/vexa#1783, UI 15g).

WHY IDENTITY VERIFIES. A person's provider photo is read from their sign-in's `picture` claim. If the
address came as a parameter, anyone holding INTERNAL_API_SECRET could put any picture on anybody.
So the terminal hands over the raw ID token and identity checks it here, the same way the sign-in
library did: the signature against the issuer's published keys, the issuer, the audience (OUR client
id), the expiry. ``person_photo_doors`` then requires the token's subject to be the provider
identity this account is bound to (``provider_subjects``) before it reads ``picture``.

THE ISSUERS are the operator's configuration, never a caller's:

    google   https://accounts.google.com, keys at https://www.googleapis.com/oauth2/v3/certs,
             audience GOOGLE_CLIENT_ID
    oidc     VEXA_OIDC_ISSUER (its discovery document names the keys), audience VEXA_OIDC_CLIENT_ID,
             VEXA_OIDC_CA_FILE when the provider's TLS is issued under an internal CA

These are the same values the terminal signs people in with. A provider with no client id
configured verifies nothing, so no photo is taken from it.

The keys are fetched from the operator's issuer (an internal ADFS may sit on a private network,
which is why this is not the user-URL guard) and cached for ``KEYS_TTL_S``; an unknown ``kid``
re-reads them once.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_KEYS = "https://www.googleapis.com/oauth2/v3/certs"
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]
LEEWAY_S = 60
KEYS_TTL_S = 3600.0
MAX_DOC_BYTES = 256 * 1024


class Invalid(Exception):
    """Not a token identity accepts. The message names the check, never the token."""


def _strip(u: str) -> str:
    return (u or "").strip().rstrip("/")


def issuer_config(provider: str) -> Optional[dict]:
    """``{"issuers", "audience", "keys"|"discovery"}`` for a provider, or None when unconfigured."""
    if provider == "google":
        aud = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        return {"issuers": list(GOOGLE_ISSUERS), "audience": aud, "keys": GOOGLE_KEYS} if aud else None
    if provider == "oidc":
        iss = _strip(os.getenv("VEXA_OIDC_ISSUER") or "")
        aud = (os.getenv("VEXA_OIDC_CLIENT_ID") or "").strip()
        if not (iss.startswith("https://") and aud):
            return None
        return {"issuers": [iss, iss + "/"], "audience": aud,
                "discovery": f"{iss}/.well-known/openid-configuration"}
    return None


def bound_subject(provider: str, claims: dict) -> str:
    """The binding subject this verified token stands for — the shape the terminal's
    ``providerIdentity.ts`` records in ``provider_subjects``."""
    sub = str(claims.get("sub") or "")
    if provider == "google":
        return f"google:{sub}"
    if provider == "oidc":
        digest = hashlib.sha256(f"{_strip(str(claims.get('iss') or ''))}\n{sub}".encode()).hexdigest()
        return f"oidc:{digest}"
    return ""


async def _http_json(url: str) -> Any:
    """GET an issuer document (discovery or keys). https only; the OIDC CA bundle when configured."""
    import httpx

    if not url.startswith("https://"):
        raise Invalid("the issuer's keys are not on https")
    ca = (os.getenv("VEXA_OIDC_CA_FILE") or "").strip()
    verify: Any = True
    if ca:
        import ssl

        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=ca)
        verify = ctx
    async with httpx.AsyncClient(timeout=5.0, verify=verify, follow_redirects=False) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200 or len(r.content) > MAX_DOC_BYTES:
            raise Invalid("the issuer's keys could not be read")
        return r.json()


#: The seam tests replace.
_fetch_json: Callable[[str], Awaitable[Any]] = _http_json
_keys: Dict[str, tuple] = {}         # keys url -> (expires, {kid: jwk})


def reset() -> None:
    _keys.clear()


async def _keys_url(cfg: dict) -> str:
    if cfg.get("keys"):
        return cfg["keys"]
    doc = await _fetch_json(cfg["discovery"])
    url = str((doc or {}).get("jwks_uri") or "")
    if not url.startswith("https://"):
        raise Invalid("the issuer's discovery document names no https keys")
    return url


async def _jwk(cfg: dict, kid: str) -> dict:
    url = await _keys_url(cfg)
    for attempt in range(2):
        hit = _keys.get(url)
        if attempt == 0 and hit and hit[0] > time.monotonic():
            keys = hit[1]
        else:
            doc = await _fetch_json(url)
            keys = {str(k.get("kid") or ""): k for k in (doc or {}).get("keys") or [] if isinstance(k, dict)}
            _keys[url] = (time.monotonic() + KEYS_TTL_S, keys)
        if kid in keys:
            return keys[kid]
        if len(keys) == 1 and not kid:
            return next(iter(keys.values()))
    raise Invalid("the token was not signed with one of the issuer's keys")


async def verified_claims(provider: str, id_token: str) -> dict:
    """The claims of ``id_token`` once it is proven to be ``provider``'s, for OUR client, unexpired.
    Raises :class:`Invalid` otherwise."""
    import jwt

    cfg = issuer_config(provider)
    if cfg is None:
        raise Invalid(f"{provider} sign-in is not configured here")
    token = (id_token or "").strip()
    if token.count(".") != 2 or len(token) > 16384:
        raise Invalid("not a signed token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise Invalid("not a signed token") from None
    alg = str(header.get("alg") or "")
    if alg not in ALGORITHMS:
        raise Invalid("the token's algorithm is not accepted")
    jwk = await _jwk(cfg, str(header.get("kid") or ""))
    try:
        key = jwt.PyJWK(jwk, algorithm=alg).key
        claims = jwt.decode(token, key, algorithms=[alg], audience=cfg["audience"], issuer=cfg["issuers"],
                            leeway=LEEWAY_S, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise Invalid(f"the token did not verify ({type(e).__name__})") from None
    return claims if isinstance(claims, dict) else {}
