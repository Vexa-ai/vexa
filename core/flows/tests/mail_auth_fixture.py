"""What a receiving MTA writes on a mail whose `From:` its domain vouched for (D25).

The intake reads the topmost `Authentication-Results` before it treats a `From:` as a person, so a
test mail standing for a real colleague's or user's message carries this verdict; a test about
spoofing leaves it off or writes a failing one.
"""
from __future__ import annotations


def authed(frm: str, headers: dict | None = None) -> dict:
    """``headers`` plus a DMARC-pass verdict for ``frm``'s domain (unless one is already there)."""
    out = dict(headers or {})
    domain = str(frm or "").rsplit("@", 1)[-1].lower()
    out.setdefault("Authentication-Results",
                   f"mx.test; dkim=pass header.d={domain}; spf=pass smtp.mailfrom={domain}; "
                   f"dmarc=pass header.from={domain}")
    return out
