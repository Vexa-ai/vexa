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

#: The agent domain's door, or "" when the agent domain is not deployed (PRD decision 40.7). Read
#: through the contract, which declares it a CAPABILITY rather than a defaulted URL — see
#: `flows_config`'s DOORS block for why a host-port default is a correctness bug and not a
#: convenience.
AGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL")

#: THE MEETINGS DOOR, and the name is a defect this change does not close. The key still says
#: GATEWAY because flows reaches meetings THROUGH THE EDGE today — twelve call sites, all of them
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


#: The ONE name for the internal-tier secret — the compose/helm secret key, the name admin-api,
#: gateway, meeting-api and agent-api read. Flows had a THIRD spelling of its own, which is exactly
#: how the published literal `vexa-internal-secret` came to sit on this file's refusal list and on
#: nobody else's (F95): one secret with three names has three refusal lists and they drift.
INTERNAL_SECRET_ENV = "INTERNAL_API_SECRET"
#: Read when the canonical name is absent, with a deprecation warning. Removed next release.
INTERNAL_SECRET_ENV_DEPRECATED = ("VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET")
#: Placeholder literals a stock deploy surface once supplied. Every one of these is PUBLISHED in the
#: OSS repository, so a deployment holding one is not configured — it is open, wearing a configured
#: face. Same list as the services' config.v1 `forbidden_values`.
INTERNAL_SECRET_PLACEHOLDERS = ("vexa-internal-secret", "lite-internal-secret", "changeme",
                                "change-me", "CHANGE-ME", "default", "secret")
#: ONE REFUSAL LIST, NOT ONE PER CREDENTIAL. The note above is about a secret with three names
#: having three refusal lists that drift; a second list for a second key is the same defect one
#: step removed. `require_admin_key` reads this, so a placeholder learned here is refused
#: everywhere in this brick at once.
PLACEHOLDER_SECRETS = INTERNAL_SECRET_PLACEHOLDERS


def require_admin_key() -> str:
    """The ADMIN-API key, or the caller refuses to act.

    It used to be `ADMIN_KEY = os.environ.get("VEXA_FLOWS_ADMIN_KEY", "changeme")` — a module
    constant with a weak default, four lines above `require_internal_secret`, whose docstring says
    *"a weak default is worse than no default… so there is no default"*. The default was the
    stronger claim of the two, because this key is not a read credential: it opens
    `ensure_platform_user`, which mints platform accounts, and `user_api_key`, which mints
    full-scope gateway tokens for ANY user id the caller names (R-B11).

    A FUNCTION, not a constant, on purpose: a constant read at import forces the refusal into
    module-import time, where a test that never touches admin-api still pays for it, and where the
    failure is attributed to whoever imported first rather than to the call that needed the key.
    """
    key = (os.environ.get("VEXA_FLOWS_ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "VEXA_FLOWS_ADMIN_KEY is unset — refusing to call admin-api rather than trying a "
            "placeholder. It mints accounts and full-scope tokens; give it a real value the same "
            f"way {INTERNAL_SECRET_ENV} gets one, from a mode-600 file the start script exports.")
    if key in PLACEHOLDER_SECRETS:
        raise RuntimeError(f"VEXA_FLOWS_ADMIN_KEY is the placeholder {key!r} — refusing to use it. "
                           "That literal is published in this repository, so it authenticates "
                           "nobody and everybody.")
    return key


def _admin_headers() -> dict:
    return {"X-Admin-API-Key": require_admin_key()}


def require_internal_secret() -> str:
    """The INTERNAL-TIER secret, or the process refuses to start.

    agent-api's meeting room (`control_plane/meeting_room.py`, gate 0) opens only for a caller that
    presents `X-Internal-Secret`. A browser client through the gateway holds no such secret and
    therefore cannot open a room at all — which is the entire point of the gate, and why flows,
    which is an internal-tier caller, must hold one.

    Deliberately the SAME REFUSAL as `flows_api._require_api_key`, down to the wording: a weak
    default makes an unconfigured deployment look configured and fails no test, so there is no
    default and no fallback. The value lives in a mode-600 file on the deployment host, exported by the
    lane's start script; it never appears in this repository, in a log, or in an error message —
    including the ones below, which name the VARIABLE and never the value.

    TWO things changed with F95. The variable is now `INTERNAL_API_SECRET`, the one name every other
    service uses (the old spellings still work for one release and say so); and the placeholder list
    carries the literals the deploy surfaces actually shipped, not four generic ones — the refusal
    that missed `vexa-internal-secret` was a refusal list written from imagination rather than from
    the compose file it was defending against.
    """
    key = (os.environ.get(INTERNAL_SECRET_ENV) or "").strip()
    if not key:
        for legacy in INTERNAL_SECRET_ENV_DEPRECATED:
            key = (os.environ.get(legacy) or "").strip()
            if key:
                print(f"WARNING: {legacy} is DEPRECATED — rename it to {INTERNAL_SECRET_ENV}, the "
                      f"one name the whole internal tier uses. Honoured this release, removed next.",
                      file=sys.stderr)
                break
    if not key:
        raise RuntimeError(
            f"{INTERNAL_SECRET_ENV} is unset — flows refuses to start rather than run with no "
            "internal-tier identity. Mint one into a mode-600 file on the deployment host "
            "and export it from the lane's start script; never put the value in the repo.")
    if key in INTERNAL_SECRET_PLACEHOLDERS:
        raise RuntimeError(
            f"{INTERNAL_SECRET_ENV} is the placeholder {key!r} — refusing to start. That literal is "
            "published in this repository, so it authenticates nobody and everybody.")
    return key

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
    meeting is not the recipient's own: the restricted grant is written as the meeting's OWNER, and
    the owner's gateway key lives here, not in agent-api. agent-api composes the token into the url
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
                      {"X-Internal-Secret": require_internal_secret()}, payload)
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


def db_url() -> str:
    """The engine's database, named by the deployment and by nothing else (decision 18d).

    It used to fall back to `docker exec <a named container> sh -c 'echo -n $POSTGRES_PASSWORD'`
    and compose a DSN against `127.0.0.1:5458` — the flows service reading a credential out of a
    container belonging to one developer's other stack, on one host. No deployment can satisfy
    that, no contract can declare it, and no operator can see it until it fails; worse, on a
    machine that runs more than one stack the guessed DSN does not fail at all, it addresses
    somebody else's data. `require` refuses and names the key instead."""
    return flows_config.require("VEXA_FLOWS_DB_URL")


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 20):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in {"content-type": "application/json", **headers}.items():
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
    code, u = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/admin/users/email/"
                          f"{_q(email, safe='')}", _admin_headers())
    return str(u["id"]) if code == 200 and isinstance(u, dict) and u.get("id") is not None else ""


def ensure_platform_user(email: str) -> str:
    """This person's platform id, CREATING the account when they have none.

    The creating half, written ON TOP OF the asking half rather than beside it: the same lookup
    used to be spelled out twice, so a change to how we ask would have had to be made in two places
    and would have been made in one."""
    existing = platform_user_id(email)
    if existing:
        return existing
    _code, u = http("POST", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/admin/users", _admin_headers(),
                    {"email": email, "name": email.split("@")[0].title()})
    return str(u["id"])


_KEY_CACHE: dict = {}


def _key_ttl() -> int:
    try:
        return max(int((os.environ.get("VEXA_FLOWS_USER_KEY_TTL_S") or "900").strip()), 60)
    except (TypeError, ValueError):
        return 900


def user_api_key(uid: str) -> str:
    """This user's gateway key, or a StepError that says why there isn't one.

    Returning None here was the whole bug: the caller put it straight into an X-API-Key header,
    urllib died joining it, and the reaction blamed the gateway for a 404 from the admin API. A
    key that cannot be minted is a fact about the account, and it belongs in the reason.

    IT MINTED A PERMANENT FULL-SCOPE TOKEN ON EVERY CALL, and it is called per gateway read —
    including once per attendee inside `mint_transcript_share`. One 20-person meeting left about
    thirty `["bot","browser","tx"]` tokens on the organiser's account and nothing ever deleted one
    (R-B13). Two changes, and the second is the one that bounds the damage:

      * `expires_in` — admin-api has taken it since the mint endpoint existed. A token that a
        post-meeting run needs for four minutes does not need to outlive the deployment.
      * a per-uid cache with the same lifetime — so a run that reads the gateway thirty times
        mints ONE token, and a restart simply mints another. Process memory, never a file: this
        is a cache of a credential, not state a step may depend on (the file's stateless law).
    """
    ttl = _key_ttl()
    hit = _KEY_CACHE.get(str(uid))
    if hit and hit[1] > time.time() + 30:
        return hit[0]
    st, tok = http("POST", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/admin/users/"
                           f"{_q(str(uid), safe='')}/tokens",
                   _admin_headers(),
                   {"scopes": ["bot", "browser", "tx"], "expires_in": ttl})
    key = tok.get("token") or tok.get("key") if isinstance(tok, dict) else None
    if not key:
        from flows import StepError
        detail = (tok.get("detail") if isinstance(tok, dict) else str(tok))
        raise StepError(f"no api key for platform user {uid} — admin api said {st}: "
                        f"{str(detail)[:120]}")
    _KEY_CACHE[str(uid)] = (key, time.time() + ttl)
    return key


def ws_file(uid: str, path: str, slug: Optional[str] = None) -> Optional[str]:
    # `path` is refs-derived and one of its sources is the invite's own `#group:` token, so it is
    # attacker-adjacent in exactly the way the address above is: unencoded, a `&` or a `#` in it
    # forges a second query parameter on an internal service (R-B14).
    q = f"&slug={_q(slug, safe='')}" if slug else ""
    code, body = http("GET", f"{agent_door()}/api/workspace/file?path={_q(path, safe='')}{q}",
                      {"X-User-Id": uid})
    return body.get("content") if code == 200 and isinstance(body, dict) else None


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
}

#: uid -> settings, for the length of one step. A step reads two or three of these in a row and the
#: values cannot change mid-step; a cache that outlived the process would be a stale preference,
#: which is the failure this whole move is about.
_person_settings_cache: dict = {}


def person_settings(uid: str) -> dict:
    """Every setting for one person, from identity. Never raises.

    ON AN UNREACHABLE IDENTITY IT RETURNS THE DEFAULTS, and that direction is deliberate: a mail
    preference that fails OPEN is a person who gets mail they turned off, one that fails CLOSED is a
    person who silently stops receiving their minutes. The defaults are the documented answer and
    they are exactly what somebody who has never touched a setting already gets."""
    uid = str(uid)
    hit = _person_settings_cache.get(uid)
    if hit is not None:
        return hit
    out = dict(_SETTING_DEFAULTS)
    try:
        # `_door(...)`, never the bare `ADMIN_API`. The door is served by module `__getattr__`
        # (PEP 562), which fires on ATTRIBUTE access from another module — `from common import
        # ADMIN_API` — but NOT on a LOAD_GLOBAL inside this module's own functions, where the name
        # is simply absent and raises NameError. The broad `except` below would then swallow that
        # into the defaults, so every timezone would read UTC and every mail preference would read
        # its default: the exact "mail everybody everything, in UTC" failure this move exists to
        # end, reintroduced by the lookup rather than by the missing domain. The three call sites
        # above already spell it this way.
        code, body = http("GET", f"{_door('VEXA_FLOWS_ADMIN_API_URL')}/internal/users/{uid}/settings",
                          {"X-Internal-Secret": require_internal_secret()})
        if code == 200 and isinstance(body, dict):
            out.update({k: v for k, v in body.items() if k in _SETTING_DEFAULTS})
    except Exception:  # noqa: BLE001 — a preference read must never fail a reaction
        pass
    _person_settings_cache[uid] = out
    return out


def setting(uid: str, key: str):
    """One preference for one person, from identity. Never raises: unreachable means defaults.

    `bot_name` IS NOT HERE, and its absence is the point: a bot default is a fact about the bot, so
    meetings resolves it on the spawn path and no caller reads it from anywhere."""
    return person_settings(uid).get(key, _SETTING_DEFAULTS.get(key))


def scaffolded(uid: str, slug: Optional[str] = None) -> bool:
    return ws_file(uid, ".scaffolded", slug) is not None
