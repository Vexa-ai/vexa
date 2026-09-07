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

    # ── service endpoints: THE DOORS ────────────────────────────────────────
    #
    # NO HOST-PORT DEFAULTS, and this is a correctness rule, not tidiness. `http://localhost:18057`
    # is not "unconfigured" — on any host that runs more than one stack it is A DIFFERENT
    # DEPLOYMENT'S admin-api, and a default that silently names one is worse than no default at
    # all: found live on 2026-09-03, when a bare `pytest` run of `test_admin_user_lookup_shapes`
    # talked to a NEIGHBOURING stack's admin-api and read its 403 as this stack's answer. A door
    # that is
    # not configured must REFUSE, so the operator (or the test) is told which deployment it is
    # missing rather than being handed somebody else's.
    #
    # A deployment default may name the SERVICE (`http://admin-api:8057`, which resolves only
    # inside the deployment's own network) and lives in compose/helm, never here.
    # THE MEETINGS DOOR (PRD decision 40.7 + decision 5, founder-agreed: flows → meetings is a
    # declared OPTIONAL dependency that degrades). `capability`, which is what the class means
    # here: unset is not a misconfiguration, it is a deployment that runs no meetings domain, and
    # every step that would schedule or read a bot answers `not_present` instead of knocking.
    # `required-explicit` was a refusal to BOOT — `preflight()` names the door and exits before
    # anything about meetings is asked, so a flows+identity deployment could not start at all.
    # The key still says GATEWAY because flows reaches meetings through the edge today; that hop
    # is ADR-0037's to close and is not this class change (see flows_steps.common.MEETINGS_DOOR).
    "VEXA_FLOWS_GATEWAY_URL": ("capability", None,
                               "the meetings domain, reached through the gateway. UNSET MEANS THE "
                               "MEETINGS DOMAIN IS NOT DEPLOYED — see "
                               "flows_steps.common.domain_present."),
    "VEXA_FLOWS_ADMIN_API_URL": ("required-explicit", None, "admin-api's admin tier."),
    # THE LINK PORT, and `capability` is what makes it one (PRD decision 4, founder 2026-09-03
    # 09:56Z: *"fine as a port + adapter (P16): flows owns a link port, the terminal is one
    # adapter, the no-agents product has none"*). It was `required-explicit`, which made a
    # STEP-TIME link decide whether the process could BOOT: `preflight()` refused, so flows-api
    # crash-looped on the compose network before it could serve /health or its tool manifest — and
    # the no-agents product has no terminal to name. Unset is therefore a supported deployment,
    # exactly as the agent door's absence is, and it is still not a silent one: `require` refuses
    # at the moment a link would have been composed, naming this key (`ui_link`, `mint_scaffold`).
    # There is still NO DEFAULT — a localhost default is a dead button in every deployment that is
    # not a laptop, which is the reason the door rule exists at all.
    "VEXA_UI_URL": ("capability", None,
                    "where a person's own terminal lives — it goes into every link we mail. UNSET "
                    "MEANS THIS DEPLOYMENT HAS NO TERMINAL ADAPTER: nothing is mailed with a link "
                    "and any step that would compose one refuses, naming this key."),
    # THE AGENT DOMAIN'S PRESENCE SIGNAL (PRD decision 40.7: *"meetings, agents and flows work
    # independently and together in any configuration"*). `capability`, which is exactly what the
    # class means here — unset is not a misconfiguration, it is the `no-agents` profile, and every
    # flow step that would dispatch an agent turn reports `not_present` instead of knocking on a
    # door that is not there. `defaulted` could not express that: a default URL asserts the domain
    # exists, and the absence then arrives as a connection error that retries forever.
    "VEXA_FLOWS_AGENT_API_URL": ("capability", None,
                                 "agent-api's internal tier. UNSET MEANS THE AGENT DOMAIN IS NOT "
                                 "DEPLOYED — see flows_steps.common.domain_present."),
    "VEXA_FLOWS_DB_URL": (
        "required-explicit", None,
        "the engine's Postgres DSN — the two tables the whole engine is, and the ONLY dialect "
        "(2026-09-03): `db_from_url` refuses anything that does not name Postgres, naming the "
        "scheme it saw. It USED to fall back to reading a password out of a named container on "
        "one developer's host (decision 18d); a guessed DSN on a machine that runs more than one "
        "stack does not fail, it addresses somebody else's data, which is the "
        "`localhost:18057` bug in the DOORS block above with a password on the end. "
        "gate:health needs no database at all now — `postgres_db` is lazy (connects and applies "
        "schema on first real use), so the app composes and answers `/health` before any DB is "
        "reachable; the offline/storm dialect (`SqliteDB`) is a TEST double in "
        "core/flows/tests/sqlite_double.py, constructed directly there and never through this "
        "key."),
    "VEXA_FLOWS_API_PORT": ("defaulted", "18200", "the port flows-api binds."),
    "VEXA_FLOWS_API_HOST": (
        "defaulted", "127.0.0.1",
        "the interface flows-api binds. Loopback by default because this process also runs as a "
        "HOST LANE on the dogfood rig, where 0.0.0.0 would publish its port on every interface of "
        "that box. The compose service sets 0.0.0.0 in its own environment — in a container "
        "loopback is that container's own, so nothing else on the network can reach it, which is "
        "why the interim wiring had to bind the lane to the docker bridge address by hand."),

    # ── the mailbox: which inbox, and what it will answer ───────────────────
    "VEXA_MAIL_INBOX": ("defaulted", "imap", "`imap` (real) or `mailpit` (the dev double)."),
    # `capability`, not `required-explicit` — the ruling on the #1479 x #1483 collision (P14: a
    # capability is optional BY DEFINITION, and a deployment may carry no mail intake at all). The
    # pair stays grouped under `mailbox` in `all` mode, so a set address with no password is
    # MISCONFIGURED — which is the check #1483 wanted, enforced inside the capability rather than
    # at boot. Decision 18(c) holds either way: the `sops -d` vault path is gone from `emailx.creds`
    # regardless of class. VEXA_FLOWS_DB_URL went the OTHER way and is `required-explicit` — flows
    # without a database is not a deployment — so the `own_database` capability is gone.
    # WHERE THESE CLASSES BITE, precisely: flows-api’s runtime entrypoint calls BOTH validators —
    # this module’s own `preflight`, which enforces DOOR_KEYS, and the vendored `config_preflight`,
    # which enforces the whole `config.v1.json` declaration (F-D20 b). It did not always: this
    # comment used to end *“and nothing under core/flows/src imports the vendored
    # config_preflight”*, which was true, and meant flows-api’s own boot refusal was a hand-written
    # placeholder list three literals shorter than the declared one — so the service booted on
    # `vexa-internal-secret`. `required-explicit` here was never merely documentary; it is now not
    # documentary anywhere.
    "VEXA_MAIL_ADDR": (
        "capability", None,
        "the address this deployment's mailbox answers as, and the identity every allow-list is "
        "anchored on. Unset = no mail intake; set without VEXA_MAIL_APP_PASSWORD is the "
        "half-configured mailbox the capability's `all` mode calls misconfigured. THERE IS NO "
        "VAULT BEHIND IT: `emailx.creds` used to shell out to `sops -d` against a path in one "
        "developer's home directory when this was unset (decision 18c)."),
    "VEXA_MAIL_APP_PASSWORD": (
        "capability", None,
        "the IMAP/SMTP credential paired with VEXA_MAIL_ADDR, and a P14 SECRET (see SECRETS "
        "below): the deploy surface feeds it from a secret store, the service never reads one. "
        "Required WITHIN the capability, not at boot: `mailbox` is `all` mode, so a set address "
        "with no password is misconfigured rather than optional."),
    "VEXA_MAIL_SMTP_HOST": ("capability", None, "unset = Gmail SMTP over SSL; set = a plain host (the mail double)."),
    "VEXA_MAIL_SMTP_PORT": ("defaulted", "25", "the port for a set VEXA_MAIL_SMTP_HOST."),
    "VEXA_MAILPIT_URL": ("defaulted", "http://127.0.0.1:8025", "mailpit's HTTP base, when the inbox is mailpit."),
    "VEXA_MAILPIT_LOOKBACK_S": ("defaulted", "300", "re-scan window behind the mailpit watermark."),
    "VEXA_NOTIFY_CHANNEL": ("defaulted", "smtp",
                            "where a notification goes: `smtp` or `graph` (Microsoft 365 with "
                            "SMTP AUTH off). Anything else is refused loudly."),
    # ── Microsoft 365 / Graph mailbox ───────────────────────────────────────
    #
    # FOUR KEYS, ONE CAPABILITY, AND NONE OF THEM CAN BE `required-explicit` — which is a fact
    # about the contract, not a preference. `required-explicit` refuses the BOOT when a key is
    # unset, so declaring the client secret that way would stop every deployment that is not on
    # Microsoft from starting. config.v1 has no way to say "required only when another key
    # selects this path": the class is a property of the key, evaluated against the environment,
    # and there is no conditional in the schema (`deploy/contracts/config.v1/README.md`).
    #
    # `capability` with mode=all is the closest true statement and it is not a weaker one: all
    # four set = `configured`, none set = `not_configured` (this deployment is not on Microsoft),
    # SOME set = `misconfigured`, surfaced by name on /health. The refusal for the selected path
    # then happens where it can name the keys — `graph_client.GraphClient.__init__`, at the
    # moment the mailbox is constructed, exactly as `emailx.creds` refuses a half-configured
    # SMTP pair.
    "VEXA_GRAPH_TENANT_ID": ("capability", None,
                             "the Microsoft Entra tenant the mailbox lives in."),
    "VEXA_GRAPH_CLIENT_ID": ("capability", None,
                             "the app registration's application (client) id."),
    "VEXA_GRAPH_CLIENT_SECRET": (
        "capability", None,
        "the app registration's client secret, and a P14 SECRET. Application permissions "
        "Mail.ReadWrite + Mail.Send with admin consent, and an Exchange ApplicationAccessPolicy "
        "scoping the app to the ONE mailbox — without that policy this credential reads every "
        "mailbox in the tenant."),
    "VEXA_GRAPH_MAILBOX": ("capability", None,
                           "the mailbox address Graph polls and sends as (`vexa@customer.tld`)."),
    "VEXA_GRAPH_BASE": ("defaulted", "https://graph.microsoft.com/v1.0",
                        "the Graph endpoint — an in-code dial for a national cloud."),
    "VEXA_GRAPH_LOGIN": ("defaulted", "https://login.microsoftonline.com",
                         "the token endpoint — the same dial, same reason."),
    "VEXA_GRAPH_PAGE_SIZE": ("defaulted", "25", "`$top` on the inbox listing."),

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
    "VEXA_FLOWS_DEFS_EXTRA": (
        "capability", None,
        "flow packs this deployment composes on top of the ones in this repo — importable module "
        "names, comma-separated, each exposing `build(reg, db)`, called last by "
        "`flows_defs.production.build`. It is to STEPS what `VEXA_BEHAVIOR_DIR` above is to "
        "words: the vocabulary stays closed to the API (`flow_by_names` refuses a step name the "
        "image has not got, deliberately — the API never accepts code) and open to the operator. "
        "Unset is this repo's own product; nothing here ships dark. A named module that will not "
        "import REFUSES the boot rather than falling back, because a deployment that declares a "
        "pack and starts without it reacts to none of that pack's events, silently, for as long "
        "as nobody looks."),
    "VEXA_JITSI_HOSTS": ("capability", None, "extra Jitsi hosts a meeting link may live on, beyond meet.jit.si."),
    "VEXA_FLOWS_INSTANCE_GATE": ("capability", None, "forces the instance gate open or shut, for the rig."),
    "VEXA_FLOWS_USER_KEY_TTL_S": (
        "defaulted", "900",
        "how long a minted gateway token lives AND how long this process reuses it. One 20-person "
        "meeting used to leave ~30 permanent full-scope tokens on the organiser's account (R-B13)."),
    "VEXA_TIMELINE_SCAN_ROWS": ("defaulted", "2000", "how many reaction rows the timeline projection scans."),
}


#: THE CREDENTIALS (P14). A value named here is never logged, never written into a golden, and
#: never appears in an error message: every refusal below names the KEY, exactly as
#: `common.require_admin_key` does one file over. It is a SET rather than a fourth class because
#: secrecy is orthogonal to necessity — `VEXA_FLOWS_TIMELINE_KEY` is a capability AND a secret,
#: `VEXA_MAIL_ADDR` is required and public. The deploy surface feeds each of these from a secret
#: store; a service that reads a secret store itself is the shape decision 18(c) removed.
SECRETS = frozenset({
    "INTERNAL_API_SECRET", "VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET",
    "VEXA_FLOWS_ADMIN_KEY", "VEXA_FLOWS_API_KEY", "VEXA_FLOWS_TIMELINE_KEY",
    "VEXA_MAIL_APP_PASSWORD",
})

#: The mail capability's keys, in one place so a half-declared control is visible as one.
MAIL_KEYS = ("VEXA_MAIL_ADDR", "VEXA_MAIL_APP_PASSWORD", "VEXA_MAIL_SMTP_HOST",
             "VEXA_MAIL_SMTP_PORT")


def _decl(name: str) -> tuple[str, object, str]:
    try:
        return DECLARED[name]
    except KeyError:
        raise KeyError(
            f"{name} is not declared in flows_config.DECLARED — a key nobody declared is a key "
            "nobody can deploy. Add it to the table with its class and its why.") from None


class ConfigError(RuntimeError):
    """A door this deployment must name and did not (see the DOORS block above)."""


#: The service endpoints flows reaches. `required-explicit` ones must be named by the deployment;
#: the agent door and the link port are capabilities, because their absence is a supported product
#: configuration — no agent domain (40.7) and no terminal adapter (decision 4) respectively.
#: `missing_doors` filters on the CLASS, so a door moves between the two lists by reclassification
#: in the table above and nowhere else.
DOOR_KEYS = ("VEXA_FLOWS_GATEWAY_URL", "VEXA_FLOWS_ADMIN_API_URL", "VEXA_UI_URL",
             "VEXA_FLOWS_AGENT_API_URL")
# `missing_doors` filters this tuple on the CLASS, so the two capability doors (meetings, agent)
# drop out of the preflight by the same line that declares them optional — there is no second list
# to keep in step, which is how the two would drift.


def require(name: str) -> str:
    """The declared key, or `ConfigError` naming it. For a door: refuse rather than guess.

    `get` answers "" for an unset required-explicit key, which is the right shape for a caller that
    wants to test emptiness. A DOOR has no such caller: every use of one is about to become a URL
    in an HTTP request, and an empty base silently produces a relative URL."""
    _cls, _default, why = _decl(name)
    value = get(name)
    if not value:
        raise ConfigError(f"{name} is unset — {why} There is no default: a host-port default names "
                          f"whatever else happens to be listening on this machine. Set it.")
    return value


def missing_doors() -> list[str]:
    """Which required doors this process cannot name. The boot preflight, and what the test asserts."""
    out = []
    for key in DOOR_KEYS:
        cls, _default, _why = _decl(key)
        if cls == "required-explicit" and not get(key):
            out.append(key)
    return out


def preflight() -> None:
    """Refuse to run with a door unnamed. Called at the entrypoints, and by anything that is about
    to talk to a service."""
    missing = missing_doors()
    if missing:
        raise ConfigError("this flows deployment cannot name " + ", ".join(missing)
                          + " — set each to the service it should reach "
                            "(e.g. http://admin-api:8057). There are no host-port defaults: one "
                            "would silently address a different stack on the same host.")


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
