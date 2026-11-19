"""WHO THE MAILBOX WILL ACT FOR — the authorization half of the inbound intake.

The mailbox is an open door on the public internet. Before this module, everything that came
through it was treated as a customer: an address nobody had ever seen got a platform account
(`ensure_platform_user`), an agent turn with its body pasted into the prompt, a workspace the
turn could write to, and a reply — with no allow-list, no rate limit and no instance-gate check
(R-B12). Unauthenticated account creation, unbounded model spend and a direct prompt-injection
channel into a workspace-writing agent, in one path, reachable by anybody who can send email.

THE RULE, in the PRD's own words for the outbound direction (§16.2): *the domain allow-list is a
deployment value; outside the domain, never.* This is the same rule pointed inward. Three
populations, and only the first two cost anything:

  a known user            → routed as before; the account already exists because they made it
  inside the allow-list   → a colleague at this deployment's own domain, provisioned as before
  everybody else          → NO account, NO agent turn, NO model call. One quarantine row, and at
                            most one fixed line back — a template, never a model.

UNSET IS NOT "EVERYONE". `VEXA_FLOWS_MAIL_DOMAINS` unset means the mailbox's own domain, exactly
as `VEXA_FLOWS_ATTENDEE_DOMAINS` unset means the organizer's. An allow-list whose empty value is
"admit the internet" is not a hardening control, it is a control that ships off.

THE QUARANTINE ROW IS THE POINT, not a side effect. A refusal that leaves no trace is
indistinguishable from a poller that silently died, and the operator finds out from the model
bill either way. Every refusal is a row: who, what kind, why, and — for an invite — the meeting
facts it carried, so that nothing the mail actually knew is thrown away by refusing to act on it.
"""
from __future__ import annotations

import json
import re
import time

import flows_config as cfg

# The refusal kinds, named so a quarantine row can be read without the code.
STRANGER_MAIL = "stranger_mail"                # not a user, not in the allow-list
UNVERIFIED_INVITE = "unverified_invite"        # an invite whose ORGANIZER is neither
THREAD_MISMATCH = "thread_mismatch"            # In-Reply-To names a thread this sender is not on
RATE_LIMITED = "rate_limited"                  # per-sender or global ceiling on mail-triggered turns

# The one line a quarantined stranger may be answered with, when the deployment turns it on. A
# TEMPLATE: no model runs, nothing is read, nothing is created, and it says the one true thing.
QUARANTINE_TEMPLATE = (
    "This mailbox only takes meeting invitations from inside this organisation — nobody has read "
    "your message and no account was created. If you meant to reach a person here, write to them "
    "directly.")


# The refusal recorded when the company has switched invitations off (`bot_joins_on_invite: off`).
INVITE_POLICY_OFF = "invite_policy_off"

# A `From:` the sending domain did not vouch for (D25, H6): no DMARC pass, no aligned DKIM or SPF
# pass in the receiving MTA's verdict. Treated as a stranger, whoever it names.
UNAUTHENTICATED_MAIL = "unauthenticated_mail"


# ── IS THE `From:` THE SENDER? (D25, H6) ─────────────────────────────────────────────────────────
#
# `From:` is a string anybody can type. A known user's address in it used to be enough to run a
# turn in that user's own session. The domain vouches for its senders through DMARC, and the
# receiving MTA records its verdict in `Authentication-Results` (RFC 8601). We read that verdict;
# we never re-run SPF or DKIM here, and a mail carrying no verdict at all is unauthenticated.
#
# WHICH HEADER: the receiving MTA PREPENDS its own, so the TOPMOST one is ours and anything below it
# came with the message (a sender can write any header it likes). `inbox.from_rfc822` keeps the
# topmost. When `VEXA_FLOWS_MAIL_AUTHSERV_ID` names our MTA, a header that names any other is
# ignored too.
#
# WHOSE VERDICT (`VEXA_FLOWS_MAIL_AUTH_MODE`): `mta` reads the MTA's header and nothing else. That
# header is only as good as the MTA's habits: an authserv-id is public, and an MTA that writes no
# header for some path leaves the sender's own on top (H6b). `inapp` does not read the header at
# all: it verifies the message's DKIM signature here, from the RFC822 bytes, against the key the
# signing domain publishes, and aligns it with the `From:` domain under that domain's DMARC record.
# `both` requires the MTA's verdict AND the signature. SPF is not checked in-app: it needs the
# connecting IP, which an IMAP reader never sees, so in-app DMARC is the DKIM half of DMARC.

_COMMENT = re.compile(r"\([^()]*\)")
_RESULT = re.compile(r"^\s*([a-z0-9_-]+)\s*=\s*([a-z0-9_-]+)(.*)$", re.IGNORECASE | re.DOTALL)
_PROP = re.compile(r"([a-z0-9_-]+\.[a-z0-9_.-]+)\s*=\s*\"?([^\s;\"]+)\"?", re.IGNORECASE)


def _domain(value: str) -> str:
    v = str(value or "").strip().strip("<>").lower()
    return v.rsplit("@", 1)[1] if "@" in v else v


def _aligned(from_domain: str, other: str) -> bool:
    """Relaxed alignment (RFC 7489 §3.1): the same domain, or one a parent of the other."""
    a, b = from_domain.strip(".").lower(), other.strip(".").lower()
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def parse_auth_results(value) -> tuple[str, list[dict]]:
    """``(authserv_id, [{method, result, props}])`` from one `Authentication-Results` value."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    text = _COMMENT.sub(" ", str(value or ""))
    parts = [p.strip() for p in text.split(";")]
    if not parts or not parts[0]:
        return "", []
    authserv = parts[0].split()[0].lower() if parts[0].split() else ""
    out = []
    for part in parts[1:]:
        m = _RESULT.match(part)
        if not m:
            continue
        props = {k.lower(): v.lower() for k, v in _PROP.findall(m.group(3) or "")}
        out.append({"method": m.group(1).lower(), "result": m.group(2).lower(), "props": props})
    return authserv, out


AUTH_MODES = ("mta", "inapp", "both")


def auth_mode() -> str:
    """`VEXA_FLOWS_MAIL_AUTH_MODE`, or ValueError naming it. Unset is `mta`."""
    mode = (cfg.get("VEXA_FLOWS_MAIL_AUTH_MODE") or "mta").lower()
    if mode not in AUTH_MODES:
        raise ValueError(f"VEXA_FLOWS_MAIL_AUTH_MODE={mode!r} — expected one of "
                         + ", ".join(AUTH_MODES))
    return mode


def sender_authenticated(frm: str, headers: dict, raw: bytes = b"",
                         txt=None) -> tuple[bool, str]:
    """``(True, how)`` when the `From:` domain vouches for this mail, else ``(False, why)``.

    `raw` is the message as received (RFC822 bytes), which in-app verification reads; `txt(name)
    -> [str]` is the TXT lookup it uses, `dns_txt` unless injected. Fails closed: no verdict, a
    verdict from another server, a verdict about another domain, a signature that does not verify
    or does not align, an unknown mode and a DNS failure are none of them a pass."""
    from_domain = _domain(frm)
    if not from_domain:
        return False, "the From: address has no domain"
    try:
        mode = auth_mode()
    except ValueError as e:
        return False, str(e)
    if mode == "inapp":
        return dkim_dmarc(from_domain, raw, txt or dns_txt)
    ok, how = mta_verdict(from_domain, headers)
    if mode == "mta" or not ok:
        return ok, how
    ok, how_inapp = dkim_dmarc(from_domain, raw, txt or dns_txt)
    return ok, (f"{how}; {how_inapp}" if ok else how_inapp)


def mta_verdict(from_domain: str, headers: dict) -> tuple[bool, str]:
    """The receiving MTA's `Authentication-Results` verdict on `from_domain`."""
    raw = None
    for k, v in (headers or {}).items():
        if str(k).lower() == "authentication-results":
            raw = v
            break
    if not raw:
        return False, "no Authentication-Results verdict on the message"
    authserv, results = parse_auth_results(raw)
    want = (cfg.get("VEXA_FLOWS_MAIL_AUTHSERV_ID") or "").strip().lower()
    if want and authserv != want:
        return False, f"the Authentication-Results verdict is not from {want}"
    dmarc = [r for r in results if r["method"] == "dmarc"]
    for r in dmarc:
        hf = r["props"].get("header.from", from_domain)
        # header.from IS the domain the MTA evaluated: it must be this From:'s, exactly
        if r["result"] == "pass" and hf.strip(".") == from_domain:
            return True, "dmarc=pass"
    if any(r["result"] == "fail" for r in dmarc):
        return False, "dmarc=fail for the From: domain"
    # No DMARC pass to read (the domain publishes no policy, or the MTA did not evaluate it): an
    # ALIGNED DKIM or SPF pass is what DMARC itself would have accepted.
    for r in results:
        if r["result"] != "pass":
            continue
        if r["method"] == "dkim" and _aligned(from_domain, r["props"].get("header.d", "")):
            return True, "dkim=pass, aligned"
        if r["method"] == "spf" and _aligned(from_domain, _domain(r["props"].get("smtp.mailfrom", ""))):
            return True, "spf=pass, aligned"
    return False, "no DMARC pass and no aligned DKIM or SPF pass for the From: domain"


# ── in-app DKIM + DMARC ───────────────────────────────────────────────────────────────────────────

DNS_TIMEOUT_S = 5


def dns_txt(name: str) -> list[str]:
    """The TXT strings at `name` (each record's chunks joined), [] when there is none. Raises on a
    resolver failure, which every caller turns into a refusal."""
    import dns.exception
    import dns.resolver
    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=DNS_TIMEOUT_S)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as e:
        raise OSError(f"DNS lookup for {name} failed: {type(e).__name__}") from None
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def dmarc_record(from_domain: str, txt) -> tuple[str, dict]:
    """``(domain, tags)`` of the DMARC record that governs `from_domain`: `_dmarc.<domain>` at the
    domain itself, else at each parent up to the two-label one (the DNS tree walk). ``("", {})``
    when no domain on the way publishes one."""
    labels = from_domain.strip(".").split(".")
    for i in range(0, max(len(labels) - 1, 1)):
        domain = ".".join(labels[i:])
        for rec in txt("_dmarc." + domain):
            if rec.strip().lower().startswith("v=dmarc1"):
                tags = {}
                for part in rec.split(";"):
                    k, _, v = part.partition("=")
                    if k.strip():
                        tags[k.strip().lower()] = v.strip().lower()
                return domain, tags
    return "", {}


def dkim_dmarc(from_domain: str, raw: bytes, txt) -> tuple[bool, str]:
    """Verify `raw`'s DKIM signatures here and apply DMARC's DKIM alignment to `from_domain`.

    A pass is one signature that verifies against the key its `d=` publishes, signs the whole
    body (no `l=`: a partial-body signature leaves whatever follows unsigned, and that text would
    reach a prompt), and whose `d=` aligns with the `From:` domain: relaxed by default, exact when
    the DMARC record says `adkim=s`. Anything else, including a DNS failure, is not a pass."""
    import dkim

    if not raw:
        return False, "in-app: the message bytes are not available to verify"
    try:
        _org, policy = dmarc_record(from_domain, txt)
    except Exception as e:  # noqa: BLE001 — any lookup failure is a refusal, named
        return False, f"in-app: the DMARC lookup failed ({e})"
    strict = policy.get("adkim") == "s"

    def dnsfunc(name, timeout=DNS_TIMEOUT_S):
        found = txt(name.decode("ascii", "replace") if isinstance(name, bytes) else str(name))
        return found[0].encode() if found else None

    d = dkim.DKIM(raw, minkey=1024, timeout=DNS_TIMEOUT_S)
    sigs = sum(1 for k, _v in d.headers if k.lower() == b"dkim-signature")
    if not sigs:
        return False, "in-app: the message carries no DKIM signature"
    seen = []
    for idx in range(sigs):
        try:
            ok = d.verify(idx=idx, dnsfunc=dnsfunc)
        except Exception as e:  # noqa: BLE001 — dkimpy raises on malformed input; not a pass
            seen.append(f"signature {idx + 1}: {type(e).__name__}")
            continue
        signer = (getattr(d, "domain", b"") or b"").decode("ascii", "replace").lower()
        if not ok:
            seen.append(f"d={signer}: does not verify")
            continue
        if b"l" in (getattr(d, "signature_fields", {}) or {}):
            seen.append(f"d={signer}: signs only part of the body (l=)")
            continue
        aligned = signer.strip(".") == from_domain.strip(".") if strict \
            else _aligned(from_domain, signer)
        if aligned:
            return True, f"in-app: dkim=pass d={signer}, aligned" + (" (adkim=s)" if strict else "")
        seen.append(f"d={signer}: not aligned with {from_domain}"
                    + (" under adkim=s" if strict else ""))
    return False, "in-app: no aligned DKIM pass (" + "; ".join(seen) + ")"


def allow_domains(self_addr: str, rules: dict | None = None) -> set:
    """The inbound allow-list, in the order every rule in this engine resolves:
    `_global/POLICIES.md` (`mail_domains`, the admin account's answer) -> `VEXA_FLOWS_MAIL_DOMAINS`
    (the operator's) -> the mailbox's own domain. `rules` is the resolved policy dict when the
    caller already holds one; absent, the env and the fallback decide exactly as before."""
    page = tuple((rules or {}).get("mail_domains") or ())
    if page:
        return {str(d).strip().lower().lstrip("@") for d in page if str(d).strip()}
    return cfg.domains("VEXA_FLOWS_MAIL_DOMAINS", fallback=(self_addr or ""))


def in_allow_list(email: str, self_addr: str, allowed: set | None = None) -> bool:
    """Is this address inside the deployment's own perimeter? Domain only — a per-address list is
    a different control and this is not a spam filter."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    allowed = allow_domains(self_addr) if allowed is None else allowed
    return bool(allowed) and e.rsplit("@", 1)[-1] in allowed


# ── the durable record ────────────────────────────────────────────────────────

def quarantine(db, *, ext_id: str, frm: str, kind: str, reason: str,
               facts: dict | None = None, at: float | None = None) -> None:
    """Record one refusal. Keyed by the inbound message id so a re-scan of the poller's lookback
    window writes the row once — the same idempotence `mail_seen` gives the cursor."""
    db.execute(
        """INSERT INTO mail_quarantine (ext_id, from_addr, kind, reason, facts, at)
           VALUES (:e,:f,:k,:r,:x,:t) ON CONFLICT DO NOTHING""",
        {"e": str(ext_id), "f": (frm or "").strip().lower(), "k": kind, "r": reason[:500],
         "x": json.dumps(facts or {}, sort_keys=True), "t": float(at if at is not None else time.time())})


def already_answered(db, frm: str) -> bool:
    """Has this sender had the fixed line back? Once per address for as long as its quarantine rows
    are kept (`VEXA_FLOWS_FACT_RETENTION_DAYS`, `flows.retention`) — a stranger who keeps writing
    gets rows, not replies, and a mail loop between two auto-responders cannot start."""
    return bool(db.execute("SELECT 1 FROM mail_quarantine WHERE from_addr = :f AND replied_at IS NOT NULL",
                           {"f": (frm or "").strip().lower()}))


def mark_answered(db, ext_id: str, at: float | None = None) -> None:
    db.execute("UPDATE mail_quarantine SET replied_at = :t WHERE ext_id = :e",
               {"t": float(at if at is not None else time.time()), "e": str(ext_id)})


# ── the ceiling on what one inbox can cost ────────────────────────────────────

def rate_check(db, frm: str, now: float | None = None) -> str:
    """"" when this mail may cause an agent turn, else the reason it may not.

    TWO ceilings, because they answer different questions. Per-sender bounds what ONE
    correspondent can spend — including a legitimate user whose own mail client has gone into a
    loop. Global bounds what the inbox can spend in total, which is the number that matters when
    the sender is a hundred addresses inside the allow-list rather than one.

    Counted over a sliding window of admitted mail turns, not of received mail: a message that was
    quarantined or ignored never entered the count, so a flood of strangers cannot exhaust the
    budget of the people who are allowed to use this.
    """
    now = float(now if now is not None else time.time())
    window = max(cfg.get_int("VEXA_FLOWS_MAIL_RATE_WINDOW_S"), 1)
    since = now - window
    per = max(cfg.get_int("VEXA_FLOWS_MAIL_RATE_PER_SENDER"), 0)
    glob = max(cfg.get_int("VEXA_FLOWS_MAIL_RATE_GLOBAL"), 0)
    f = (frm or "").strip().lower()
    if per:
        mine = db.execute("SELECT COUNT(*) FROM mail_turn WHERE from_addr = :f AND at >= :s",
                          {"f": f, "s": since})
        if mine and int(mine[0][0]) >= per:
            return (f"per-sender rate limit: {f} has caused {int(mine[0][0])} mail-triggered turns "
                    f"in the last {window}s (limit {per})")
    if glob:
        allc = db.execute("SELECT COUNT(*) FROM mail_turn WHERE at >= :s", {"s": since})
        if allc and int(allc[0][0]) >= glob:
            return (f"global rate limit: {int(allc[0][0])} mail-triggered turns in the last "
                    f"{window}s (limit {glob})")
    return ""


def record_turn(db, ext_id: str, frm: str, now: float | None = None) -> None:
    """One admitted mail turn, for the window above. Keyed by the message so the poller's lookback
    re-scan cannot inflate a sender's count and lock them out of their own inbox."""
    db.execute("""INSERT INTO mail_turn (ext_id, from_addr, at) VALUES (:e,:f,:t)
                  ON CONFLICT DO NOTHING""",
               {"e": str(ext_id), "f": (frm or "").strip().lower(),
                "t": float(now if now is not None else time.time())})
