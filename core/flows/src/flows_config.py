"""THE CONFIG CONTRACT for `core/flows` — every environment key this brick reads, declared once.

`gate:config-contract` covers five adopted services and `core/flows` is not one of them (seam
backlog B7/B9: "the config-contract gate is green over less than half the seam"). Adopting flows
into `config.v1` is that structural item and it is not this file. What this file IS, is the thing
that makes the adoption mechanical when it happens, and that closes the hole in the meantime: ONE
table of every key, both directions asserted by a test — nothing read that is not declared, and
nothing declared that is not read.

Both directions matter and only one of them is the usual one. `read ⊆ declared` catches the key
somebody added in a hurry; `declared ⊆ read` catches the key whose reader was deleted — the shape
that leaves an operator setting a value that reaches nothing (R-E18: three `VEXA_LLM_*` keys
advertised, read by the runner, forwarded by no dispatch).

Three classes, the same three the `config.v1` contract uses, so the adoption is a transcription:

  required-explicit  unset or empty ⇒ the process refuses to start. `default` is None.
  defaulted          the documented code default applies; the default lives HERE, next to the why.
  capability         the key belongs to a feature that is simply off when it is unset.

Reading a key through `get`/`get_int`/`get_bool`/`domains` is what makes the declaration binding:
the accessors refuse a name that is not in the table, so a typo is a crash at the read rather than
a silent empty string. The historical constants in `flows_steps/common.py`,
`flows_integrations/inbox.py` and friends still read `os.environ` directly — they are declared
here and the test proves it; converting them is hygiene, not security, and it is not this branch.
"""
from __future__ import annotations

import os

# name -> (class, default, why). `default` is the value a reader gets when the key is unset;
# None for required-explicit, and for capability keys whose absence IS the off state.
DECLARED: dict[str, tuple[str, object, str]] = {
    # ── identity and secrets ────────────────────────────────────────────────
    "INTERNAL_API_SECRET": (
        "required-explicit", None,
        "the internal-tier identity flows presents to agent-api's meeting room — the ONE name the "
        "whole internal tier uses since F95. No default: a weak default makes an unconfigured "
        "deployment look configured (common.require_internal_secret). Read through "
        "`common.INTERNAL_SECRET_ENV`, which is why this table's test also scans `*_ENV` constants "
        "and not only literals."),
    "VEXA_INTERNAL_SECRET": (
        "capability", None,
        "DEPRECATED alias of INTERNAL_API_SECRET (F95) — honoured with a warning for one release, "
        "removed next. Declared so the removal is a deletion here rather than an archaeology."),
    "VEXA_INTERNAL_API_SECRET": (
        "capability", None,
        "the second DEPRECATED alias of INTERNAL_API_SECRET (F95) — same terms as the one above. "
        "One secret with three names is what F95 was; the third name is gone and these two are on "
        "a clock."),
    "VEXA_FLOWS_ADMIN_KEY": (
        "required-explicit", None,
        "the admin-api key flows uses to resolve and create platform users and to mint gateway "
        "tokens. It opens `ensure_platform_user` and `user_api_key`, so it is refused the same way "
        "as the internal secret rather than defaulting to `changeme` (R-B11)."),
    "VEXA_FLOWS_API_KEY": (
        "required-explicit", None,
        "the operator key that gates `POST /flows` and `POST /events` on flows-api — decision 4's "
        "whole access model."),
    "VEXA_FLOWS_TIMELINE_KEY": (
        "capability", None,
        "a read-only key that opens the timeline projection and nothing else. Unset = only the "
        "operator key opens it."),

    # ── service endpoints ───────────────────────────────────────────────────
    "VEXA_FLOWS_GATEWAY_URL": ("defaulted", "http://localhost:18056", "the meetings gateway."),
    "VEXA_FLOWS_AGENT_API_URL": ("defaulted", "http://localhost:18100", "agent-api's internal tier."),
    "VEXA_FLOWS_ADMIN_API_URL": ("defaulted", "http://localhost:18057", "admin-api's admin tier."),
    "VEXA_UI_URL": ("defaulted", "http://localhost:18300", "where a person's own terminal lives."),
    "VEXA_FLOWS_DB_URL": (
        "capability", None,
        "the engine's Postgres DSN. Unset falls back to the dogfood container lookup in "
        "`common.db_url`, which exists for the rig and never for a deployment."),
    "VEXA_FLOWS_API_PORT": ("defaulted", "18200", "the port flows-api binds."),

    # ── the mailbox: which inbox, and what it will answer ───────────────────
    "VEXA_MAIL_INBOX": ("defaulted", "imap", "`imap` (real) or `mailpit` (the dev double)."),
    "VEXA_MAIL_ADDR": (
        "required-explicit", None,
        "the address this deployment's mailbox answers as. It is also the identity every "
        "allow-list is anchored on, so an unset value is not a cosmetic gap."),
    "VEXA_MAIL_APP_PASSWORD": ("capability", None, "the IMAP/SMTP credential paired with VEXA_MAIL_ADDR."),
    "VEXA_MAIL_SMTP_HOST": ("capability", None, "unset = Gmail SMTP over SSL; set = a plain host (the mail double)."),
    "VEXA_MAIL_SMTP_PORT": ("defaulted", "25", "the port for a set VEXA_MAIL_SMTP_HOST."),
    "VEXA_MAILPIT_URL": ("defaulted", "http://127.0.0.1:8025", "mailpit's HTTP base, when the inbox is mailpit."),
    "VEXA_MAILPIT_LOOKBACK_S": ("defaulted", "300", "re-scan window behind the mailpit watermark."),
    "VEXA_NOTIFY_CHANNEL": ("defaulted", "smtp", "where a notification goes; `smtp` is the only real one."),

    # ── WHO THE MAILBOX WILL ACT FOR (R-B12) ────────────────────────────────
    "VEXA_FLOWS_MAIL_DOMAINS": (
        "capability", None,
        "the domain allow-list for INBOUND mail (PRD §16.2: a deployment value; outside the "
        "domain, never). Comma-separated, with or without a leading `@`. UNSET IS NOT `EVERYONE`: "
        "it means the mailbox's own domain, exactly as `VEXA_FLOWS_ATTENDEE_DOMAINS` unset means "
        "the organizer's. A sender who is neither a known user nor inside it gets no account, no "
        "agent turn and no model spend — only a quarantine row."),
    "VEXA_FLOWS_MAIL_QUARANTINE_REPLY": (
        "capability", None,
        "`1` answers a quarantined stranger ONCE with a fixed one-line template — no model, no "
        "account, no thread. Default off: silence is the safest answer to an address we cannot "
        "place, and any automatic reply to an unverified sender is a reflector."),
    "VEXA_FLOWS_MAIL_RATE_PER_SENDER": (
        "defaulted", "12",
        "mail-triggered agent turns one sender may cause per window. Above it the mail is "
        "recorded and dropped."),
    "VEXA_FLOWS_MAIL_RATE_GLOBAL": (
        "defaulted", "120",
        "mail-triggered agent turns the whole deployment may run per window — the ceiling on what "
        "one inbox can cost, whoever sends it."),
    "VEXA_FLOWS_MAIL_RATE_WINDOW_S": ("defaulted", "3600", "the window both mail rate limits count over."),
    "VEXA_FLOWS_MAIL_BODY_MAX": (
        "defaulted", "4000",
        "how much of an inbound body may enter an agent prompt, inside the untrusted block."),

    # ── behaviour and content ───────────────────────────────────────────────
    "VEXA_FLOWS_ATTENDEE_DOMAINS": (
        "capability", None,
        "the OUTBOUND fan-out allow-list (PRD §16.2). Unset = the organizer's own domain."),
    "VEXA_FLOWS_DATA_STATEMENT": ("capability", None, "the deployment's own sentence about where the words live."),
    "VEXA_BEHAVIOR_DIR": ("capability", None, "the private behavior mount; unset uses the in-repo showcase prompts."),
    "VEXA_JITSI_HOSTS": ("capability", None, "extra Jitsi hosts a meeting link may live on, beyond meet.jit.si."),
    "VEXA_FLOWS_INSTANCE_GATE": ("capability", None, "forces the instance gate open or shut, for the rig."),
    "VEXA_FLOWS_FIXTURE_TRANSCRIPT": ("capability", None, "`1` serves the declared fixture transcript instead of the gateway's."),
    "VEXA_FLOWS_USER_KEY_TTL_S": (
        "defaulted", "900",
        "how long a minted gateway token lives AND how long this process reuses it. One 20-person "
        "meeting used to leave ~30 permanent full-scope tokens on the organiser's account (R-B13)."),
    "VEXA_TIMELINE_SCAN_ROWS": ("defaulted", "2000", "how many reaction rows the timeline projection scans."),
}


def _decl(name: str) -> tuple[str, object, str]:
    try:
        return DECLARED[name]
    except KeyError:
        raise KeyError(
            f"{name} is not declared in flows_config.DECLARED — a key nobody declared is a key "
            "nobody can deploy. Add it to the table with its class and its why.") from None


def get(name: str) -> str:
    """The declared key as a stripped string; the declared default when it is unset or empty."""
    _cls, default, _why = _decl(name)
    raw = (os.environ.get(name) or "").strip()
    return raw or ("" if default is None else str(default))


def get_int(name: str) -> int:
    """The declared key as an int. A non-numeric value falls back to the declared default rather
    than crashing a poller mid-flight — the same rule `_room_read_max` states for its own param."""
    _cls, default, _why = _decl(name)
    try:
        return int(get(name))
    except (TypeError, ValueError):
        return int(default) if default is not None else 0


def get_bool(name: str) -> bool:
    """`1`/`true`/`yes`/`on` and nothing else. An unset capability key is OFF."""
    return get(name).lower() in ("1", "true", "yes", "on")


def domains(name: str, fallback: str = "") -> set[str]:
    """A comma-separated domain allow-list, normalised. `fallback` is an ADDRESS or a domain whose
    domain is used when the key is unset — the "unset means our own domain, never everyone" rule
    that PRD §16.2 states and that `_attendees` already implements for the outbound direction."""
    raw = [d for d in get(name).split(",") if d.strip()]
    if not raw and fallback:
        raw = [fallback.rsplit("@", 1)[-1]]
    return {d.strip().lower().lstrip("@") for d in raw if d.strip().lstrip("@")}
