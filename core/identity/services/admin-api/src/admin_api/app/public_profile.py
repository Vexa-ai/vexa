"""PUBLIC PROFILE — what a person declares about themselves for everyone (R4; registry ADR, founder
2026-10-15: "the only cross-org layer is what an entity declares about itself").

IDENTITY IS THE ONE WRITER. The profile lives in the person's own row (``users.data["public_profile"]``)
and only the person changes it, field by field:

    name · photo · role · company · links · bio        each {value, public}

NOTHING IS PUBLIC UNTIL PUBLISHED. An account is not a profile; saving a draft publishes nothing;
"withdraw" hides the whole profile at once (the values stay, for the person to publish again).

A CRM RECORD MAY LINK TO A PROFILE; IT NEVER COPIES IT. crm-api reads the public fields live through
``GET /internal/profiles/{handle}`` and tells identity it linked or unlinked through
``POST /internal/profiles/{handle}/links``. Identity keeps only a SALTED HASH per org, so the owner
sees how many orgs link to them — never which, and never anything an org wrote.

NO ENUMERATION. A profile is reached by an opaque ``handle`` (random, ≥ 16 characters), never by user
id; an unpublished or withdrawn handle answers exactly as one that never existed. The directory
answers a query of at least ``MIN_QUERY`` letters with at most ``MAX_RESULTS`` profiles, matched on
public fields only, with no count of matches, and is rate-limited per person (:class:`RateLimiter`).

VERIFIED COMPANIES. A person may verify a company DOMAIN — by a DNS TXT record or because their own
verified email is on that domain. This is data plus a flow, no payment. The verified domain shows on
the public profile only beside a public company field.

Behind ``public_profiles`` (``VEXA_FEATURES``), an EXPLICIT feature: ``all`` does not switch it on.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from typing import Any, Iterable, Mapping, Optional, Tuple

DATA_KEY = "public_profile"
#: RETIRED (PRD 0.13.4 D35, 2026-10-17): THE profile is the person's claimed CRM record. This module's
#: write path is closed; its reads answer for one release with where the profile moved.
MOVED_TO = "/api/person/profile"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def moved() -> dict:
    """The answer the closed write doors give."""
    return {"refused": "profile_moved", "moved_to": MOVED_TO,
            "tell_your_person": "Your profile is now your own record in the organisation's directory: edit it "
                                "in Settings → Profile. Its public page is /p/<handle>, off until you switch it on."}
VERIFY_KEY = "company_verification"
FEATURE = "public_profiles"
REASON = "feature_disabled"

#: field -> (kind, max length) — the closed vocabulary
FIELDS = {"name": ("text", 120), "photo": ("url", 500), "role": ("text", 120),
          "company": ("text", 120), "links": ("urls", 5), "bio": ("text", 500)}
SEARCHED = ("name", "role", "company")
MIN_QUERY = 3
MAX_RESULTS = 10
_SALT = "vexa-profile-links-v1"
#: How long a DNS TXT verification stands before it must be checked again (a removed record lapses).
VERIFY_TTL_S = 30 * 86400
#: Consumer mail providers: an address on one proves nothing about a company, and nobody verifies them.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.de", "gmx.net", "gmx.com", "web.de", "mail.com", "yandex.ru", "yandex.com", "mail.ru",
    "zoho.com", "fastmail.com", "tutanota.com", "tuta.io", "qq.com", "163.com", "126.com",
    "hey.com", "duck.com", "outlook.de", "hotmail.de", "hotmail.co.uk", "yahoo.co.uk", "yahoo.de",
    "t-online.de", "orange.fr", "free.fr", "laposte.net", "libero.it", "seznam.cz", "wp.pl"})
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class Refused(ValueError):
    def __init__(self, detail: dict):
        super().__init__(str(detail))
        self.detail = detail


def feature_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """``public_profiles`` by NAME only: ``all`` does not switch an explicit feature on."""
    raw = str(env.get("VEXA_FEATURES") if env is not None else os.environ.get("VEXA_FEATURES", "") or "")
    on = False
    for token in (t.strip().lower() for t in raw.split(",")):
        if token.lstrip("-+") == FEATURE:
            on = not token.startswith("-")
    return on


def refusal() -> dict:
    return {"status": "refused", "reason": REASON, "feature": FEATURE,
            "instruction": f"{FEATURE} is switched off on this deployment. Do not retry it another way; "
                           "say it is not available here.",
            "tell_your_person": "Public profiles aren't switched on here yet. An administrator can turn them on."}


def _profile(data: Mapping[str, Any]) -> dict:
    p = (data or {}).get(DATA_KEY)
    return dict(p) if isinstance(p, dict) else {}


def _check(name: str, value: Any) -> Any:
    kind, limit = FIELDS[name]
    if kind == "urls":
        vals = value if isinstance(value, list) else []
        if len(vals) > limit or any(not isinstance(v, str) or not re.match(r"^https://[^\s]{3,300}$", v) for v in vals):
            raise Refused({"refused": f"{name}: at most {limit} https:// links"})
        return list(vals)
    text = str(value or "").strip()
    if kind == "url":
        if text and not re.match(r"^https://[^\s]{3,%d}$" % limit, text):
            raise Refused({"refused": f"{name}: an https:// address"})
        return text
    if len(text) > limit or any(ord(ch) < 32 for ch in text):
        raise Refused({"refused": f"{name}: at most {limit} characters on one line"})
    return text


def apply(data: Mapping[str, Any], fields: Mapping[str, Any], *, publish: Optional[bool] = None) -> dict:
    """The person's edit: any subset of fields as ``{value, public}``; ``publish`` True/False sets the
    profile's state, None keeps it. Unknown fields are refused WITH the list. Returns the new data."""
    out = dict(data or {})
    prof = _profile(out)
    stored = dict(prof.get("fields") or {})
    for name, change in (fields or {}).items():
        if name not in FIELDS:
            raise Refused({"refused": f"{name!r} is not a profile field", "fields": list(FIELDS)})
        change = change if isinstance(change, dict) else {"value": change}
        cur = dict(stored.get(name) or {"value": "" if FIELDS[name][0] != "urls" else [], "public": False})
        if "value" in change:
            cur["value"] = _check(name, change["value"])
        if "public" in change:
            cur["public"] = bool(change["public"])
        stored[name] = cur
    prof["fields"] = stored
    prof.setdefault("handle", secrets.token_urlsafe(16))
    if publish is not None:
        prof["published"] = bool(publish)
    out[DATA_KEY] = prof
    return out


def withdraw(data: Mapping[str, Any]) -> dict:
    out = dict(data or {})
    prof = _profile(out)
    if prof:
        prof["published"] = False
        out[DATA_KEY] = prof
    return out


def _company_text(data: Mapping[str, Any]) -> str:
    f = (_profile(data).get("fields") or {}).get("company") or {}
    return str(f.get("value") or "").strip().casefold() if isinstance(f, dict) else ""


def _verified_domain(data: Mapping[str, Any], *, email: Optional[str] = None,
                     now: Optional[float] = None) -> Optional[str]:
    """The verified company domain while the verification still stands, else None. It stands only:
      - for the company text it was verified with (a renamed company loses the badge);
      - by email: while the account's address is still that address, on that domain, verified;
      - by DNS TXT: for VERIFY_TTL_S after the last successful check (a removed record lapses)."""
    v = (data or {}).get(VERIFY_KEY)
    if not isinstance(v, dict) or not v.get("verified_at") or v.get("domain") in FREE_MAIL:
        return None
    if v.get("company") is not None and v.get("company") != _company_text(data):
        return None
    now = time.time() if now is None else now
    if v.get("method") == "admin_email":
        addr = str(email or "").strip().lower()
        if not addr or addr != v.get("email") or not email_is_verified(data, addr):
            return None
    elif v.get("method") == "dns_txt":
        if now - float(v.get("checked_at") or v.get("verified_at") or 0) > VERIFY_TTL_S:
            return None
    else:
        return None
    return v.get("domain")


def public_view(data: Mapping[str, Any], *, email: Optional[str] = None,
                now: Optional[float] = None) -> Optional[dict]:
    """What anyone may see, or None (no profile, not published, withdrawn). ``email`` is the owner's
    current address, which an email-based company verification is checked against."""
    prof = _profile(data)
    if not prof.get("published"):
        return None
    fields = {k: v.get("value") for k, v in (prof.get("fields") or {}).items()
              if k in FIELDS and isinstance(v, dict) and v.get("public") and v.get("value")}
    if not fields:
        return None
    return {"handle": prof["handle"], "fields": fields,
            "company_verified": _verified_domain(data, email=email, now=now) if "company" in fields else None}


def read_own(data: Mapping[str, Any]) -> dict:
    """The person's own view: every field with its public flag, the state, and HOW MANY orgs link."""
    prof = _profile(data)
    return {"handle": prof.get("handle"), "published": bool(prof.get("published")),
            "fields": {k: dict((prof.get("fields") or {}).get(k) or {"value": "" if FIELDS[k][0] != "urls" else [],
                                                                      "public": False}) for k in FIELDS},
            "linked_by": len(prof.get("links") or []),
            "company_verification": (data or {}).get(VERIFY_KEY) or None}


def _org_key(org: str, handle: str) -> str:
    return hashlib.sha256(f"{_SALT}:{handle}:{org}".encode()).hexdigest()[:24]


def link(data: Mapping[str, Any], org: str, linked: bool) -> dict:
    """An org linked (or unlinked) a contact to this profile. Only a salted hash is kept."""
    out = dict(data or {})
    prof = _profile(out)
    if not prof.get("handle"):
        return out
    key = _org_key(str(org), prof["handle"])
    keys = set(prof.get("links") or [])
    keys.add(key) if linked else keys.discard(key)
    prof["links"] = sorted(keys)
    out[DATA_KEY] = prof
    return out


def _unpack(row) -> tuple:
    """A directory row: (id, data) or (id, data, email)."""
    return (row[0], row[1], row[2] if len(row) > 2 else None)


def find(rows: Iterable[Tuple[Any, ...]], handle: str) -> Optional[dict]:
    for row in rows:
        _uid, data, email = _unpack(row)
        view = public_view(data, email=email)
        if view and secrets.compare_digest(view["handle"], str(handle or "")):
            return view
    return None


def search(rows: Iterable[Tuple[Any, Mapping[str, Any]]], q: str) -> list:
    """Published profiles whose PUBLIC name, role or company contain every word of ``q``."""
    words = [w for w in re.findall(r"[^\W_]+", str(q or "").lower())]
    if sum(len(w) for w in words) < MIN_QUERY:
        return []
    out = []
    for row in rows:
        _uid, data, email = _unpack(row)
        view = public_view(data, email=email)
        if not view:
            continue
        hay = " ".join(str(view["fields"].get(k) or "") for k in SEARCHED).lower()
        if all(w in hay for w in words):
            out.append(view)
            if len(out) >= MAX_RESULTS:
                break
    return out


def start_verification(data: Mapping[str, Any], domain: str) -> dict:
    d = str(domain or "").strip().lower().rstrip(".")
    if not _DOMAIN.match(d):
        raise Refused({"refused": "a company domain like example.org"})
    if d in FREE_MAIL:
        raise Refused({"refused": f"{d} is a consumer mail provider, not a company domain"})
    out = dict(data or {})
    out[VERIFY_KEY] = {"domain": d, "txt": "vexa-verify=" + secrets.token_urlsafe(18)}
    return out


def check_verification(data: Mapping[str, Any], *, txt_records: Iterable[str], email: str = "",
                       email_verified: bool = False, now: Optional[float] = None) -> dict:
    """Verified when the domain's TXT records carry the token, or the person's own VERIFIED email is
    on the domain (an admin email on it)."""
    out = dict(data or {})
    v = dict(out.get(VERIFY_KEY) or {})
    if not v.get("domain"):
        return out
    now = time.time() if now is None else now
    addr = str(email or "").strip().lower()
    method = None
    if v.get("txt") and any(str(t).strip().strip('"') == v["txt"] for t in txt_records or []):
        method = "dns_txt"
    elif (email_verified and v["domain"] not in FREE_MAIL and addr.rsplit("@", 1)[-1] == v["domain"]):
        method = "admin_email"
    if method:
        # bound to what it proves: the company text now, and (by email) the address that proved it
        v.update(verified_at=now, checked_at=now, method=method, company=_company_text(out),
                 email=addr if method == "admin_email" else None)
    else:
        # a check that no longer proves it (the record removed, the address gone) lapses it at once
        for k in ("verified_at", "checked_at", "method", "company", "email"):
            v.pop(k, None)
    out[VERIFY_KEY] = v
    return out


EMAIL_VERIFIED_KEY = "email_verified"
#: How an inbox was proven at sign-in: an emailed link the person opened, or an identity provider
#: that vouches for the address (Google's `email_verified`, a configured Microsoft tenant).
_VIA = re.compile(r"^(magic_link|oidc:[a-z0-9_.-]{1,40})$")


def record_email_verified(data: Mapping[str, Any], email: str, *, via: str, now: Optional[float] = None) -> dict:
    """Record that the account's address was proven at sign-in. Kept with the address it proves, so
    a later change of address lapses it."""
    if not _VIA.match(str(via or "")):
        raise Refused({"refused": "an email is verified by a sign-in link or an identity provider only"})
    out = dict(data or {})
    out[EMAIL_VERIFIED_KEY] = {"email": str(email or "").strip().lower(), "via": via,
                               "at": time.time() if now is None else now}
    return out


def email_is_verified(data: Mapping[str, Any], email: str) -> bool:
    rec = (data or {}).get(EMAIL_VERIFIED_KEY)
    return isinstance(rec, dict) and bool(email) and rec.get("email") == str(email).strip().lower()


class RateLimiter:
    """At most ``per_minute`` directory reads per person per minute (in this process)."""

    def __init__(self, per_minute: int = 30):
        self.per_minute = per_minute
        self._hits: dict = {}

    def reset(self) -> None:
        self._hits.clear()

    def allow(self, who: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = [t for t in self._hits.get(who, []) if now - t < 60.0]
        if len(hits) >= self.per_minute:
            self._hits[who] = hits
            return False
        hits.append(now)
        self._hits[who] = hits
        return True
