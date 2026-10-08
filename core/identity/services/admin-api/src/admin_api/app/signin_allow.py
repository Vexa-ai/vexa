"""WHO MAY SIGN IN — the instance's sign-in allow-list, and the one decision every door asks.

WHY THIS EXISTS. Removing the company-layer gate (Vexa-ai/vexa#1783, founder ruling 2026-10-08)
removed the only thing that controlled who could sign in. Every sign-in door — the emailed link,
Google, Microsoft — ends in find-or-create, so anybody who could finish one got an account, an API
token, agent turns on the instance's model credentials and bot launches. A self-hosted instance
must not be open to anyone with an email address by default.

THE RULE. A sign-in is admitted when the address is ONE of:

  * an ADMIN — the claimed admin here, or an address in `VEXA_ADMIN_EMAILS`;
  * an EXISTING USER of this instance — so upgrading locks nobody out who already has an account;
  * on the ALLOW-LIST — `VEXA_SIGNIN_ALLOW` (the deployment's seed) plus the `signin.allow`
    platform setting the admin edits in the terminal's Settings. The effective list is the union.

…and, on an instance nobody has claimed and NOTHING has been configured for (no admin emails, no
allow-list), a sign-in that presents the instance's one-time ADMIN CLAIM CODE (`app/claim_code.py`),
because that sign-in is the admin claim. Without the code nobody new gets in: the first visitor to an
exposed instance is a stranger, not its owner. Configuring either list closes that door entirely:
then only those addresses get in. The same inputs decide who may CLAIM the admin role (`may_claim`):
nobody while `VEXA_ADMIN_EMAILS` names the admins; otherwise only with the claim code, and only an
allowed address while an allow-list is configured.

ONE PLACE. Every input to both decisions — the user rows, the claimed admin, both lists — is read by
this service, and the terminal only asks (`POST /internal/signin-admission`,
`POST /internal/bootstrap-admin`) and obeys. An operator configures sign-in on admin-api alone.

ENTRIES are exact addresses (`alice@example.com`) and whole domains (`@example.com`). A domain entry
matches that domain exactly — a subdomain needs its own entry — and an exact entry matches that
address exactly (no plus-address folding). Case never matters. There is deliberately no wildcard:
"everybody" is not a list.

THIS MODULE IS PURE. `app/main.py` owns the one door that asks (`POST /internal/signin-admission`)
and the settings row; everything that can be decided without a database is decided here, so it is
provable with no docker.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Tuple

ENV_KEY = "VEXA_SIGNIN_ALLOW"
ADMIN_EMAILS_ENV = "VEXA_ADMIN_EMAILS"
SETTING_KEY = "signin"
SETTING_FIELD = "allow"
SETTING_FIELDS = (SETTING_FIELD,)

# The admission reasons. Named, so a log line and a test read the same word.
WHY_ADMIN = "admin"
WHY_ADMIN_EMAIL = "admin-email"
WHY_EXISTING_USER = "existing-user"
WHY_ALLOW_LIST = "allow-list"
WHY_CLAIM_CODE = "claim-code"
WHY_NOT_ALLOWED = "not-allowed"

# The claim outcomes (`may_claim` and `POST /internal/bootstrap-admin`).
CLAIMED = "claimed"
CLAIM_ADMIN_EXISTS = "admin-exists"
CLAIM_NOT_ALLOWED = "not-allowed"
CLAIM_BAD_CODE = "bad-code"

# Bounds. An address is at most 254 characters (RFC 5321); a list longer than this is a directory,
# and a domain entry is the tool for that.
MAX_ENTRY_LEN = 254
MAX_ENTRIES = 1000

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
_LOCAL_RE = re.compile(r'^[^\s@,;<>"]+$')
# Separators an operator plausibly types between entries: commas, semicolons, any whitespace
# (so one-per-line from a textarea works the same as an env var on one line).
_SPLIT_RE = re.compile(r"[\s,;]+")


class InvalidAllowList(ValueError):
    """At least one entry is neither an address nor an @domain. Carries every problem, so the
    caller can say all of them at once rather than one per round-trip."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def split_entries(raw) -> List[str]:
    """The raw entries, lower-cased and trimmed, in the order written. Accepts a string (any of the
    separators above) or a list of strings."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts: Iterable[str] = (str(x) for x in raw)
    else:
        parts = _SPLIT_RE.split(str(raw))
    return [p.strip().lower() for p in parts if p and p.strip()]


def entry_problem(entry: str) -> Optional[str]:
    """None when `entry` is a valid allow-list entry, else one sentence saying what is wrong."""
    if len(entry) > MAX_ENTRY_LEN:
        return f"{entry[:40]!r}… is longer than {MAX_ENTRY_LEN} characters"
    if entry.startswith("@"):
        return None if _DOMAIN_RE.match(entry[1:]) else (
            f"{entry!r} is not a domain entry (write a domain as @example.com)")
    local, at, domain = entry.rpartition("@")
    if not at:
        if _DOMAIN_RE.match(entry):
            return (f"{entry!r} has no @ — write @{entry} to allow everyone at that domain, "
                    f"or a full address to allow one person")
        return f"{entry!r} is neither an email address nor an @domain entry"
    if not local or not _LOCAL_RE.match(local) or not _DOMAIN_RE.match(domain):
        return f"{entry!r} is not a valid email address"
    return None


def parse(raw) -> Tuple[List[str], List[str]]:
    """(valid entries, de-duplicated in the order written; one sentence per invalid entry)."""
    valid: List[str] = []
    problems: List[str] = []
    seen = set()
    for entry in split_entries(raw):
        problem = entry_problem(entry)
        if problem:
            problems.append(problem)
        elif entry not in seen:
            seen.add(entry)
            valid.append(entry)
    return valid, problems


def normalize_setting(raw) -> str:
    """The canonical stored form of an admin-written list: valid entries joined by ", ".

    ALL-OR-NOTHING: one bad entry refuses the whole write. Storing the good half and dropping the
    rest would answer 200 for a list that is not the one the admin typed — the same silent partial
    write `PUT /internal/settings/{key}` already refuses for unknown fields."""
    valid, problems = parse(raw)
    if problems:
        raise InvalidAllowList(problems)
    if len(valid) > MAX_ENTRIES:
        raise InvalidAllowList([f"{len(valid)} entries — at most {MAX_ENTRIES}; "
                                f"use an @domain entry for a whole organisation"])
    return ", ".join(valid)


def env_entries() -> Tuple[List[str], List[str]]:
    """The deployment's seed, `VEXA_SIGNIN_ALLOW`, read on every call (so a test or an operator
    sees the value now in the environment, not the one at import). An invalid entry never matches
    anything; it is reported, never guessed into a domain."""
    return parse(os.getenv("VEXA_SIGNIN_ALLOW", ""))


def effective(env: Iterable[str], setting_value) -> List[str]:
    """The effective list: the env entries plus the settings entries, de-duplicated. Invalid stored
    entries (only possible if the row was written around the validator) are dropped, never
    widened."""
    stored, _ = parse(setting_value)
    out: List[str] = []
    seen = set()
    for entry in list(env) + stored:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def normalize_email(email) -> str:
    return str(email or "").strip().lower()


def is_address(email: str) -> bool:
    """The minimal shape every sign-in door already enforces: something@domain.tld, no spaces."""
    local, at, domain = email.rpartition("@")
    return bool(at and local and "." in domain and not any(c.isspace() for c in email))


def matches(email, entries: Iterable[str]) -> bool:
    """Does this address match an exact entry, or the @domain entry for its domain?"""
    e = normalize_email(email)
    if not is_address(e):
        return False
    allowed = set(entries)
    return e in allowed or ("@" + e.rpartition("@")[2]) in allowed


def admin_email_problem(entry: str) -> Optional[str]:
    """None when `entry` is a usable admin address. An admin is one person: a domain entry would make
    everybody at that domain an administrator, so it is refused rather than widened."""
    if entry.startswith("@"):
        return f"{entry!r} is a domain — {ADMIN_EMAILS_ENV} names people, one full address each"
    return entry_problem(entry)


def admin_emails() -> Tuple[List[str], List[str]]:
    """The administrators the deployment names, `VEXA_ADMIN_EMAILS` (comma-separated exact
    addresses), read on every call. (valid addresses; one sentence per invalid entry)."""
    valid: List[str] = []
    problems: List[str] = []
    for entry in split_entries(os.getenv(ADMIN_EMAILS_ENV, "")):
        problem = admin_email_problem(entry)
        if problem:
            problems.append(problem)
        elif entry not in valid:
            valid.append(entry)
    return valid, problems


def is_admin(email, data, admins: Iterable[str]) -> bool:
    """THE admin test: the claimed role on the user row, or an address the deployment names."""
    claimed = isinstance(data, dict) and data.get("is_admin") is True
    return claimed or normalize_email(email) in set(admins)


def decide(email, *, user_exists: bool, is_admin: bool, admin_claimed: bool,
           allow: Iterable[str], admins: Iterable[str] = (),
           claim_code_ok: bool = False) -> Tuple[bool, str]:
    """(admitted, why). The order only decides which reason is reported, except for the last row:
    an instance nobody has claimed and nothing has been configured for admits a sign-in that holds
    the claim code, because that sign-in is the claim. `is_admin` is the claimed role on the user
    row; `admins` is `VEXA_ADMIN_EMAILS`; `claim_code_ok` says the sign-in presented the live code."""
    e = normalize_email(email)
    if not is_address(e):
        return False, WHY_NOT_ALLOWED
    admins = set(admins)
    allow = list(allow)
    if is_admin:
        return True, WHY_ADMIN
    if e in admins:
        return True, WHY_ADMIN_EMAIL
    if user_exists:
        return True, WHY_EXISTING_USER
    if matches(e, allow):
        return True, WHY_ALLOW_LIST
    if not admin_claimed and not admins and not allow and claim_code_ok:
        return True, WHY_CLAIM_CODE
    return False, WHY_NOT_ALLOWED


def may_claim(email, *, admin_claimed: bool, allow: Iterable[str],
              admins: Iterable[str] = (), code_ok: bool = False) -> Tuple[bool, str]:
    """(may this user take the admin role now, why). Nobody, once an admin is claimed or while
    `VEXA_ADMIN_EMAILS` names the admins — those addresses already are. Otherwise only with the live
    claim code (`code_ok`): no code, no claim. And while an allow-list is configured, only an address
    on it: the first admin of a closed instance is one of its people."""
    if admin_claimed or list(admins):
        return False, CLAIM_ADMIN_EXISTS
    if not code_ok:
        return False, CLAIM_BAD_CODE
    allow = list(allow)
    if allow and not matches(email, allow):
        return False, CLAIM_NOT_ALLOWED
    return True, CLAIMED
