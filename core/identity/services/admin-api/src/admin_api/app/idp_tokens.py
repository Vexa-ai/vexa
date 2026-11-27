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
import re
import time
from typing import Any, Awaitable, Callable, Dict, Optional

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_KEYS = "https://www.googleapis.com/oauth2/v3/certs"
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]
LEEWAY_S = 60
KEYS_TTL_S = 3600.0
MAX_DOC_BYTES = 256 * 1024
#: The connect route's audiences (``app/connect.py``), the operator's to set (config.v1).
CONNECT_GOOGLE_CLIENT_IDS = "VEXA_CONNECT_GOOGLE_CLIENT_IDS"
#: What a Google OAuth client id looks like. One entry of the connect list that is not is a
#: misconfiguration, and the whole list is refused (nothing verifies) rather than guessed at.
GOOGLE_CLIENT_ID_RE = re.compile(r"^[0-9]{6,32}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com$")
#: The connect route takes only a FRESH sign-in: issued no more than this many seconds ago.
CONNECT_MAX_AGE_S = 300
#: A token naming a kid the cached keys do not hold re-reads the issuer's keys at most this often.
KEYS_MISS_REFETCH_S = 60.0


class Invalid(Exception):
    """Not a token identity accepts. The message names the check, never the token."""


def _strip(u: str) -> str:
    return (u or "").strip().rstrip("/")


def issuer_config(provider: str, connect: bool = False) -> Optional[dict]:
    """``{"issuers", "audience", "keys"|"discovery"}`` for a provider, or None when unconfigured.

    ``connect`` asks for the connect route's audiences (``app/connect.py``): the Google client ids of
    the sign-ins allowed to connect, ``VEXA_CONNECT_GOOGLE_CLIENT_IDS``, comma-separated. Any one of
    them is accepted; unset, nothing verifies. Every other caller keeps the one audience, OUR client."""
    if provider == "google":
        aud: Any = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        if connect:
            aud = connect_client_ids()
        return {"issuers": list(GOOGLE_ISSUERS), "audience": aud, "keys": GOOGLE_KEYS} if aud else None
    if connect:
        return None
    if provider == "oidc":
        iss = _strip(os.getenv("VEXA_OIDC_ISSUER") or "")
        aud = (os.getenv("VEXA_OIDC_CLIENT_ID") or "").strip()
        if not (iss.startswith("https://") and aud):
            return None
        return {"issuers": [iss, iss + "/"], "audience": aud,
                "discovery": f"{iss}/.well-known/openid-configuration"}
    return None


_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def canonical_segment(seg: str) -> bool:
    """Is ``seg`` base64url in its one canonical spelling: no padding, no characters outside the
    alphabet, and no bits set past the last whole byte (it re-encodes to itself)?"""
    import base64

    if not seg or not _B64URL_RE.match(seg) or len(seg) % 4 == 1:
        return False
    try:
        raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))
    except (ValueError, TypeError):
        return False
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == seg


def connect_client_ids() -> list:
    """``VEXA_CONNECT_GOOGLE_CLIENT_IDS`` as a list — or [] (nothing verifies) when unset, or when any
    entry is not a plausible Google client id."""
    ids = [x.strip() for x in (os.getenv(CONNECT_GOOGLE_CLIENT_IDS) or "").split(",") if x.strip()]
    return ids if ids and all(GOOGLE_CLIENT_ID_RE.match(x) for x in ids) else []


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
_fetched_at: Dict[str, float] = {}   # keys url -> when its keys were last read


def reset() -> None:
    _keys.clear()
    _fetched_at.clear()


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
        now = time.monotonic()
        if attempt == 0 and hit and hit[0] > now:
            keys = hit[1]
        elif attempt == 1 and hit and now - _fetched_at.get(url, 0.0) < KEYS_MISS_REFETCH_S:
            # An unknown kid re-reads the keys at most once a minute: a stream of made-up kids is
            # not a stream of fetches to the issuer.
            break
        else:
            doc = await _fetch_json(url)
            keys = {str(k.get("kid") or ""): k for k in (doc or {}).get("keys") or [] if isinstance(k, dict)}
            _keys[url] = (time.monotonic() + KEYS_TTL_S, keys)
            _fetched_at[url] = time.monotonic()
        if kid in keys:
            return keys[kid]
        if len(keys) == 1 and not kid:
            return next(iter(keys.values()))
    raise Invalid("the token was not signed with one of the issuer's keys")


async def verified_claims(provider: str, id_token: str, connect: bool = False) -> dict:
    """The claims of ``id_token`` once it is proven to be ``provider``'s, for OUR client, unexpired.
    With ``connect``, for one of the connect route's clients instead (:func:`issuer_config`).
    Raises :class:`Invalid` otherwise."""
    import jwt

    cfg = issuer_config(provider, connect)
    if cfg is None:
        raise Invalid(f"{provider} sign-in is not configured here")
    token = (id_token or "").strip()
    if token.count(".") != 2 or len(token) > 16384:
        raise Invalid("not a signed token")
    if connect and not all(canonical_segment(seg) for seg in token.split(".")):
        # One token, one spelling: base64url with no `=` padding and no stray bits, so the same
        # signed token cannot be sent again as different text (connect.py keeps each token once).
        raise Invalid("not a canonical signed token")
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
    claims = claims if isinstance(claims, dict) else {}
    if connect:
        # A FRESH sign-in only, and when Google names the party the token was issued to (azp), it is
        # one of the listed clients too.
        try:
            iat = float(claims.get("iat"))
        except (TypeError, ValueError):
            raise Invalid("the token has no issue time") from None
        if iat < time.time() - CONNECT_MAX_AGE_S:
            raise Invalid("the sign-in is not fresh")
        if "azp" in claims and claims.get("azp") not in cfg["audience"]:
            raise Invalid("the token was issued to another client")
    return claims
