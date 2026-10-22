"""Shared plumbing for production steps: config by env (P14), tiny HTTP, admin/user auth.
STATELESS BY LAW: everything a step needs travels in ctx.refs / ctx.prior — worker restarts
must be invisible (the duplicate-email lesson)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Optional
from urllib.parse import quote as _q

import flows_config
import flows_obs
# THE ONLY MODULE-SCOPE ENGINE IMPORT IN THIS FILE, and it is here because a class statement needs
# its base at definition time (`SettingsUnavailable` below). Everything else keeps the existing
# function-local `from flows import StepError` idiom, which is not stylistic: it keeps the engine
# out of this module's import graph for every caller that only wants `http` or a door.
from flows import StepError

#: The agent domain's door, or "" when the agent domain is not deployed (PRD decision 40.7). Read
#: through the contract, which declares it a CAPABILITY rather than a defaulted URL — see
#: `flows_config`'s DOORS block for why a host-port default is a correctness bug and not a
#: convenience.
AGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL")

#: THE MEETINGS DOOR, and the name is a defect this change does not close. The key still says
#: GATEWAY because flows reaches meetings THROUGH THE EDGE today — eleven call sites, all of them
#: `{gateway}/meetings`, `{gateway}/bots`, `{gateway}/transcripts/…`. ADR-0037 forbids that hop
#: ("fronting a sibling's door with the edge does not make it not-an-edge") and it is a separate
#: change with its own consequences, measured and stated on this PR: the gateway resolves the
#: caller's key and enriches every forward with X-User-Id, X-User-Scopes, X-User-Workspaces and
#: X-User-Limits — and `POST /bots` enforces the per-user concurrent-bot cap out of that last one
#: (`meeting_api/bot_spawn/router.py:208` `_resolve_max_concurrent`). Calling meeting-api directly
#: without carrying those forward would silently drop the cap, so the de-hop is not a rename.
#:
#: What IS decided here (decision 5, founder-agreed) is the DEPENDENCY: meetings is optional, and
#: its absence is a supported configuration rather than a refusal to boot.
MEETINGS_DOOR = "VEXA_FLOWS_GATEWAY_URL"
MEETINGS_API = flows_config.get(MEETINGS_DOOR)


def domain_present(domain: str) -> bool:
    """Is this domain deployed alongside flows? (PRD decision 40.7.)

    *"We want agents service be optional, all domains must work independently and in any
    configuration. Identity is probably the one that everyone depends on… meetings, agents and
    flows — independently and together in any configuration."*

    Presence is a CONFIGURATION FACT, never a probe: a health check would make "the agent-api is
    restarting" and "there is no agent-api" the same answer, and the second is a supported product
    (the `no-agents` profile — decision 40.6) while the first is an outage that must retry. The
    signal is whether the deployment named the door.

    `identity` is always present by construction — it is the one shared dependency 40.7 names, and
    a flows deployment that cannot reach it has no subjects at all. Reads the MODULE attribute so a
    test can set the world with one `monkeypatch.setattr`, the way this suite already does."""
    if domain == "identity":
        return True
    if domain == "agent":
        return bool((AGENT_API or "").strip())
    if domain == "meetings":
        return bool((MEETINGS_API or "").strip())
    return True


class AgentDomainAbsent(RuntimeError):
    """A helper that reaches agent-api was called in a deployment that does not run it.

    THE SECOND LINE OF DEFENCE, and it should never fire: the engine answers `not_present` for a
    step that declared `needs=("agent",)` without entering its body (PRD decision 40.7,
    `flows/loop.tick`). This exists because the first line is a DECLARATION, and a step that
    reaches the agent domain without declaring it would otherwise hand an empty base to urllib and
    get `ValueError: unknown url type: '/api/workspace/file?...'` — an exception about a URL,
    three frames from anything that names the real cause."""


def agent_door() -> str:
    """agent-api's base, or `AgentDomainAbsent`. Reads the MODULE attribute so a test can set the
    world with one `monkeypatch.setattr`, the way this suite already does."""
    base = (AGENT_API or "").strip()
    if not base:
        raise AgentDomainAbsent(
            "this deployment does not run the agent domain (VEXA_FLOWS_AGENT_API_URL is unset). "
            "A flow step that needs it must declare `needs=(\"agent\",)` so the engine answers "
            "`not_present` instead of reaching for a door that is not there.")
    return base.rstrip("/")


class MeetingsDomainAbsent(RuntimeError):
    """A helper that reaches the meetings domain was called in a deployment that does not run it.

    The sibling of `AgentDomainAbsent`, and the same second line of defence: the engine answers
    `not_present` for a step that declared `needs=("meetings",)` without entering its body
    (`flows/loop.tick`), so this should never fire. It exists because the first line is a
    DECLARATION, and the next step somebody adds will not remember to make it.

    Deliberately NOT `flows_config.ConfigError`. That one says *this deployment is misconfigured*,
    and an absent optional domain is a supported configuration — the whole point of the class
    change behind it. A refusal that names the wrong cause sends an operator to fix a door that
    was never supposed to be there."""


def meetings_door() -> str:
    """The meetings base, or `MeetingsDomainAbsent`. Reads the MODULE attribute so a test can set
    the world with one `monkeypatch.setattr`, exactly as `agent_door` does.

    RESOLVED AT ACCESS, never at import. `from .common import GATEWAY` ran `flows_config.require`
    while `flows_steps/meeting.py` was still loading, so an unset door was an ImportError for the
    entire step vocabulary — every step, including the ones with no interest in meetings. An
    optional domain has to be absent-able at boot, at import AND at the step; this is the middle
    one."""
    base = (MEETINGS_API or "").strip()
    if not base:
        raise MeetingsDomainAbsent(
            f"this deployment does not run the meetings domain ({MEETINGS_DOOR} is unset). "
            "A flow step that needs it must declare `needs=(\"meetings\",)` so the engine answers "
            "`not_present` instead of reaching for a door that is not there.")
    return base.rstrip("/")


def _door(name: str) -> str:
    """A required door, resolved at ACCESS time and refused when unnamed (see flows_config.require).

    Module ``__getattr__`` rather than a module constant: a constant binds whatever the environment
    said at import, which is how a bare `pytest` run bound `http://localhost:18057` and talked to a
    different stack's admin-api on the same host. Resolving at access makes an unnamed door a loud
    refusal at the moment it would have been used, and lets a test set one without import order
    mattering."""
    return flows_config.require(name).rstrip("/")


def __getattr__(name: str) -> str:                     # PEP 562
    if name in _DOORS:
        return _door(_DOORS[name])
    raise AttributeError(name)


# `GATEWAY` is GONE from this map on purpose. `__getattr__` resolves a name here through
# `flows_config.require`, which REFUSES an empty value — correct for a door the process cannot work
# without, and exactly wrong for an optional domain. The meetings door is reached through
# `meetings_door()` above, which answers with a typed absence instead.
_DOORS = {"ADMIN_API": "VEXA_FLOWS_ADMIN_API_URL",
          "UI_URL": "VEXA_UI_URL"}


#: THE FLOWS TIER'S ONE CREDENTIAL. It opens the gateway's service door (`gateway/service_door.py`)
#: and nothing else: the door forwards the calls on its allow-list to the other domains and refuses
#: the rest. Flows holds no credential that can act for an arbitrary person on an arbitrary route —
#: not the internal-tier secret (every `/internal/*` and `/api/*` route of every domain, as any
#: `X-User-Id`), not the admin key (any person's gateway token, minted on request).
SERVICE_KEY_ENV = "VEXA_FLOWS_SERVICE_KEY"
#: The header the door reads the key from — and the header the door's inbound leg (`/to-flows/*`)
#: stamps on the internal-tier calls it forwards INTO flows-api.
SERVICE_KEY_HEADER = "X-Vexa-Service-Key"
#: Placeholder literals a stock deploy surface once supplied for a secret. Every one of these is
#: PUBLISHED in the OSS repository, so a deployment holding one is not configured — it is open,
#: wearing a configured face. The internal tier's list (fact placeholder-secrets): the services'
#: config.v1 `forbidden_values` carry the same values, and so does the flows key's declaration.
INTERNAL_SECRET_PLACEHOLDERS = ("vexa-internal-secret", "lite-internal-secret", "changeme",
                                "change-me", "CHANGE-ME", "default", "secret")
#: ONE REFUSAL LIST, NOT ONE PER CREDENTIAL: a placeholder learned here is refused everywhere in this
#: brick at once.
PLACEHOLDER_SECRETS = INTERNAL_SECRET_PLACEHOLDERS


def service_key() -> str:
    """The flows key AS THE ENVIRONMENT CARRIES IT — "" when unset, and never a refusal.

    THE REFUSAL IS `require_service_key`'s. This is the READ, for a module that must be IMPORTABLE
    outside a deployment (`flows_integrations.flows_api`, Vexa-ai/vexa#1629 — the release's identity
    probe imports it inside the image with no environment at all)."""
    return (os.environ.get(SERVICE_KEY_ENV) or "").strip()


def require_service_key() -> str:
    """The flows key, or the process refuses to start.

    No default and no fallback: a weak default makes an unconfigured deployment look configured and
    fails no test. The value lives in the deployment's secret store; it never appears in this
    repository, in a log, or in an error message — including the ones below, which name the
    VARIABLE and never the value."""
    key = service_key()
    if not key:
        raise RuntimeError(
            f"{SERVICE_KEY_ENV} is unset — flows refuses to start rather than run with no way into "
            "the other domains. Give flows and the gateway the same generated value; never put it "
            "in the repo.")
    if key in PLACEHOLDER_SECRETS:
        raise RuntimeError(
            f"{SERVICE_KEY_ENV} is the placeholder {key!r} — refusing to start. That literal is "
            "published in this repository, so it authenticates nobody and everybody.")
    return key


def door_headers(uid=None) -> dict:
    """The headers of one call through the service door: the flows key, and the SUBJECT the call
    acts for when it acts for one. The subject is the only identity fact flows can name — the door
    drops every other `x-user-*` — and the door decides which routes it may be named on."""
    h = {SERVICE_KEY_HEADER: require_service_key()}
    if uid not in (None, ""):
        h["X-User-Id"] = str(uid)
    return h



# Where a person's own terminal lives — resolved through `__getattr__` above, like the other two
# doors. Same env name the control MCP already reads: one deployment fact, one variable, never two
# spellings of one host. A mail that says "open it here" and names a host the person cannot reach
# is worse than a mail with no link, which is exactly what a `localhost` default produced.


def ui_link(**params) -> str:
    """A composed terminal deeplink: ``ui_link(ask="minutes-review", meeting=41)``.

    NO PRODUCTION TOUCH IS BUILT THIS WAY ANY MORE — every step that creates one mints a SCAFFOLD
    (`mint_scaffold`, below), because a url carrying a preset name leaves the UI and the agent to
    compose their own halves and they disagree. This remains the HAND-LINK builder: `?ask=` is
    still a real entry point in the terminal (it mints a local scaffold client-side), and the eval
    harnesses address it directly.

    The params compose (``?ask=`` primes a chat, ``?meeting=`` opens the room) and they survive
    the sign-in hop, so ONE url is both the door and the destination. Empty values are dropped —
    a link with ``?meeting=`` and nothing after it reads as a bug to the person who hovers it.
    """
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in params.items() if v not in (None, "", [])})
    ui = _door("VEXA_UI_URL")
    return f"{ui}/?{q}" if q else f"{ui}/"


def mint_scaffold(kind: str, recipient: str, *, opening: str,
                  meeting_id=None, refs: Optional[dict] = None,
                  workspaces: Optional[list] = None,
                  tabs: Optional[list] = None, focus: Optional[str] = None,
                  share_token: Optional[str] = None,
                  provenance: Optional[dict] = None) -> str:
    """THE LINK, minted as a SCAFFOLD (PRD §5.5). Returns the url, or RAISES.

    This replaces `ui_link` everywhere a step creates a TOUCH. The difference is not cosmetic and
    it is the whole point of the primitive: `ui_link` built a url out of a preset name and a
    meeting ref and left both renderers behind it — the terminal panel and the agent's first turn —
    to compose the rest out of whatever they could find. They disagreed, in every way the alpha
    ledger records: the chat opened on a Zoom number (F1), the panel opened the reader's own README
    (F19), the phase greeting beat the preset (F5), the header said UPCOMING about a meeting that
    had happened (F4). A scaffold is ONE record both of them read.

    A FAILED MINT RAISES, and the caller must not send. That is the share-gate doctrine one layer
    up, in the same words `email_attendees` already uses for a share it could not mint: *a mail
    whose only button opens a chat that cannot see the meeting is worse than no mail*. Everything
    that can be wrong is checked at the mint — the preset exists, the kind is in the catalogue, the
    terminal has an origin — because the mint is the last moment a step can still choose not to send.

    `share_token` is minted BY THE CALLER (`flows_steps.meeting.mint_transcript_share`) when the
    meeting is not the recipient's own: the restricted grant is written as the meeting's OWNER, whom
    flows names as the subject through the service door. agent-api composes the token into the url
    so the link is still built in exactly one place.
    """
    payload = {"who": recipient, "kind": kind, "opening": opening}
    if meeting_id not in (None, ""):
        payload["meeting"] = str(meeting_id)
    for key, value in (("refs", refs), ("workspaces", workspaces), ("tabs", tabs),
                       ("focus", focus), ("share_token", share_token), ("provenance", provenance)):
        if value not in (None, "", [], {}):
            payload[key] = value
    code, body = http("POST", f"{agent_door()}/internal/scaffolds",
                      door_headers(), payload)
    url = body.get("url") if isinstance(body, dict) else None
    if 200 <= int(code or 0) < 300 and url:
        return str(url)
    from flows import StepError
    detail = (body.get("detail") if isinstance(body, dict) else str(body))
    # 5xx is the platform having a moment; a 4xx is a fact about this preset, this kind or this
    # deployment and retrying it only delays the mail without changing the answer. Same split as
    # `mint_transcript_share`, deliberately — one rule for one class of failure.
    raise StepError(
        f"no scaffold could be minted for {recipient} ({kind}/{opening}"
        + (f", meeting {meeting_id}" if meeting_id else "")
        + f"): HTTP {code} — {str(detail)[:200]}. Not sending: a link that opens onto nothing is "
          "worse than no mail.",
        retryable=int(code or 0) >= 500 or int(code or 0) == 429)


def notify_in_app(kind: str, *, meeting_id, title: str = "", to: str = "", email: str = "",
                  actor: str = "", actor_name: str = "", url: str = "", object_kind: str = "") -> bool:
    """Hand one meeting fact to the person's in-app notifications (#1783): `meeting_shared` (to the
    account behind `email`), `bot_scheduled` / `bot_joined` (to the meeting's owner, `to`).

    agent-api is the store's one writer and composes the words; this only names the fact, on the
    internal tier. A PUBLISH, NOT A DEPENDENCY: no agent domain, no secret, a refusal or a timeout
    all return False and the step carries on — the mail (or the bot) is the act, the bell its echo.
    `url` is where Open goes and must be a path in the app; an absolute link is cut to its path."""
    base = (AGENT_API or "").strip()
    if not base:
        return False
    try:
        from urllib.parse import urlsplit
        path = ""
        if url:
            parts = urlsplit(str(url))
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        body = {"kind": kind, "meeting_id": str(meeting_id), "title": str(title or "")[:1000]}
        for key, value in (("to", to), ("email", email), ("actor", actor), ("actor_name", actor_name),
                           ("url", path), ("object_kind", object_kind)):
            if value:
                body[key] = str(value)
        code, _ = http("POST", f"{base.rstrip('/')}/internal/notifications",
                       door_headers(), body, timeout=5)
        return 200 <= int(code or 0) < 300
    except Exception:  # noqa: BLE001 — an in-app echo never fails the step that reports it
        return False


def db_url() -> str:
    """The engine's database, named by the deployment and by nothing else (decision 18d).

    It used to fall back to `docker exec <a named container> sh -c 'echo -n $POSTGRES_PASSWORD'`
    and compose a DSN against `127.0.0.1:5458` — the flows service reading a credential out of a
    container belonging to one developer's other stack, on one host. No deployment can satisfy
    that, no contract can declare it, and no operator can see it until it fails; worse, on a
    machine that runs more than one stack the guessed DSN does not fail at all, it addresses
    somebody else's data. `require` refuses and names the key instead."""
    return flows_config.require("VEXA_FLOWS_DB_URL")


def swallowed(source: str, kind: str, exc: BaseException | None = None, **facts) -> None:
    """SAY THAT SOMETHING WAS SWALLOWED. One line, at warning, naming who swallowed it and why.

    P18. This brick has fourteen `except: pass` sites and, before this, not one of them logged —
    so a Postgres outage during a refresh surfaced to an operator as *"flow retired by deploy"*,
    which is a different event with a different owner and a different fix. A degradation nobody can
    see is indistinguishable from the healthy path it degrades to, which is the whole reason the
    swallow was safe to write in the first place.

    `source` is the function that decided to continue and `kind` is what it decided to continue
    PAST — deliberately two fields rather than one sentence, so a reader grepping the logs can
    count occurrences of a kind without parsing prose.

    `print`, not `logging`, because that is this brick's convention end to end (`flows_worker`,
    `mailbox`, `flows_api` all print to stdout and the deployment collects it); introducing a
    second logging mechanism for one helper would be the drift this file's own rules are about."""
    tail = "".join(f" {k}={v!r}" for k, v in facts.items())
    detail = f": {type(exc).__name__}: {exc}" if exc is not None else ""
    print(f"warning: swallowed source={source} kind={kind!r}{tail}{detail}"[:600], flush=True)


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 20):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    # THE TRACE TRAVELS ON EVERY HOP THROUGH HERE (logevent.v1). This is the one door to Vexa
    # services; third-party wires (Graph, IMAP, mailpit, SMTP) have their own openers and never
    # carry it. A caller that set its own X-Trace-Id keeps it.
    trace = {} if any(k.lower() == flows_obs.TRACE_HEADER for k in headers) else flows_obs.trace_headers()
    for k, v in {"content-type": "application/json", **trace, **headers}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except Exception as e:  # noqa: BLE001 — steps turn this into a typed retry
        from flows import StepError
        # The reason column is the only thing anyone reads when a reaction is stuck, so it has to
        # carry the cause. It used to carry `TypeError` alone — the class name of an exception
        # raised while building a header, against a url this line had already truncated to the
        # host. Hours went into finding a deleted user behind that word.
        raise StepError(f"http {method} {url}: {type(e).__name__}: {e}"[:400])


def platform_user_id(email: str) -> str:
    """This person's platform id IF THEY ALREADY HAVE ONE, else "". NEVER creates.

    `ensure_platform_user` was once the only door, so a step that merely wanted to know whether
    somebody is already a user had to mint an account to find out — and an account minted by a mail
    nobody asked for is a ghost that later reads as an adopted user. Asking is a different verb
    from creating, and this is the asking half: one GET, no side effect, "" for a stranger.
    """
    # PERCENT-ENCODED. `email` comes off an ICS `ATTENDEE`/`ORGANIZER` line — attacker-adjacent
    # text — and was interpolated raw into an internal service path, where a `/`, a `?` or a `#`
    # re-points the request at a different route on a service that trusts this caller (R-B14).
    # agent-api's own resolver has always quoted; these two calls did not.
    code, u = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users/by-email/"
                          f"{_q(email, safe='')}", door_headers())
    return str(u["id"]) if code == 200 and isinstance(u, dict) and u.get("id") is not None else ""


def ensure_platform_user(email: str) -> str:
    """This person's platform id, CREATING the account when they have none.

    The creating half, written ON TOP OF the asking half rather than beside it: the same lookup
    used to be spelled out twice, so a change to how we ask would have had to be made in two places
    and would have been made in one."""
    existing = platform_user_id(email)
    if existing:
        return existing
    _code, u = http("POST", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users", door_headers(),
                    {"email": email, "name": email.split("@")[0].title()})
    return str(u["id"])


def is_admin_address(email: str) -> bool:
    """Is this address the deployment's admin account, or one of its instance admins? Identity's own
    answer (`/internal/users/{id}/is-admin`); never raises, and anything that is not a clear yes —
    a stranger, identity down, a malformed answer — is NO. It gates `mail.<kind>: admins`, where a
    wrong yes would mail a person a kind the company has not turned on for them."""
    try:
        uid = platform_user_id(email)
        if not uid:
            return False
        code, body = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users/"
                                 f"{_q(uid, safe='')}/is-admin",
                          door_headers())
    except Exception:  # noqa: BLE001 — fail closed
        return False
    return code == 200 and isinstance(body, dict) and (
        body.get("admin_account") is True or body.get("is_admin") is True)


def address_for_uid(uid: str) -> str:
    """This platform user's own email address, or "" if the id does not resolve. NEVER creates.

    The reverse of `platform_user_id`: `ensure_user` mints a uid FROM an organiser's address, on
    the assumption that every meeting starts as a calendar invite naming one. An AD HOC meeting
    (`POST /bots`, or the MCP tool, with no calendar invite) has no organiser at all —
    meeting-api's `meeting.completed` publishes only `{admitted_by, completion_reason, meeting_id,
    native, platform, uid}` (`meeting_api.events.meeting_completed_refs`), because the person who
    dispatched the bot IS `uid`, by definition, and nothing else names them. A step that needs an
    address to mail to — every send in this file, and `mint_scaffold`'s `who` refuses anything
    without an "@" — resolves that address here rather than assuming `refs["organizer"]` exists.
    """
    code, u = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users/"
                          f"{_q(str(uid), safe='')}/email", door_headers())
    return str(u["email"]) if code == 200 and isinstance(u, dict) and u.get("email") else ""


def ws_get(uid: str, path: str, slug: Optional[str] = None) -> tuple[int, object]:
    """`GET /api/workspace/file` AS `uid` → (status, body). For a caller that must tell a page that
    is not there (404) from one that could not be read (anything else)."""
    # `path` is refs-derived and one of its sources is the invite's own `#group:` token, so it is
    # attacker-adjacent in exactly the way the address above is: unencoded, a `&` or a `#` in it
    # forges a second query parameter on an internal service (R-B14).
    q = f"&slug={_q(slug, safe='')}" if slug else ""
    return http("GET", f"{agent_door()}/api/workspace/file?path={_q(path, safe='')}{q}",
                door_headers(uid))


def ws_file(uid: str, path: str, slug: Optional[str] = None) -> Optional[str]:
    code, body = ws_get(uid, path, slug)
    return body.get("content") if code == 200 and isinstance(body, dict) else None


def ws_write(uid: str, path: str, content: str, slug: Optional[str] = None) -> tuple[int, object]:
    """Write one page AS `uid` through agent-api's own door (`PUT /api/workspace/file`), which
    decides whether `uid` may write there — for `_global`, only the deployment's admin account.
    Returns (status, body); never raises on a refusal, so the caller can say what agent-api said."""
    body = {"path": path, "content": content}
    if slug:
        body["slug"] = slug
    return http("PUT", f"{agent_door()}/api/workspace/file",
                door_headers(uid), body)


# A person's preferences, read from IDENTITY — the one domain flows may depend on.
#
# They used to be read from `.settings.json` through `GET {AGENT_API}/api/workspace/file`, which was
# flows reaching into the agent domain for a fact about a PERSON. Two things were wrong with that
# and only one was the layering: every mail this engine sends is gated on one of these values, so a
# deployment without the agent domain did not get "no preferences", it got "mail everybody
# everything, in UTC". Identity is the only domain everyone may depend on (founder ruling
# 2026-09-02), and `admin_api.app.person_settings` is now where the vocabulary lives.
#
# `bot_name` is NOT here: a bot default is a fact about the bot, and the bot is meetings'.
_SETTING_DEFAULTS = {
    "mail_minutes": True, "mail_join": False, "mail_rsvp": True, "timezone": "",
    # the prepare-for-this-meeting note, the twin of mail_minutes at the other end of the meeting.
    "mail_prep": True,
    # the notification policy the delivery gate reads (PRD 0.13.4 D15, S6): identity validates it,
    # `flows_delivery.policy.normalize` fills its defaults and clamps it to the global limits.
    "notifications": None,
}

#: HOW LONG A CACHED ANSWER IS GOOD FOR. The entry below used to have no expiry at all: its own
#: comment said *"for the length of one step"*, and nothing ever cleared it, so in a worker — which
#: is a process that lives for days — the first answer for a uid was the last one that person ever
#: got. Somebody turning their minutes mail off did not take effect until a deploy, and nobody
#: could see why, because the cache is invisible and the identity row said the right thing.
#:
#: Thirty seconds keeps what the cache is FOR — a step reads two or three of these in a row and
#: they cannot change between two lines of the same function — and gives up what it never should
#: have taken, which is the rest of the process's life. It is deliberately not a config key: a
#: value nobody would ever tune is a surface for no reader (P14 the other way round).
SETTINGS_TTL_S = 30.0

#: uid -> (expires_at, settings). Process memory, never a file: this is a cache of a preference,
#: not state a step may depend on (this file's stateless law).
_person_settings_cache: dict = {}


class SettingsUnavailable(StepError):
    """Identity could not be asked what this person prefers — RETRYABLE, and never a default.

    THE OLD SHAPE ANSWERED THE DEFAULTS ON ANY FAILURE, and its docstring defended the choice as
    the lesser of two evils: *"a mail preference that fails OPEN is a person who gets mail they
    turned off, one that fails CLOSED is a person who silently stops receiving their minutes"*.
    Both of those are true, and both are answers to the wrong question — a transport error is not
    a fact about what somebody prefers, and neither direction had to be picked. The third option is
    to not answer yet: a reaction that retries in ten minutes mails nothing wrong now and mails the
    right thing later, which is what `retryable` is for.

    It mattered more than a single wrong mail, because the answer was then CACHED: one refused
    connection during a rolling restart pinned that person to defaults for the life of the worker,
    with the identity row saying something else the whole time.

    What is NOT a failure and still answers defaults: identity replying 200 with a setting absent.
    That is a person who has never touched it, and the default is the documented answer."""


def person_settings(uid: str) -> dict:
    """Every setting for one person, from identity. Raises `SettingsUnavailable` if it cannot ask.

    The door is `_door(...)`, never the bare `ADMIN_API`: that name is served by module
    `__getattr__` (PEP 562), which fires on ATTRIBUTE access from another module — `from common
    import ADMIN_API` — but NOT on a LOAD_GLOBAL inside this module's own functions, where the name
    is simply absent and raises NameError. Under the old broad `except` that NameError became the
    defaults, so a lookup mistake produced the exact "mail everybody everything, in UTC" failure
    this move exists to end. Nothing here swallows it now."""
    uid = str(uid)
    hit = _person_settings_cache.get(uid)
    if hit is not None and hit[0] > time.time():
        return hit[1]
    # `http` raises StepError for a transport failure and RETURNS a code for an HTTP answer, so the
    # two are handled separately and on purpose: the first propagates as-is (it already carries the
    # method, the url and the cause), the second is named here with the code identity actually gave.
    code, body = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users/{uid}/settings",
                      door_headers())
    if code != 200 or not isinstance(body, dict):
        raise SettingsUnavailable(
            f"identity answered {code} for {uid}'s settings — this reaction does not know what "
            f"they prefer yet, and a default is a guess about a person, not an answer: "
            f"{str(body)[:160]}", retryable=True)
    out = dict(_SETTING_DEFAULTS)
    out.update({k: v for k, v in body.items() if k in _SETTING_DEFAULTS})
    _person_settings_cache[uid] = (time.time() + SETTINGS_TTL_S, out)
    return out


def forget_person_settings(uid: Optional[str] = None) -> None:
    """Drop a cached answer — one person's, or everybody's. For a caller that has just CHANGED a
    setting and for the suite; the TTL is what everything else relies on."""
    if uid is None:
        _person_settings_cache.clear()
    else:
        _person_settings_cache.pop(str(uid), None)


def setting(uid: str, key: str):
    """One preference for one person, from identity.

    RAISES when identity cannot be asked (see `SettingsUnavailable`); answers the documented
    default when identity answers and simply holds no value for that key.

    `bot_name` IS NOT HERE, and its absence is the point: a bot default is a fact about the bot, so
    meetings resolves it on the spawn path and no caller reads it from anywhere."""
    return person_settings(uid).get(key, _SETTING_DEFAULTS.get(key))


def scaffolded(uid: str, slug: Optional[str] = None) -> bool:
    return ws_file(uid, ".scaffolded", slug) is not None
