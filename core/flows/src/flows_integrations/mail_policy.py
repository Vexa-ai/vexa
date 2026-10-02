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


def allow_domains(self_addr: str) -> set:
    """The inbound allow-list: the declared value, else the mailbox's own domain."""
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
    """Has this sender ever had the fixed line back? Once per address, ever — a stranger who keeps
    writing gets rows, not replies, and a mail loop between two auto-responders cannot start."""
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
