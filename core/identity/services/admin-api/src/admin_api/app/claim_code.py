"""THE ADMIN CLAIM CODE — what makes the first administrator someone who can read this server's log.

WHY. A fresh instance has no administrator, and whoever claims the role configures models,
transcription and every other user. Before this code existed the first person to reach the sign-in
screen got the role, so an instance exposed before its operator signed in belonged to whoever found
it first. Now the claim needs a one-time code that admin-api writes to its own log at boot: reading
it takes access to the deployment, not just to the URL. (`VEXA_ADMIN_EMAILS` is the other way in, and
when it is set no code is issued at all.)

THE CODE is 16 characters of Crockford base32 (80 bits), shown as four groups of four. Typing is
forgiving: case, spaces and dashes do not matter, and O/I/L read as 0/1/1.

STORED AS A DIGEST ONLY, in the `admin_claim` platform_settings row, never as the code itself. One row
for the deployment, so every admin-api replica checks the same code. The claim that succeeds clears
the row in the same transaction that grants the role: a code works once.

WHO ISSUES. Every boot of an unclaimed instance issues a fresh code and invalidates the previous one,
so an operator who lost the log line restarts admin-api — with one exception: a code another replica
issued in the last `REISSUE_GRACE_S` seconds is kept, so replicas starting together agree on one code
(the log line says which replica holds it).

This module is pure; `app/main.py` owns the row and the doors.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

ROW_KEY = "admin_claim"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O, U
LENGTH = 16
GROUP = 4
REISSUE_GRACE_S = 300

_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1"})
_NOT_CODE = re.compile(r"[^0-9A-Z]")


def generate() -> str:
    """A fresh code, grouped for reading aloud: ``XXXX-XXXX-XXXX-XXXX``."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return "-".join(raw[i:i + GROUP] for i in range(0, LENGTH, GROUP))


def normalize(code) -> str:
    """The code as typed, reduced to what it means: upper case, no separators, confusables folded."""
    return _NOT_CODE.sub("", str(code or "").upper().translate(_CONFUSABLE))


def digest(code) -> str:
    return hashlib.sha256(normalize(code).encode("ascii")).hexdigest()


def record(code: str, *, host: str, now: datetime) -> dict:
    """The row value for a freshly issued code — its digest, when, and which replica wrote it."""
    return {"sha256": digest(code), "issued_at": now.isoformat(), "issued_by": host}


def is_live(rec) -> bool:
    """Is there an unconsumed code at all?"""
    return isinstance(rec, dict) and bool(rec.get("sha256"))


def matches(code, rec) -> bool:
    """Does ``code`` open ``rec``? False for an empty or malformed code, and for a consumed record."""
    if not is_live(rec):
        return False
    n = normalize(code)
    if len(n) != LENGTH:
        return False
    return hmac.compare_digest(digest(n), str(rec["sha256"]))


def held_elsewhere(rec, *, host: str, now: datetime) -> Optional[str]:
    """The replica that issued a still-fresh code, when that is not this one — this boot keeps its
    code rather than replacing it. None when this boot should issue."""
    if not is_live(rec) or rec.get("issued_by") in (None, "", host):
        return None
    try:
        issued = datetime.fromisoformat(str(rec.get("issued_at")))
    except ValueError:
        return None
    if now - issued < timedelta(seconds=REISSUE_GRACE_S):
        return str(rec.get("issued_by"))
    return None


def announcement(code: str) -> str:
    """The log text. It names the code and the two ways to make it unnecessary."""
    return (
        "\n════════════════════ ADMIN CLAIM CODE ════════════════════\n"
        "This instance has no administrator. Open the terminal and enter this one-time code\n"
        "on the claim screen; the person who signs in with it becomes the administrator:\n"
        f"\n        {code}\n\n"
        "It works once. Restarting admin-api issues a new one and retires this one.\n"
        "To name the administrators instead, set VEXA_ADMIN_EMAILS (no code is issued then).\n"
        "═══════════════════════════════════════════════════════════"
    )
