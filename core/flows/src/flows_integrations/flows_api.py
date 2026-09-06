"""flows-api — manage workflows FROM OUTSIDE, faster than any image rebuild. FastAPI, house-style
(the same shape as meeting-api/agent-api/admin-api), OpenAPI docs at /docs.

  GET  /flows                       every version (image + DB) + the step vocabulary
  GET  /flows/pages                 ONE PAGE PER RUNTIME-AUTHORED VERSION, as markdown — what the
                                    admin reads back after writing a flow from the governance chat
                                    (`bodies=0` for the poll shape, `only=a@1,b@2` to narrow).
                                    It renders; agent-api's `flow_pages_watch` is what writes them
                                    into `_global/flows/`, because this service has no `_global`
  POST /flows                       submit {name, on_event, steps:[names], params?, activate?}
                                    — validated against the deployed vocabulary AT SUBMISSION;
                                    auto-versioned; live in the worker within ~10 s
  POST /events                      admit ONE fact {event_type, source_event_id, refs} — the
                                    intake for producers that are not the mailbox
  POST /flows/{name}/{v}/activate   · POST /flows/{name}/{v}/retire
  GET  /reactions[?status=…]        the operator projection
  POST /reactions/{id}/{retry|resume|cancel}    the signal verbs (audited rows)
  GET  /queue/waiting               WHAT IS WAITING FOR ONE PERSON — the subject-scoped pending
                                    reactions, each naming the flow that produced it and its
                                    typed reason (PRD decision 42.2). The subject is the
                                    authenticated caller's, never an argument.
  GET  /queue/notices               THE STANDING NOTICES ONLY — the say text of the waiting items
                                    whose copy declared itself one (`notice: true` in a
                                    `behavior/queue/` file's front-matter). Same door as
                                    /queue/waiting, a much smaller answer: it is built to be asked
                                    on every call and to ride along with unrelated work.
  GET  /timeline?subject=…          ONE PERSON'S DAY, in order — facts, receipts and the
                                    meetings table merged and scoped to them (PRD decision 31).
                                    Read-only, and it takes the operator key OR the narrower
                                    VEXA_FLOWS_TIMELINE_KEY (see `_timeline_key`).

AUTH, TWO TIERS — because two different callers reach this surface and only one of them is an
operator (issue #1468):

  * THE SUBJECT-SCOPED ROUTES (`GET /flows`, `GET /reactions`, `POST /reactions/{id}/{verb}`,
    `GET /queue/waiting`, `GET /queue/notices`, `GET /timeline`) take EITHER the operator key OR a person's own Vexa
    credential, as a bearer or `X-API-Key`. With a person's credential the subject is DERIVED from
    it, through identity's `/internal/validate` — the one resolver, the same one the gateway asks
    (P23) — and a `subject` argument naming anyone else is refused rather than honoured. The MCP
    edge forwards the caller's own credential and holds none of its own, so this is what makes
    those five tools usable by a person at all: before it, the only way to make them answer was to
    hand the edge the operator key, and the operator key is not a person — it reads every reaction
    in the instance and cancels any of them.
  * THE OPERATOR ROUTES (`POST /flows`, `POST /events`, `POST /events/batch`,
    `POST /flows/{name}/{version}/{action}`) take the operator key and nothing else. They
    configure the machine or admit facts on the instance's behalf; there is no per-person version
    of either.

THE OPERATOR TIER IS MEDIATED TOO (P20/E1, 2026-09-05). The operator key is a credential, not a
person: `Caller(kind="admin")` carries no uid, no email and no owner, so a subject-scoped route it
opened WITHOUT a subject had nothing left to authorize against — and answered with the instance.
`GET /reactions` returned every row a deployment held, and `POST /reactions/{id}/{verb}` gated
ownership behind `if subj:`, so an operator who simply omitted `subject` cancelled ANYONE's
reaction with no check at all. `VEXA_FLOWS_API_KEY` is exported into five compose services. So:
every subject-scoped route now REQUIRES a named subject from any credential that is not a person
(`?subject=`, or the gateway's `X-User-Id` where the route takes one), the ownership check on the
verb runs unconditionally, and there is no instance-wide read left on this surface.

`VEXA_FLOWS_TIMELINE_KEY` is its own tier for the same reason. It used to resolve to
`Caller(kind="admin")` — one line that turned "a key that can do exactly one thing" into a key
that read any person's queue and, through `meetings=true`, minted a gateway token on the named
third party's account. It is now `Caller(kind="timeline")`: `GET /timeline` for a named subject,
no meetings hop, and 403 on every other route by name.

The operator key travels as `X-Flows-Operator-Key`. It used to be `X-Flows-Admin-Key`, which reads
as ADMIN-API's token and is not one — that confusion is on the record: a lane start script carried
a `changeme` for it because whoever wrote the script exported the admin-api key under the name this
service reads. The old header is still accepted for one release, with a deprecation line printed
once per process; a caller sending both is answered on the new one.

NEVER accepts code — steps are reviewed Python in the image; this API composes them (the n8n line
we do not cross).

THE INSTANCE GATE cuts across this surface in three different ways, and the difference is the
point (see `flows_integrations/instance_gate.py`):

  * the two INTAKES (`POST /events`, `POST /events/batch`) still ADMIT while the gate is up — a
    fact that happened, happened, and dropping it at the door would lose it forever. The loop
    parks the reaction; the RESPONSE says so, because a fact that looks accepted and produces
    nothing is indistinguishable from a broken product.
  * the two OPERATOR VERBS (`POST /flows`, `POST /flows/{name}/{v}/{action}`) REFUSE with 409:
    they configure the machine, and configuring it before it knows who it works for is precisely
    what the gate exists to prevent.
  * READING (`GET /flows`, `GET /reactions`) stays open. An admin must be able to see the machine
    they are about to configure."""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from flows import Registry, SystemClock, admit, cancel, db_from_url, resume, retry, wake  # noqa: E402
import flows_config  # noqa: E402
import flows_pages  # noqa: E402 — the page renderer `GET /flows/pages` serves (Vexa-ai/vexa#1639)
from flows_defs import production  # noqa: E402
from flows_integrations import instance_gate  # noqa: E402
from flows_steps.common import (db_url, internal_secret,  # noqa: E402
                                require_internal_secret, setting)
from flows_integrations.subject_auth import (Caller, IdentityUnavailable,  # noqa: E402
                                              SubjectUnknown, resolve)
# ALIASED, and it is not style. The route below is also called `list_reactions`, and `def` rebinds
# the module global — so the route was calling ITSELF, and every authenticated `GET /reactions`
# answered 500 (`TypeError: list_reactions() got multiple values for argument 'status'`). The 401
# in front of it hid that for as long as the route was operator-only. One name, one thing.
import config_preflight                                            # noqa: E402
from flows_timeline import (REACTION_FOUND, REACTION_MISSING,  # noqa: E402
                            build_timeline, fetch_meetings, friction_for_subject,
                            reaction_concerns, render_preamble, render_text)
from flows_timeline import list_reactions as reactions_for  # noqa: E402
from flows_timeline.model import to_epoch as _friction_since_epoch  # noqa: E402

#: The placeholder literals to fall back on when the declaration cannot be read. DELIBERATELY the
#: superset of the four this file used to carry inline: a fallback that is narrower than the
#: contract is the drift this function exists to end.
_FALLBACK_PLACEHOLDERS = ("vexa-internal-secret", "lite-internal-secret", "changeme", "change-me",
                          "CHANGE-ME", "default", "secret")


def _forbidden_values(key: str) -> tuple:
    """The placeholder literals THE DECLARATION forbids for `key` — read, never re-typed.

    This file used to carry its own list of four (`changeme`, `change-me`, `default`, `secret`)
    while `config.v1.json` declared seven, so `VEXA_FLOWS_API_KEY=vexa-internal-secret` — a literal
    published in this repository, therefore not a secret — booted green. Two hand-maintained copies
    of one list drift the moment one of them is edited; there is now one copy, and it is the one
    `gate:config-contract` reads.
    """
    try:
        from config_preflight import load_declaration
        for entry in load_declaration().get("keys") or []:
            if entry.get("key") == key:
                return tuple(entry.get("forbidden_values") or ())
    except Exception:  # noqa: BLE001 — a declaration we cannot read must not weaken the check
        pass
    return _FALLBACK_PLACEHOLDERS


def _require_api_key() -> str:
    """The operator key, or the process refuses to start.

    It used to be `os.environ.get("VEXA_FLOWS_API_KEY", "changeme")`. The variable was never set
    on the running deployment — `flows-up.sh` exports `VEXA_FLOWS_ADMIN_KEY`, which is the
    admin-api token under a different name this module never reads — so the live intake accepted
    `X-Flows-Admin-Key: changeme`, a string printed in this file. The port binds 127.0.0.1, but
    the control MCP is public and forwards to it with that same key, so the door was open to
    anyone who could read the source.

    A weak default is worse than no default: it makes an unconfigured deployment look configured
    and it fails no test. So there is no default. A deployment that has not set the variable
    stops here, loudly, rather than serving on a known string.

    THE REFUSAL IS THE SHARED VALIDATOR'S NOW (F-D20 b), not this function's. What stood here was
    a hand-written pair of `RuntimeError`s carrying their own placeholder list —
    `("changeme", "change-me", "default", "secret")` — while `config.v1.json`, five directories
    away and enforced against every deploy surface by `gate:config-contract`, declared this same
    key `required-explicit` with SEVEN `forbidden_values`. The three the local list was missing
    (`vexa-internal-secret`, `lite-internal-secret`, `CHANGE-ME`) are precisely the literals a
    stock deploy supplies, so flows-api booted, green, on a secret published in this repository —
    F95's own lesson, in the one service that had not learned it: *"one secret with three names
    has three refusal lists and they drift"*.

    `flows_config.py` even said so out loud — *"nothing under core/flows/src imports the vendored
    `config_preflight`"* — with the declaration held up by contract tests that drove the validator
    while the running service never did. It does now, and the declaration is the only list.

    It validates the WHOLE declaration, not this key alone, and that is deliberate: `preflight`
    names every missing required-explicit key in ONE message rather than a peel-the-onion restart
    loop, so it is called FIRST, before anything else this module reads. `VEXA_FLOWS_ADMIN_KEY` is
    the only key whose absence newly stops the boot, and it was already refused at its first use
    (`flows_steps.common.require_admin_key`), already exported by every start script, and already
    asserted by `tests/test_config_contract.py`. flows-api declares no probes, so this does no I/O.
    """
    config_preflight.preflight()
    return (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()


def _configured(key: str, value: Optional[str] = None) -> str:
    """A credential AS THE ENVIRONMENT CARRIES IT — never a refusal, and "" for a literal the
    declaration forbids.

    THE READ, not the refusal. `_require_api_key`, `_timeline_key` and `require_internal_secret`
    are the refusals, they are unchanged, and they still run — in `boot()`, which is a different
    moment (see the block below). What this gives the module is a value to hold between import and
    boot, and it is FAIL-CLOSED on a placeholder because `_same_key` refuses an empty expected key:
    a module that has been imported but not booted then authenticates nobody at all, rather than
    authenticating anybody who can read a literal published in this repository.

    The forbidden list is the declaration's, read through `_forbidden_values` — never a second copy
    (F-D20 b: a second copy is how the first one drifted).
    """
    raw = os.environ.get(key) if value is None else value
    raw = (raw or "").strip()
    return "" if raw in _forbidden_values(key) else raw


def _timeline_key() -> str:
    """A SECOND key, for `GET /timeline` alone — or "" when the deployment has not minted one.

    The timeline is read-only and the agent worker asks for it on EVERY dispatch (decision 31 §1),
    so it needs a credential in every worker container. Handing those containers the OPERATOR key —
    the one that submits and activates flows — to read a list of times would widen the operator
    key's blast radius by one container per person per turn, which is the opposite of what the
    key-hardening above was for. This is a key that can do exactly one thing.

    Unset ⇒ only the operator key opens the route. That is the right default: a deployment that has
    not thought about this gets the narrower reach (nobody but the operator), never the wider one.

    A PLACEHOLDER IS REFUSED, not coerced to "". It used to be coerced, which reads as prudent and
    is not: an operator who set the key to a published literal got no error and no effect, and the
    literal is one anybody can present. Same refusal, same reason, same declared list as the
    operator key above.
    """
    key = (os.environ.get("VEXA_FLOWS_TIMELINE_KEY") or "").strip()
    if key and key in _forbidden_values("VEXA_FLOWS_TIMELINE_KEY"):
        raise RuntimeError(
            f"VEXA_FLOWS_TIMELINE_KEY is the placeholder {key!r} — refusing to start. Unset it, or "
            "mint a real value; a published literal is not a narrow credential.")
    return key


# ── IMPORT READS THE ENVIRONMENT. BOOT REFUSES ON IT. (Vexa-ai/vexa#1629) ─────────────────────
# They used to be the same moment. `a90e442a3` routed `_require_api_key` through the shared
# `config.v1` validator — right, and unchanged below — and MODULE SCOPE called it, so
# `import flows_integrations.flows_api` raised `ConfigError` in any process with no
# `VEXA_FLOWS_DB_URL`. The release's identity probe is such a process:
#
#   python -c "import flows_worker, flows_integrations.mailbox, flows_integrations.flows_api, os;
#              assert os.path.exists('/app/mcp.tools.v1.json')"
#
# and so is `make flow-pages`, and so is every tool that imports this module to READ it. None of
# them is a deployment and none of them has a DSN. A module that refuses to be imported refuses to
# be read — which is a different refusal from the one that was wanted, and it stood between a
# built candidate and a validated one.
#
# `flows_steps.common.require_admin_key` had already written the rule down, four lines above the
# secret this file was reading at import: *"A FUNCTION, not a constant, on purpose: a constant read
# at import forces the refusal into module-import time … and the failure is attributed to whoever
# imported first rather than to the call that needed the key."*
#
# So: the refusals are the same functions with the same messages in the same order, and they run in
# `boot()` — the ASGI lifespan runs it before the first request is served, `main()` runs it before
# the port is bound. Refusing to BOOT stays; refusing to IMPORT goes.
API_KEY = _configured("VEXA_FLOWS_API_KEY")
TIMELINE_KEY = _configured("VEXA_FLOWS_TIMELINE_KEY")
# The internal-tier identity, read under whichever name set it (`common.internal_secret`) and
# refused, like the two above, at boot.
INTERNAL_SECRET = _configured("INTERNAL_API_SECRET", internal_secret())

logger = logging.getLogger(__name__)

#: The engine's database — composed here when the deployment named one, `None` when it did not.
#: `postgres_db` is already lazy (no connection until the first query, deliberately, so this module
#: could be imported against a Postgres that is not up yet), so composing at import costs nothing
#: and keeps ONE adapter object: `production.build` below closes over this value, so `boot()` must
#: not quietly swap it for a second one. `None` is never served from — `boot()` refuses first.
db = db_from_url(db_url()) if flows_config.get("VEXA_FLOWS_DB_URL") else None
clock = SystemClock()
vocab = Registry()
production.build(vocab, db)

import json as _json
import pathlib as _pathlib


def boot() -> dict:
    """EVERY REFUSAL THIS PROCESS MAKES BEFORE IT SERVES — four questions, none of them the import's.

    * `contract_preflight()` — the whole `config.v1` declaration against this environment: ONE
      message naming every missing required-explicit key, plus a refusal for any key still holding
      a placeholder literal that is published in this repository (E6/ADR-0026, F-D20 b).
    * `_require_api_key()` and `_timeline_key()` — the two credentials this surface compares
      against, re-read here so a booted module holds the validated value rather than the lenient
      one `_configured` left it with.
    * `flows_config.preflight()` — flows' own rule, which is NOT in the declaration: a deployment
      that cannot NAME a door does not run, because there are no host-port defaults.
    * `require_internal_secret()` and the DSN — the internal-tier identity, and the database.

    IDEMPOTENT, and it has to be: `main()` runs it before binding the port and uvicorn runs the
    lifespan after, so an ordinary boot calls it twice. The validator is pure, flows declares no
    probes so it does no I/O, and the declaration is `lru_cache`d — the second call is a dict
    comparison.
    """
    global API_KEY, TIMELINE_KEY, INTERNAL_SECRET, db
    contract_preflight()
    API_KEY = _require_api_key()
    TIMELINE_KEY = _timeline_key()
    flows_config.preflight()          # no door, no boot — see flows_config's DOORS block
    INTERNAL_SECRET = require_internal_secret()
    if db is None:
        # Only reachable when the DSN arrived between import and boot — `contract_preflight()`
        # above refuses when it is still absent. Safe to compose here and nowhere else: the steps
        # `production.build` closed over run in the WORKER's process, never in this one (this
        # surface admits facts and steers reactions; it never ticks).
        db = db_from_url(db_url())
    return {"service": "flows-api", "steps": len(vocab.steps), "flows": len(vocab.flows)}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """The refusal at the one moment EVERY way of running this app passes through.

    `main()` is not that moment. Compose, helm and the rig all run `python -m
    flows_integrations.flows_api`, but `uvicorn flows_integrations.flows_api:app` does not, and
    neither does a test entering `TestClient(app)` as a context manager — and a surface that serves
    without booting is precisely what moving the check off module scope could otherwise have cost.
    """
    boot()
    yield


app = FastAPI(title="flows-api", version="0.1.0",
              description="Submit and manage Vexa workflows as data — no code over the wire.",
              lifespan=_lifespan)


def _same_key(presented: str, expected: str) -> bool:
    """Constant-time, like agent-api's equivalent check (R-B16).

    `!=` returns on the first differing byte, so the time it takes is a function of how much of
    the key the caller already has — which is the whole shape of a byte-at-a-time recovery. This
    is the key that gates `flows_submit`: decision 4's entire access model is "flows are
    admin-controlled, org-wide, full stop", and this comparison is that full stop.
    """
    if not expected:
        return False
    return hmac.compare_digest(str(presented or ""), str(expected))


#: The header the operator key travels in, and the one it used to.
OPERATOR_HEADER = "X-Flows-Operator-Key"
DEPRECATED_OPERATOR_HEADER = "X-Flows-Admin-Key"
#: Which deprecated spellings this process has already complained about. A deprecation printed per
#: REQUEST is a flood nobody reads and a cost on the hot path; printed once it is a message.
_DEPRECATED_HEADER_SAID: set = set()


def _operator_key(x_flows_operator_key: str = "", x_flows_admin_key: str = "") -> str:
    """The operator key the caller presented, under either name. "" when they presented none.

    The new name wins when both are sent: a caller mid-migration sends the new one deliberately and
    the old one because something else still adds it, and answering on the old one would make the
    migration invisible to them.
    """
    key = (x_flows_operator_key or "").strip()
    if key:
        return key
    key = (x_flows_admin_key or "").strip()
    if key and DEPRECATED_OPERATOR_HEADER not in _DEPRECATED_HEADER_SAID:
        _DEPRECATED_HEADER_SAID.add(DEPRECATED_OPERATOR_HEADER)
        print(f"WARNING: {DEPRECATED_OPERATOR_HEADER} is DEPRECATED — send the same value as "
              f"{OPERATOR_HEADER}. It is flows-api's OWN operator key (VEXA_FLOWS_API_KEY), never "
              "admin-api's token; the old name said otherwise and was believed.", flush=True)
    return key


def auth(x_flows_operator_key: str = Header(default=""),
         x_flows_admin_key: str = Header(default="")) -> None:
    if not _same_key(_operator_key(x_flows_operator_key, x_flows_admin_key), API_KEY):
        raise HTTPException(status_code=401, detail=f"{OPERATOR_HEADER} required")


def _bearer(authorization: str, x_api_key: str) -> str:
    """The caller's own Vexa credential, in either spelling of the ONE authentication path.

    `X-API-Key` is what the MCP edge forwards (`vexa_mcp/register.py`) and what the gateway
    resolves; `Authorization: Bearer` is what an MCP client sets. Same credential, two headers on
    the way here, and no third path: nothing is read from a query string or an argument (PRD 40.8).
    """
    key = (x_api_key or "").strip()
    if key:
        return key
    auth = (authorization or "").strip()
    if not auth:
        return ""
    scheme, _, token = auth.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def subject_or_operator(x_flows_operator_key: str = Header(default=""),
                        x_flows_admin_key: str = Header(default=""),
                        authorization: str = Header(default=""),
                        x_api_key: str = Header(default="")) -> Caller:
    """WHO IS CALLING — the operator, or one person. The dependency of every subject-scoped route.

    The operator key is checked first and short-circuits: it is a local constant-time comparison,
    it is what every existing caller sends, and it must keep working while identity is down.
    """
    presented = _operator_key(x_flows_operator_key, x_flows_admin_key)
    if _same_key(presented, API_KEY):
        return Caller(kind="admin")
    # THE NARROW KEY IS REFUSED HERE, BY NAME. It reaches this dependency only on a route it does
    # not open, and the honest answer to "you presented a key that opens something else" is 403 —
    # not the 401 the caller would otherwise get for holding no bearer, which reads as "your
    # credential is unknown" and sends the operator to check a key that is fine.
    if TIMELINE_KEY and _same_key(presented, TIMELINE_KEY):
        raise HTTPException(status_code=403, detail=(
            "VEXA_FLOWS_TIMELINE_KEY opens GET /timeline for one named subject and nothing else. "
            "This route needs a person's own Vexa credential, or the operator key in "
            f"{OPERATOR_HEADER}."))
    token = _bearer(authorization, x_api_key)
    if not token:
        raise HTTPException(status_code=401, detail=(
            "this route needs your Vexa credential (Authorization: Bearer …, or X-API-Key), "
            f"or the operator key in {OPERATOR_HEADER}"))
    try:
        return resolve(token, secret=INTERNAL_SECRET)
    except SubjectUnknown:
        raise HTTPException(status_code=401, detail="that credential does not identify anyone")
    except IdentityUnavailable as e:
        # 503, NEVER 401. We did not reach a verdict on the credential — see subject_auth.
        #
        # AND THE REASON IS LOGGED, NOT SERVED (P15). `IdentityUnavailable` carries the transport
        # exception, which names the internal admin-api address — and this 503 fires BEFORE
        # identity has vouched for anyone, so the reader of that sentence is an unauthenticated
        # caller. The operator needs the detail and has the log; the caller needs to know it is
        # not their key.
        logger.warning("identity could not answer who a caller is: %s", e)
        raise HTTPException(status_code=503, detail=(
            "identity could not answer who you are — this is our side, not your key"))


def timeline_reader(x_flows_operator_key: str = Header(default=""),
                    x_flows_admin_key: str = Header(default=""),
                    authorization: str = Header(default=""),
                    x_api_key: str = Header(default="")) -> Caller:
    """`GET /timeline` alone: the narrow read-only key opens it, as well as the two tiers above.

    THE NARROW KEY IS ITS OWN KIND, not an operator (P20/E1). It used to return
    `Caller(kind="admin")`, and that one line made "a key that can do exactly one thing" a key that
    could do every subject-scoped thing this service has: it read any person's reactions and queue,
    and — with `meetings=true`, the default — reached `common.user_api_key` and MINTED a gateway
    token on the named third party's account. `Caller(kind="timeline")` reaches exactly one route,
    must name its subject like the operator must, and never mints (see `timeline()`).
    """
    if TIMELINE_KEY and _same_key(_operator_key(x_flows_operator_key, x_flows_admin_key),
                                  TIMELINE_KEY):
        return Caller(kind="timeline")
    return subject_or_operator(x_flows_operator_key, x_flows_admin_key, authorization, x_api_key)


def scoped_subject(caller: Caller, requested: str, *, stamped: str = "") -> str:
    """The subject these rows are about — derived, never asserted, and never absent.

    THE OPERATOR MUST NAME ONE (P20/E1). It used to be allowed to name nothing, and nothing meant
    the whole instance: `GET /reactions` returned every row a deployment held and
    `POST /reactions/{id}/{verb}` steered any of them. `Caller(kind="admin")` carries no uid, no
    email and no owner, so with no subject there is nothing left to authorize against — the check
    is not weak there, it is absent. A credential that is not a person now says whose rows it wants,
    every time, on every subject-scoped route; asking about everyone is not a thing this surface
    does any more.

    `stamped` is the gateway's `X-User-Id` on the routes that take it, and it outranks `?subject=`
    for the same reason it always did: it is a service identity vouching for a person it resolved,
    where `?subject=` is the unstamped console read. Both are ways for a non-person credential to
    name a subject; neither is a way to name none.

    For a PERSON the subject is who their credential says they are. A `subject` argument is still
    accepted, because the tool schema advertises one and a client that holds its own uid or address
    will send it — but only as a spelling of themselves. Anything else is 403 rather than silently
    overridden: an argument quietly ignored is the same defect as one quietly dropped, and here it
    would be the difference between "here is your queue" and "here is someone else's".
    """
    asked = str(requested or "").strip()
    if caller.must_name_a_subject:
        subj = str(stamped or "").strip() or asked
        if not subj:
            raise HTTPException(status_code=400, detail={
                "subject_required": True,
                "note": ("this credential is not a person, so it must name the person it is asking "
                         "about: send ?subject=<uid|email> (or the gateway's X-User-Id where the "
                         "route takes one). There is no instance-wide read on this surface.")})
        return subj
    if asked and asked.lower() not in caller.names:
        raise HTTPException(status_code=403, detail={
            "not_your_subject": asked,
            "you_are": caller.uid,
            "note": ("the subject is derived from your credential — send no subject, or your own "
                     "platform id or address")})
    return caller.uid


def _as_me(caller: Caller):
    """The identity resolver to hand the model for a subject caller: the pair we already hold.

    `flows_timeline` otherwise asks admin-api to turn a uid into an address, which for this caller
    is a second hop for a fact `/internal/validate` already returned — and one more thing that can
    be down in the middle of a read.

    `None` for every credential that is NOT a person — the operator key and the timeline key both
    carry no pair to hand over, so the model does its own lookup for the subject they named.
    """
    if not caller.uid:
        return None
    return lambda _subject: (caller.uid, caller.email)


@app.get("/health")
def health():
    """Liveness, and the ONE route on this surface that takes no credential.

    A probe that needs a key is not a liveness probe: the orchestrator holding it would be a second
    place the operator key has to reach, and a 401 and a dead process look identical to a restart
    policy. Nothing here is a secret — the two counts are the in-memory registry's depth, the same
    shape meeting-api's receiver reports beside its own status.

    LIVENESS, NOT READINESS: it touches no database. `postgres_db` is lazy, so a flows-api that
    cannot reach Postgres still answers here, which is correct — this says the process is up, and
    the reaction loop's own health is `GET /reactions`, behind the key, where it belongs.
    """
    return {"status": "ok", "service": "flows-api",
            "flows": len(vocab.flows), "steps": len(vocab.steps)}


def _refuse_if_gated(verb: str) -> None:
    """409 while the company layer is missing — for the verbs that CHANGE the machine.

    409 rather than 403 on purpose: this is not "you may not", it is "not in this state, yet".
    The detail names the verb as well as the gate, because the operator who meets it is usually
    holding a script that made three calls and needs to know WHICH one stopped.
    """
    if instance_gate.company_layer_ready():
        return
    raise HTTPException(status_code=409, detail=(
        f"{verb} is refused: the company layer is not set up. {instance_gate.SETUP_SENTENCE}"))


def _with_gate(payload: dict) -> dict:
    """Tell an intake caller their fact was PARKED, not acted on.

    Silence here is the defect. Admission returns 202 either way — the reaction row exists, the
    dedup key is burned, everything the caller can observe says it worked — and then nothing
    happens for as long as setup takes. One field and one sentence turn an invisible delay into a
    stated one. The key is absent entirely when the gate is down: a caller should never have to
    parse `"gate": "completed"` to learn that things are normal.
    """
    if instance_gate.company_layer_ready():
        return payload
    return {**payload, "gate": "missing",
            "note": ("Admitted and PARKED — no step runs until the instance admin commits the "
                     f"company layer. Nothing is lost. {instance_gate.SETUP_SENTENCE}")}


class FlowSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    on_event: str = Field(min_length=1, max_length=120)
    steps: list[str] = Field(min_length=1)
    params: dict = Field(default_factory=dict)
    activate: bool = True


def _db_flows() -> list:
    """Every runtime-authored version, oldest first — the `flow_version` table as dicts.

    ONE READER, because `GET /flows` and `GET /flows/pages` answer two questions about the same
    rows and a second SELECT is a second column list to keep in step. `created_at` is here for the
    page: a flow somebody wrote from the governance chat says on its own page who activated it and
    when, and neither fact is anywhere else."""
    rows = db.execute("SELECT name, version, on_event, steps, params, status, created_by, "
                      "created_at FROM flow_version ORDER BY name, version")
    return [{"name": n, "version": v, "on": e, "steps": json.loads(st),
             "params": json.loads(p or "{}"), "status": status,
             "created_by": by, "created_at": str(at or ""), "source": "api"}
            for n, v, e, st, p, status, by, at in rows]


@app.get("/flows")
def list_flows(caller: Caller = Depends(subject_or_operator)):
    """Every flow this engine knows, and the step vocabulary flows are built out of.

    Read this when you need to know what this deployment can actually DO before promising it: each
    entry names the flow, its version, the event that starts it (`on`) and its ordered steps, and
    `source` says whether it came from the image or was submitted through the API. It answers
    "is there a flow for X?" — it does not start one; facts start flows.

    `steps_vocabulary` is every step name with its own one-line description, which is the same list
    a submitted flow is validated against. `shadowing_versions` names any runtime version that
    overrides an image version with fewer steps — the shape of a flow quietly doing less than the
    code says, surfaced here because nobody reads a startup log until they already suspect it.

    Takes the operator key or a person's own credential; it describes the machine, not a person, so
    the answer is the same either way.
    """
    db_flows = _db_flows()
    code_flows = [{"name": f.name, "version": f.version, "on": f.on.name,
                   "steps": list(f.steps), "source": "image", "status": "active"}
                  for f in vocab.flows.values()]
    # RUNTIME VERSIONS THAT SHADOW THE IMAGE'S with fewer steps — the F57 class. Surfaced on the
    # listing rather than only in a startup log, because the log is read when somebody already
    # suspects something and this is the defect where nobody does.
    return {"shadowing_versions": vocab.shadowing_versions(),
            "steps_vocabulary": [
        {"name": n, "doc": " ".join((vocab.steps[n].__doc__ or "undocumented").split())}
        for n in sorted(vocab.steps)],
        "flows": code_flows + db_flows}


@app.post("/flows", status_code=201, dependencies=[Depends(auth)])
def submit_flow(sub: FlowSubmission, x_actor: str = Header(default="api")):
    """Author a new version of a flow: an event to react to, and the ordered steps to run.

    `steps` may only name steps this deployment already carries — `GET /flows` lists the whole
    vocabulary under `steps_vocabulary`, and an unknown name is refused with the list rather
    than accepted and discovered at run time. The API never accepts code: a flow is data.

    Versions are minted, never overwritten. Submitting a name that already exists files the
    NEXT version beside it, and reactions already in flight keep the version they were admitted
    on. `activate` (default true) makes it live within about ten seconds; false files it as a
    draft for `POST /flows/{name}/{version}/activate` later.

    Operator-gated: this door reads the deployment's own operator key, never the caller's."""
    _refuse_if_gated("flows_submit")
    missing = [s for s in sub.steps if s not in vocab.steps]
    if missing:
        raise HTTPException(status_code=400,
                            detail={"unknown_steps": missing, "vocabulary": sorted(vocab.steps)})
    row = db.execute("SELECT COALESCE(MAX(version),0) FROM flow_version WHERE name=:n",
                     {"n": sub.name})
    code_max = max([v for (fn, v) in vocab.flows if fn == sub.name], default=0)
    version = max(row[0][0], code_max) + 1
    status = "active" if sub.activate else "draft"
    db.execute("""INSERT INTO flow_version (name, version, on_event, steps, params, status,
                                            created_by, created_at)
                  VALUES (:n,:v,:e,:s,:p,:st,:by,:t)""",
               {"n": sub.name, "v": version, "e": sub.on_event, "s": json.dumps(sub.steps),
                "p": json.dumps(sub.params), "st": status, "by": x_actor, "t": clock.now()})
    return {"name": sub.name, "version": version, "status": status,
            "live_within_s": 10 if status == "active" else None}


@app.get("/flows/pages")
def flow_pages(bodies: int = 1, only: str = "",
               caller: Caller = Depends(subject_or_operator)):
    """THE PAGE OF EVERY FLOW SOMEBODY WROTE — one per runtime-authored VERSION, as markdown.

    Founder, 2026-09-06: *"we want to be able to write flows for the global chat as we like."*
    `flows_submit` files a flow as data and the worker runs it about ten seconds later; this is
    where the admin reads what they just made. Same page shape as the image's flows carry in
    `_global/flows/` (#1615/#1626) — trigger, the steps in order with what each reads, does and
    leaves behind, what it mails, the rules it honours and the Python at the foot — plus the three
    facts only a runtime version has: who activated it, whether it is still the version new facts
    react on, and which version replaced it when it is not.

    THIS ROUTE RENDERS; IT DOES NOT WRITE. The page has to land in `_global/flows/`, and this
    service has no `_global`: compose gives flows-api no volumes at all, while agent-api holds the
    writable organisation tier (`control_plane/api._global_store`) and already seeds files into it.
    So the writer is agent-api's `flow_pages_watch`, which polls this and reconciles the directory
    — and the reason it is a poll rather than a call from here is that a page is a promise the
    product keeps continuously: a hook fires once, and when its one write cannot land (a read-only
    mirror, agent-api restarting) the flow is live with no page and nothing retries.

    `bodies=0` answers the same list without the markdown — the poll shape, so the hot loop carries
    an etag per page instead of every step's source. `only=a@1,b@2` narrows to named pages, which is
    what the writer asks for once it knows which ones changed.

    Takes the operator key or a person's own credential, exactly like `GET /flows`: it describes the
    machine, not a person."""
    wanted = {s.strip() for s in (only or "").split(",") if s.strip()}
    pages = flows_pages.runtime_pages(vocab, _db_flows())
    if wanted:
        pages = [p for p in pages
                 if p["file"] in wanted or f"{p['flow']}@{p['version']}" in wanted]
    if not bodies:
        pages = [{k: v for k, v in p.items() if k != "body"} for p in pages]
    return {"dir": flows_pages.PAGES_DIR[-1], "pages": pages}


class EventSubmission(BaseModel):
    """A fact. Not a command — the caller says what HAPPENED and the registry decides what
    reacts. `source_event_id` is the caller's own identifier for the occurrence and is the whole
    idempotency story: the same id twice creates nothing the second time."""
    event_type: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=200)
    refs: dict = Field(default_factory=dict)


def _actor(x_actor: str) -> str:
    """WHO admitted this fact — carried on the receipt and onto the reaction.

    The intake authenticates with the lane's operator key, so by default the honest answer is
    that a service did it: "service-key". A caller acting FOR a person says so with X-Actor, and
    that value is recorded verbatim rather than trusted for anything — it is provenance, not
    authorisation. Authorisation already happened at the key.

    The question this exists to answer is the one S1 will ask first: "who invited the mailbox to
    that meeting?" Before this, a reaction could say what it reacted to and never who caused it,
    and an admin bulk-seeding an org is exactly the case where that matters.
    """
    a = (x_actor or "").strip()
    return a[:120] if a else "service-key"


@app.post("/events", status_code=202, dependencies=[Depends(auth)])
def admit_event(ev: EventSubmission, x_actor: str = Header(default="")):
    """THE FACT INTAKE for producers that are not the mailbox.

    mailbox.py admits exactly two event types, both read off an IMAP inbox: invite.received and
    mail.reply. Everything else that happens to a person — a meeting scheduled from the terminal,
    a bot booked over the control MCP, a calendar sync — reaches the platform's own tables and
    never reaches flows, so no flow can react to it. This endpoint is that missing edge, and
    deliberately the smallest possible one: one fact, admitted through the SAME `admit()` the
    worker's emit uses, with the same per-(fact, flow) dedup key.

    An event type no flow reacts to is a 400 carrying the list that would have worked — a fact
    accepted into silence looks exactly like a fact that worked, and this is the endpoint where
    that mistake would be made. It never accepts code, and it never names a step: what the fact
    causes is the registry's business (the n8n line we do not cross)."""
    vocab.refresh_from_db(db)          # a DB-submitted flow may be the only reactor
    if not vocab.match(ev.event_type):
        raise HTTPException(status_code=400, detail={
            "no_flow_reacts_to": ev.event_type,
            "reactable_event_types": sorted({f.on.name for f in vocab.flows.values()})})
    actor = _actor(x_actor)
    refs = {**ev.refs, "admitted_by": actor}
    n = admit(db, vocab, clock, source_event_id=ev.source_event_id,
              event_type=ev.event_type, subject_refs=refs)
    return _with_gate({"event_type": ev.event_type, "source_event_id": ev.source_event_id,
                       "admitted_by": actor, "reactions_created": n, "duplicate": n == 0})


class SeedRow(BaseModel):
    """One recurring meeting an admin is putting the mailbox on."""
    url: str = Field(min_length=6, max_length=500)
    organizer: str = Field(min_length=3, max_length=254)
    start: float
    title: str = Field(default="Meeting", max_length=200)
    participants: list[str] = Field(default_factory=list)
    ics_uid: Optional[str] = None
    group: Optional[str] = None


class SeedBatch(BaseModel):
    meetings: list[SeedRow] = Field(min_length=1, max_length=500)
    event_type: str = "invite.received"
    prefix: str = Field(default="seed", max_length=40)


@app.post("/events/batch", status_code=202, dependencies=[Depends(auth)])
def admit_batch(batch: SeedBatch, x_actor: str = Header(default="")):
    """PUT THE MAILBOX ON MANY RECURRING MEETINGS AT ONCE — the admin seed, in one call.

    The simulator measured the reach bottleneck at 89-100% of every org never being touched, and
    the only strategies that fix it seed the production office or put the mailbox on every
    recurring dailies. Both are ADMIN actions over N meetings, and until now the product had no
    verb for either: `POST /events` takes one fact, `bot_schedule` takes one meeting, and
    forwarding takes one ICS. Twenty dailies meant twenty of something, with no list accepted
    anywhere and no receipt over the whole set. The machine time was never the cost — the absence
    of a plural was.

    Deliberately still not a step-runner: it admits FACTS, one per meeting, through the same
    `admit()` and the same per-(fact, flow) dedup key as the singular endpoint. What each fact
    causes stays the registry's business. Re-running the same batch is a no-op per meeting, so an
    admin who pastes their list twice does not double-invite anyone.

    Returns a row per meeting rather than a count: a partial success is the normal case (one bad
    url in twenty), and a bare number cannot tell an admin WHICH meeting to fix.
    """
    vocab.refresh_from_db(db)
    if not vocab.match(batch.event_type):
        raise HTTPException(status_code=400, detail={
            "no_flow_reacts_to": batch.event_type,
            "reactable_event_types": sorted({f.on.name for f in vocab.flows.values()})})
    stamp = int(clock.now())
    actor = _actor(x_actor)
    out, admitted, dupes = [], 0, 0
    for i, m in enumerate(batch.meetings):
        sid = m.ics_uid or f"{batch.prefix}-{stamp}-{i:04d}"
        refs = {"organizer": m.organizer, "url": m.url, "start": float(m.start),
                "ics_uid": sid, "title": m.title, "group": m.group,
                "participants": list(m.participants), "admitted_by": actor}
        try:
            n = admit(db, vocab, clock, source_event_id=sid,
                      event_type=batch.event_type, subject_refs=refs)
            admitted += 1 if n else 0
            dupes += 1 if not n else 0
            out.append({"source_event_id": sid, "title": m.title,
                        "reactions_created": n, "duplicate": n == 0})
        except Exception as e:  # noqa: BLE001 — one bad row must never lose the other nineteen
            # CodeQL "information exposure through an exception": `str(e)` can carry whatever the
            # failing row or the failing driver put in the message — a DSN, a header value, a
            # fragment of somebody else's row — and this response goes to an admin caller, not a
            # log. The admin gets a TYPED, stable answer (exception class + a fixed code) that is
            # enough to tell one bad row from another and to file a bug; the full exception,
            # `source_event_id` included, goes to the server log ONLY, where the operator who can
            # already read logs can look it up.
            logger.exception("admit_batch: source_event_id=%s failed", sid)
            out.append({"source_event_id": sid, "title": m.title,
                        "error": type(e).__name__, "error_code": "admit_failed"})
    log = {"admitted_by": actor, "submitted": len(batch.meetings),
           "admitted": admitted, "duplicates": dupes,
           "failed": sum(1 for r in out if "error" in r)}
    return _with_gate({**log, "meetings": out})


@app.post("/flows/{name}/{version}/{action}", dependencies=[Depends(auth)])
def set_flow_status(name: str, version: int, action: str):
    """Activate or retire ONE version of a flow, addressed by its name and version number.

    `action` is `activate` or `retire` and nothing else. Activating makes that version the one
    new facts react on, within about ten seconds; retiring stops it being chosen without
    deleting it or disturbing reactions already running on it — a retired version can be
    activated again, so this is reversible and is the safe way to take a flow out of service.

    Use `GET /flows` to see which versions exist and which one is active. Operator-gated: this
    door reads the deployment's own operator key, never the caller's."""
    if action not in ("activate", "retire"):
        raise HTTPException(status_code=404, detail="activate | retire")
    _refuse_if_gated(f"flows_{action}")
    st = "active" if action == "activate" else "retired"
    rows = db.execute("UPDATE flow_version SET status=:s WHERE name=:n AND version=:v RETURNING name",
                      {"s": st, "n": name, "v": version})
    if not rows:
        raise HTTPException(status_code=404, detail="flow version not found")
    return {"name": name, "version": version, "status": st}


@app.get("/reactions")
def list_reactions(status: Optional[str] = None, subject: str = "",
                   caller: Caller = Depends(subject_or_operator)):
    """ONE PERSON'S share of the reaction queue — or, for the operator, the whole projection.

    Unscoped, this route was fanning the entire instance's reactions into every signed-in user's
    queue: `whats_waiting` reported other tenants' flow names, step names and failure reasons as
    this person's work, and `reactions_list` handed out every reaction id instance-wide — which is
    also how `reaction_signal` could cancel a stranger's pending join (R-D07, R-D12).

    The subject is now DERIVED from the caller's own credential rather than asserted as an
    argument (issue #1468). An operator still reads any subject, or none.

    Scoping reuses `flows_timeline`'s pair — the uid AND the email — for the reason that module
    documents: the invite lineage carries an organizer address and no uid, the completed lineage
    carries a uid and no address, and matching on one of them silently returns half the rows.
    """
    subj = scoped_subject(caller, subject)
    rows = reactions_for(db, subject=subj, status=status or "", identity=_as_me(caller))
    if rows is None:
        return {"reactions": [], "subject": subj, "unresolved": True}
    return {"subject": subj, "reactions": [
        {"id": r["reaction_id"], "flow": f"{r['flow']}@{r['flow_version']}", "step": r["step"],
         "status": r["status"], "attempt": r["attempt"], "reason": r["reason"],
         "next_run_at": r["next_run_at"]}
        for r in rows]}


@app.post("/reactions/{reaction_id}/{verb}")
def signal_reaction(reaction_id: str, verb: str, subject: str = "",
                    x_actor: str = Header(default="api"), body: dict = Body(default={}),
                    caller: Caller = Depends(subject_or_operator)):
    """Steer one reaction — and, for a person, only if it is THEIRS (R-D07).

    Ownership is the right question here rather than operator authority: a person stopping the join
    THEY scheduled with `bot_schedule` is the ordinary path, and an admin-only gate would close it
    to fix an unusual one.

    The check used to run only when the CALLER passed `subject`, which meant a caller who simply
    omitted it got the unscoped behaviour — and every caller reached this route holding the same
    operator key, so nobody's identity ever reached this decision.

    IT NOW RUNS UNCONDITIONALLY, and the `if subj:` it used to sit behind is gone (P20/E1). That
    guard was the last instance-wide write on this surface: an operator who simply omitted
    `subject` cancelled ANYONE's reaction with no check at all — not a weak check, no check — and
    `VEXA_FLOWS_API_KEY` is exported into five compose services. `scoped_subject` now refuses a
    credential that names nobody, so `subj` is always a person and the ownership question is
    always asked. An operator steering a reaction they do not own still can: they say whose it is.
    """
    fns = {"retry": retry, "resume": resume, "cancel": cancel, "wake": wake}
    if verb not in fns:
        raise HTTPException(status_code=404, detail="retry | resume | cancel | wake")
    subj = scoped_subject(caller, subject)
    owns = reaction_concerns(db, reaction_id, subject=subj, identity=_as_me(caller))
    if owns == REACTION_MISSING:
        raise HTTPException(status_code=404, detail="no such reaction")
    if owns != REACTION_FOUND:
        # Deliberately NOT 404: the caller named a real id and a real account, and telling them
        # "no such reaction" for something that exists sends them off to re-derive it.
        raise HTTPException(status_code=403, detail="that reaction is not yours")
    ok = fns[verb](db, reaction_id, actor=x_actor, clock=clock, reason=body.get("reason"))
    if not ok:
        raise HTTPException(status_code=409, detail=f"{verb} not applicable in current status")
    return {verb: True}


@app.get("/timeline")
def timeline(subject: str = "", since: str = "", until: str = "", limit: int = 20,
             meetings: bool = True, format: str = "json",
             caller: Caller = Depends(timeline_reader)):
    """ONE PERSON'S DAY, IN ORDER — PRD decision 31.

    Founder, 2026-09-02: *"does the agent have temporal awareness of the last events and future
    events? scheduled meetings, the things that actually get logged in the flows data"*. It did
    not, and the reason was not that the data was missing: every one of those moments is already a
    reaction row or an effect receipt in this database. What was missing was a read along the axis
    a person thinks in. This is that read.

    `subject` is a platform uid or an email address, and the route resolves the OTHER one before it
    scopes, because the invite lineage carries an organizer and no uid while the completed lineage
    carries a uid and, without the resolution, nothing to match an address on. Scoping on one of
    them silently returns half a day — see `flows_timeline.model.concerns`.

    A PERSON does not send it: their subject is derived from their own credential, and one naming
    anyone else is refused (issue #1468). An operator still asks about whoever they name.

    `since` / `until` take epoch seconds or ISO-8601; the default window straddles NOW (14 days
    back, 30 forward) because half of what decision 31 asks for is in the future. `limit` keeps the
    events NEAREST NOW, not the oldest rows the engine still holds.

    Read-only: it admits nothing, signals nothing and writes nothing. It therefore stays open while
    the instance gate is up, exactly like `GET /flows` and `GET /reactions` — an admin must be able
    to see what the machine has done.

    `meetings=false` drops the gateway hop and answers from this database alone — the offline shape,
    used by the proof and by any caller that cannot reach the gateway.

    `format=text` (a person asking, through the control MCP) and `format=preamble` (the same
    person's agent, told unasked on every dispatch) add a rendered `text`, in THE SUBJECT'S OWN
    ZONE — read here, from `setting(uid, "timezone")`, and not by the caller. That is deliberate:
    two readers rendering the same payload is how a chat and a machinery note end up disagreeing
    about one meeting, and the zone is a fact about the person, which this service can read and a
    worker container cannot.
    """
    subj = scoped_subject(caller, subject)
    if not subj:
        raise HTTPException(status_code=400,
                            detail="subject is required: a platform uid, or an email address")
    if not (subj.isdigit() or "@" in subj):
        raise HTTPException(status_code=400, detail={
            "not_a_subject": subj,
            "expected": "a platform uid (digits) or an email address"})
    # THE NARROW KEY NEVER MINTS. `fetch_meetings` reaches `flows_steps.common.user_api_key`, which
    # asks admin-api for a `["bot","browser","tx"]` gateway token ON THE NAMED SUBJECT'S ACCOUNT —
    # a write, on a third party, from a credential documented as read-only and one-route. So the
    # meetings half is dropped for this tier rather than gated: the answer degrades to this
    # database's own rows, which is the offline shape `meetings=false` already serves and already
    # tests. A person's own credential and the operator key are unchanged.
    want_meetings = bool(meetings) and not caller.is_timeline
    out = build_timeline(db, subj, since=since or None, until=until or None,
                         limit=max(1, min(int(limit or 20), 200)),
                         meetings=fetch_meetings if want_meetings else None,
                         identity=_as_me(caller))
    if out.get("unresolved"):
        raise HTTPException(status_code=404, detail=f"nobody answers to {subj!r}")
    shape = (format or "json").strip().lower()
    if shape in ("text", "preamble"):
        # A zone we cannot read is UTC, stated as UTC — never the server's local time wearing the
        # person's name. `setting` never raises; a missing file means defaults.
        try:
            tz = str(setting(out.get("uid") or subj, "timezone") or "")
        except Exception:  # noqa: BLE001 — a preference lookup must not cost the timeline
            tz = ""
        out = {**out, "timezone": tz or "UTC",
               "text": (render_preamble(out, tz) if shape == "preamble"
                        else render_text(out, tz))}
    return out


# ── friction (PRD 40.9 open-decision 8) ─────────────────────────────────────────────────────────
# Founder, 2026-09-03 09:58Z: "friction is just a sink — we dump that somewhere in production so
# that our dev agent can read it and fix the MCP and behaviour based on it." Friction is not a
# domain and carries no product surface beyond `report_friction` — this route and the one below
# ARE that surface. It lives HERE rather than on agent-api on purpose: `admit()` runs in process,
# with no publish-edge and no config.v1 declaration to wire, which is also what makes it work in
# the no-agents profile — nothing about filing a rough edge should depend on the agent domain being
# deployed. See `core/flows/contracts/flows.v1/carriers.json`'s `friction.reported` entry for the
# carrier's full reasoning and `flows_defs/production.py`'s `record_friction` step for why a flow
# (not just `admit()` alone) has to exist for a report to be visible here at all.
FRICTION_SEVERITIES = ("blocker", "annoyance", "papercut", "idea")
FRICTION_KINDS = ("missing-tool", "refusal", "no-page", "wrong-workspace", "unfulfilled", "error",
                  "ux", "other")
FRICTION_TEXT_MAX = 900

# CATCH ALL SIGNAL — NOTHING HERE IS A TAXONOMY (F-D26, prod 2026-09-04). This route used to answer
# 400 with `{"kind": "...", "expected": [...]}` for any word outside `FRICTION_KINDS`. In twenty
# minutes of real use it threw away TWELVE reports, because the agent filing them wrote "missing",
# "broke" and "confusing" instead of "missing-tool", "error" and "ux" — the tool schema had told it
# nothing, so it guessed, and the sink whose whole job is to catch what did not work refused the
# catch over a spelling.
#
# FOUNDER RULING, 2026-09-04 10:2xZ, which is the shape this route now has: *"we want to catch all
# signal, does not make sense being strict about it, we want rich data, does not have to be too
# structured."* So `kind` and `severity` are STORED AS SENT — free text, no canonicalisation, no
# mapping to `other`, no second field holding the "raw" word, because there is no cooked one. Any
# argument this route does not name is KEPT too, under `extra`. The eight kinds below survive only
# as SUGGESTIONS in the tool description; grouping happens on the data later, by whoever reads it,
# never at the door.
#
# F-D27, prod 2026-09-04 11:0xZ, is the SAME defect one field along, and it is why nothing about a
# report's content is refusable either. F-D26 left two refusals standing — a report with no
# `session` and a report with no text — on the reasoning that those were "about the report existing,
# not about its shape". Prod disagreed within the hour: `POST /friction` answered 400 *"session is
# required"* to a reporter who did not have one, and the report describing that very edge was itself
# thrown away. A MISSING SESSION IS SIGNAL TOO. A report we cannot tie back to a conversation is
# worth strictly more than no report, and the reporter is never the right party to be told no — it
# is us who wanted the join key, so it is us who eat its absence.
#
# THE RULE, GENERALISED, so the next field does not need a third incident: NO VALUE A CALLER CAN
# SEND PRODUCES A 400. Not a word, not a length, not an absence. Over-long values truncate, unknown
# values are kept as sent, and an absent value is stored as a genuine absence — the ref is omitted
# rather than written as `""`, and `friction_for_subject` renders it "no session" for a reader while
# keeping `session_id` empty for a grep. The only refusals
# this route still has are AUTHENTICATION (401 — the edge cannot attribute a report to nobody, and
# an unattributable report is not a poorer report but a different object) and a request that is not
# parseable at all. Note also that every argument below is typed `str`, deliberately: a non-string
# annotation would hand FastAPI a 422 one layer above this function, which is the same refusal with
# somebody else's name on it. `tests/test_friction.py` pins both properties.
#
# No migration: a report lives in `reaction.subject_refs`, which is a JSON document, so every one of
# these is a key inside it and the ten tables in `schema.sql` are untouched.
#
# EXTRAS ARE NAMESPACED, and that is not tidiness. `flows_timeline.model.concerns` decides whose
# report this is by reading `uid`/`subject`/`owner`/`organizer`/… straight off the refs, so merging
# caller-supplied keys into the top level would let a reporter file a report that reads as somebody
# else's. Under `extra` they are data; at the top level they would be authority.
FRICTION_KIND_HELP = {
    "missing-tool": "there was no tool for what was asked (\"summarise this meeting\" and nothing "
                    "summarises)",
    "refusal": "a tool or policy refused (\"speak_in_meeting\" declined, the workspace forbade it)",
    "no-page": "a link or page that should exist did not (a transcript URL 404'd)",
    "wrong-workspace": "the answer came from the wrong account, tenant or workspace",
    "unfulfilled": "the tool answered success but the thing did not actually happen (bot never "
                   "joined, note never saved)",
    "error": "something broke outright — a 500, a crash, a timeout",
    "ux": "it worked but was confusing, slow or awkward to get right",
    "other": "none of the above, or you are not sure — say it in `what_happened` and we will "
             "classify it",
}
FRICTION_KIND_DESC = ("What kind of friction this was — a SUGGESTION, not a list you must pick "
                      "from. Words that group well with others: "
                      + "; ".join(f"`{k}` — {v}" for k, v in FRICTION_KIND_HELP.items())
                      + ". If none fits, send your own word; it is stored exactly as you sent it "
                        "and never refused.")
FRICTION_SEVERITY_DESC = ("How much it hurt. Words that group well: `blocker` (could not "
                          "continue), `annoyance` (worked around it), `papercut` (small and "
                          "repeated), `idea` (nothing broke, this would just be better). Your own "
                          "word is stored as you sent it and never refused.")
#: Everything this route names for itself. Any OTHER query argument is a caller's own field and is
#: kept under `extra` rather than dropped — "rich data, does not have to be too structured".
FRICTION_OWN_ARGS = frozenset({"session", "what_i_tried", "what_happened", "severity", "meeting_id",
                               "tool", "deployment", "worker_image", "kind"})
FRICTION_EXTRA_MAX = 40


#: Whether this process has already said that the query-parameter spelling of `POST /friction` is
#: deprecated. Once per process, like the operator-header deprecation above and for the same
#: reason: printed per request it is a flood on the hot path and nobody reads it.
_FRICTION_QUERY_SAID: set = set()


def _friction_query_sent(params) -> bool:
    """True when the caller put any of this route's own fields in the URL."""
    try:
        return any(name in FRICTION_OWN_ARGS for name in params.keys())
    except Exception:  # noqa: BLE001 — a deprecation notice must never cost a report
        return False


def _say_query_friction_is_deprecated() -> None:
    if "friction-query" in _FRICTION_QUERY_SAID:
        return
    _FRICTION_QUERY_SAID.add("friction-query")
    print("WARNING: POST /friction's QUERY-PARAMETER fields are DEPRECATED — send the report as a "
          "JSON body instead. A report is a person's words about a failure and a query string puts "
          "them in every access log between the caller and this service. Both spellings work for "
          "one release; the body wins when a field is sent twice.", flush=True)


async def _friction_body(request: Request) -> dict:
    """The report's JSON body, or `{}` — and NEVER a refusal (B7 + F-D27).

    A dependency rather than a `Body(...)` parameter, and the difference is the whole rule of this
    route: FastAPI validates a declared body BEFORE the handler runs, so a malformed one answers
    422 — which is a 400 with somebody else's name on it, one layer above every leniency below.
    `tests/test_friction.py::test_no_argument_is_typed_so_that_fastapi_refuses_before_the_route_does`
    already holds that door shut for the query arguments; this keeps it shut for the body.

    ASYNC, and it is the only async callable on this surface: it awaits reading the request body and
    nothing else. The route itself stays `def`, so FastAPI keeps running its database work in the
    threadpool rather than on the event loop.
    """
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001 — a body we cannot read is an absent body, never a refusal
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001 — malformed JSON is the reporter's tooling, not the reporter
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _friction_id() -> str:
    """`fr_<16 hex>` — short enough to paste into a fix reference by hand, same convention the
    (now-retired) agent-api store used, kept for continuity across the cutover."""
    return f"fr_{secrets.token_hex(8)}"


def _friction_extra(params) -> dict:
    """Every argument the caller sent that this route does not name, kept as they sent it.

    Capped in count and length so one call cannot write an unbounded document into a refs blob, and
    the cap DROPS THE OVERFLOW SILENTLY rather than refusing the call — losing the tail of an
    over-large report is bad, losing the whole report is the defect this route exists to not have.

    Takes a query string or a JSON object indifferently: both are mappings of what the caller sent,
    and which transport carried a field is not a fact about the report.
    """
    out: dict = {}
    for key in sorted(params.keys()):
        if key in FRICTION_OWN_ARGS or len(out) >= FRICTION_EXTRA_MAX:
            continue
        value = params.get(key)
        text = (value if isinstance(value, str) else str(value)).strip()
        if text:
            out[key[:100]] = text[:FRICTION_TEXT_MAX]
    return out


@app.post("/friction", status_code=201, summary="Report friction — tell Vexa what did not work")
def report_friction(
    session: str = Query("", description=(
        "The chat or meeting session this happened in — the id of the conversation you are in "
        "right now. INCLUDE IT WHENEVER YOU HAVE IT: it is how the report ties back to the "
        "conversation that produced it, which is most of what makes a report actionable. But it "
        "is not required and never refuses the call — if you do not have one, file anyway.")),
    what_i_tried: str = Query("", description=(
        "What you were attempting, in your own words. Half-formed is fine; so is nothing.")),
    what_happened: str = Query("", description=(
        "What actually happened instead — the error, the wrong answer, the missing page. Say as "
        "much as you have.")),
    severity: str = Query("annoyance", description=FRICTION_SEVERITY_DESC,
                          json_schema_extra={"examples": list(FRICTION_SEVERITIES)}),
    meeting_id: str = Query("", description="The meeting this happened on, if it was about one."),
    tool: str = Query("", description="The tool you called when it went wrong, if it was one."),
    deployment: str = Query("", description="Which deployment you were talking to, if you know."),
    worker_image: str = Query("", description="The worker image/version, if you know it."),
    kind: str = Query("", description=FRICTION_KIND_DESC,
                      json_schema_extra={"examples": list(FRICTION_KINDS)}),
    report: dict = Depends(_friction_body),
    request: Request = None,  # noqa: RUF013 — FastAPI injects it; the default keeps the signature
    x_user_id: str = Header(default=""),
    caller: Caller = Depends(subject_or_operator),
):
    """Report friction: tell us what did not work, so a developer can read it and fix it.

    CALL THIS whenever you could not do what the person asked, or did it badly: there was no tool
    for the job, a tool refused, a page or link was missing, you landed in the wrong workspace, a
    tool said it succeeded but the thing did not happen, something errored outright, or it worked
    but was confusing. You do not need permission and you do not need to be sure it is our fault —
    file it. Nothing is too small, and a report is never lost for being imperfectly labelled.

    THE PAYLOAD IS TWO SENTENCES: `what_i_tried` (what you were attempting) and `what_happened`
    (what happened instead). Send `session` too — the chat or meeting session you are in — whenever
    you have one, because it is what ties the report back to the conversation that produced it and
    that is most of what makes it actionable later.

    NOTHING YOU SEND OR OMIT WILL COST YOU THE REPORT. There is no required field on this route: no
    session, no text, an unknown word, an over-long value — all of it is filed, stored as sent, and
    readable back. A report with no session reads as "no session" and is still a report; a missing
    join key is our problem, not yours, and it is signal in its own right. The one thing that IS
    refused is a call with no credential, because a report attributed to nobody is a different
    object rather than a poorer one. Never withhold a report because you are unsure it is
    well-formed, and never re-file one because a field felt wrong.

    `kind` is a HINT, NOT A MENU. These words group well with other people's reports:
    `missing-tool` (no tool exists for what was asked), `refusal` (a tool or policy said no),
    `no-page` (a link or page that should exist 404'd), `wrong-workspace` (the answer came from the
    wrong account or tenant), `unfulfilled` (a tool reported success and the thing did not actually
    happen), `error` (a 500, a crash, a timeout), `ux` (it worked but was confusing or awkward),
    `other`. `severity` likewise: `blocker`, `annoyance`, `papercut`, `idea`. **If none of them fits
    what you saw, use your own word** — both fields are stored exactly as you send them, and no
    word you can choose will cost you the report. Never re-file because a label felt wrong.

    ANYTHING ELSE YOU KNOW, SEND IT. Arguments this route does not name are kept with the report
    rather than dropped — a stack frame, a request id, a model name, a count. Richer is better;
    structure is somebody else's problem later.

    The subject is your own credential, same as every other person-scoped route here
    (`reactions_list`, `timeline`); an operator with no bearer may stamp `X-User-Id`, same as
    `queue_waiting`. The bare operator key, unstamped, attributes a report to nobody, refused.
    Read your own reports back with `friction_so_far`.
    """
    if caller.must_name_a_subject and not (x_user_id or "").strip():
        raise HTTPException(status_code=401, detail=(
            "report_friction needs your Vexa credential — this edge cannot attribute a report to "
            "nobody"))
    subj = scoped_subject(caller, "", stamped=x_user_id)
    # THE BODY WINS, THE QUERY STILL WORKS (B7). Every field used to arrive as a QUERY PARAMETER,
    # which puts a person's own words about a failure — up to 900 characters of them — into the
    # URL, where every proxy and ingress access log in front of this service copies them down. That
    # flows-api itself does not log them is a property of ONE line (`uvicorn … log_level="warning"`
    # in `main()`), not of the design.
    #
    # The query spelling is KEPT FOR ONE RELEASE, deprecated, because the MCP manifest that is
    # already deployed sends these nine names as query arguments (`vexa_mcp/register.py` puts every
    # DECLARED argument in `params=`), and a sink that starts refusing the transport its own
    # shipped edge uses is the F-D26 failure with a new cause. Same rule as `X-Flows-Admin-Key`
    # above: accept both, prefer the new one, say so once per process.
    body_fields = report if isinstance(report, dict) else {}
    def _field(name: str, sent: str) -> str:
        """The body's value for `name`, else the query's. Absence, not emptiness, decides."""
        if name in body_fields and body_fields[name] is not None:
            value = body_fields[name]
            return value if isinstance(value, str) else str(value)
        return sent
    if request is not None and not body_fields and _friction_query_sent(request.query_params):
        _say_query_friction_is_deprecated()
    # NO 400 BEYOND THIS POINT (F-D27). Everything below truncates or stores an absence; nothing
    # rejects. `sess` empty means the reporter had no session to give — kept out of `refs` entirely
    # rather than written as `""`, so the read model reports a genuine absence instead of a blank
    # that could be mistaken for a session whose id happens to be empty.
    sess = _field("session", session).strip()[:128]
    tried = _field("what_i_tried", what_i_tried).strip()[:FRICTION_TEXT_MAX]
    happened = _field("what_happened", what_happened).strip()[:FRICTION_TEXT_MAX]
    # AS SENT (founder ruling, F-D26). No canonicalisation, no lowercasing, no mapping into a
    # bucket: the word the reporter chose IS the datum, and grouping it with other reports is a
    # question for whoever reads the sink, later, with all of them in front of them.
    sev = _field("severity", severity).strip()[:200] or "annoyance"
    knd = _field("kind", kind).strip()[:200]
    fid = _friction_id()
    refs = {"uid": subj, "friction_id": fid,
           "what_i_tried": tried, "what_happened": happened, "severity": sev}
    if sess:
        refs["session"] = sess
    for key, val in (("kind", knd),
                     ("meeting_id", _field("meeting_id", meeting_id)),
                     ("tool", _field("tool", tool)),
                     ("deployment", _field("deployment", deployment)),
                     ("worker_image", _field("worker_image", worker_image))):
        v = val.strip()[:200] if isinstance(val, str) else ""
        if v:
            refs[key] = v
    extra = dict(_friction_extra(request.query_params) if request is not None else {})
    extra.update(_friction_extra(body_fields))
    extra = dict(sorted(extra.items())[:FRICTION_EXTRA_MAX])
    if extra:
        refs["extra"] = extra
    vocab.refresh_from_db(db)          # the friction_log flow may have been (re)submitted since boot
    # LITERAL, not `production.FRICTION_REPORTED.name` — `tests/test_queue_waiting.py::_produced()`
    # proves `publishes_events` against what this domain's source actually writes to the wire by
    # grepping for `event_type="<literal>"`, the same way it already proves `invite.received`. An
    # attribute expression would be correct at runtime and invisible to that check.
    assert production.FRICTION_REPORTED.name == "friction.reported"
    created = admit(db, vocab, clock, source_event_id=f"friction-{fid}",
                    event_type="friction.reported", subject_refs=refs)
    # `recorded` IS THE ADMISSION'S ANSWER, not this route's intention (B6). It used to be the
    # literal `True`, so a deployment whose `friction_log` flow was retired — or whose registry
    # never loaded it — told every reporter their report was recorded while `admit()` created
    # nothing and the row did not exist. A sink that lies about catching is worse than one that
    # refuses: the reporter stops filing, and nobody learns anything. `admit()` returns how many
    # reactions it created; 0 means no flow matched `friction.reported` (or this exact report was
    # already filed), and the caller is told that in the same field it already reads.
    if not created:
        logger.error(
            "friction %s was NOT recorded: admit() created no reaction for friction.reported "
            "(no flow matches it in this registry, or the report was a duplicate). "
            "%d flow(s) loaded.", fid, len(vocab.flows))
    # The reply STATES what was stored, so a caller can see its own words came back unchanged and
    # can see which of its extra fields were kept — an accepted report that quietly dropped half of
    # itself is the same class of lie as a refused one.
    out = {"id": fid, "recorded": bool(created), "kind": knd, "severity": sev, "session": sess}
    if not created:
        out["note"] = ("this deployment admitted the report into no flow, so it is not readable "
                       "back — the report is not lost on your side, it was never stored on ours")
    if extra:
        out["extra"] = sorted(extra)
    return out


@app.get("/friction")
def friction_so_far(since: str = "", limit: int = 40,
                    x_user_id: str = Header(default=""),
                    caller: Caller = Depends(subject_or_operator)):
    """Your own filed reports, newest first — the subject is derived from your credential, exactly
    like `reactions_list`. `since` takes an epoch or an ISO-8601 instant; empty means everything.

    The whole-instance dump and the close-out verb stay operator-side for now (the rig's
    `friction_dump` / `friction_fixed`) — this is deliberately the narrower half PRD 40.9 asked for
    first, pending a follow-up that gives the operator view the same treatment `GET /reactions`
    already has.
    """
    if caller.must_name_a_subject and not (x_user_id or "").strip():
        raise HTTPException(status_code=400, detail=(
            "no subject — sign in to read your own reports, or stamp X-User-Id. There is no "
            "instance-wide dump behind the operator key on this route."))
    subj = scoped_subject(caller, "", stamped=x_user_id)
    ts = _friction_since_epoch(since) or 0.0 if since else 0.0
    rows = friction_for_subject(db, subject=subj, since=ts,
                                limit=max(1, min(int(limit or 40), 200)),
                                identity=_as_me(caller))
    if rows is None:
        raise HTTPException(status_code=404, detail=f"nobody answers to {subj!r}")
    return {"subject": subj, "count": len(rows), "reports": rows}


@app.post("/friction/{friction_id}/fix", status_code=201, dependencies=[Depends(auth)])
def friction_fixed(friction_id: str, fix_ref: str = ""):
    """Close one report against the change that addressed it (#1510's C3 — PRD decision 33 §4,
    ported onto the flows carrier). OPERATOR ONLY: a fixing agent closes reports filed by ANY
    subject, which only the operator may do — the same authority `friction_dump`'s whole-instance
    read already requires, and the same rule the pre-cutover Redis store enforced.

    `fix_ref` is required and unvalidated on purpose — a commit sha, a PR url, a branch, or one
    sentence naming the change. A record marked fixed with nothing to point at is indistinguishable
    from one somebody wanted off the list. The id is NOT verified against an existing
    `friction.reported` row before admission: `admit()` is fire-and-forget everywhere else on this
    surface (the agent-domain desk carriers do not check either, per their own census entries), and a fix for an id nobody
    ever filed costs nothing beyond an inert reaction row — recurrence, unlike the old store, is not
    resurrected here (see the carrier's own census entry)."""
    fid = friction_id.strip()
    ref = fix_ref.strip()[:300]
    if not fid:
        raise HTTPException(status_code=404, detail="no such friction record")
    if not ref:
        raise HTTPException(status_code=400, detail=(
            "fix_ref is required — a commit, a PR, or a sentence naming the change. A record "
            "marked fixed with nothing to point at is indistinguishable from one somebody wanted "
            "off the list."))
    vocab.refresh_from_db(db)          # the friction_fix flow may have been (re)submitted since boot
    assert production.FRICTION_FIXED.name == "friction.fixed"
    admit(db, vocab, clock, source_event_id=f"friction-fix-{fid}",
         event_type="friction.fixed", subject_refs={"friction_id": fid, "fix_ref": ref})
    return {"id": fid, "status": "fixed", "fix_ref": ref}


def bind_host() -> str:
    """Which interface to listen on — loopback unless the deployment says otherwise.

    THE DEFAULT STAYS `127.0.0.1` ON PURPOSE. This process runs two ways: as a host lane out of
    `flows-up.sh` on the dogfood rig, and (new) as a container on the compose network. In a
    container, loopback is the loopback of that container, so nothing else on the network can reach
    it — which is why the interim wiring had to bind the lane to the docker bridge address by hand
    and write a host-specific address into a deployment.

    Flipping the default to `0.0.0.0` would fix the container and, the same day, publish the host
    lane's port on every interface of the rig box: a deployment change smuggled in as a container
    fix. So the container says `VEXA_FLOWS_API_HOST=0.0.0.0` in its own environment, out loud,
    where an operator reading the compose file can see the exposure and its port binding together.
    """
    return (os.environ.get("VEXA_FLOWS_API_HOST") or "127.0.0.1").strip()


def contract_preflight() -> dict:
    """THE CONFIG.V1 DECLARATION, checked against this process's environment (E6/ADR-0026).

    `config_preflight.py` is vendored here, byte-identical to `deploy/contracts/config.v1/`, and
    until now NOTHING under `core/flows/src` imported it: the validator every adopted service runs
    at boot was dead code in the one service that declares 35 keys. What flows checked instead was
    `flows_config.preflight()` over `DOOR_KEYS`, which is a different and much narrower question —
    so the seven `forbidden_values` the declaration carries were enforced by a hand-copied list of
    four inside this file, and `VEXA_FLOWS_API_KEY=vexa-internal-secret` booted green.

    This is the call the MCP already makes (`vexa_mcp/app.py`: `from .config_preflight import
    preflight; preflight()`), in the same position — first thing, before anything is served.
    It does NOT replace `flows_config.preflight()`: that one refuses a deployment that cannot name
    a door, which is flows' own rule and is not in the declaration.
    """
    from config_preflight import preflight as _contract_preflight
    return _contract_preflight()


def main() -> int:  # pragma: no cover — process entrypoint
    import uvicorn
    # BEFORE THE PORT IS BOUND. `uvicorn.run` runs the lifespan, which runs this again, so a
    # misconfigured deployment would refuse either way — but refusing here means it never listens
    # at all, which is what "no door, no boot" has always meant on this service.
    boot()
    port = int(os.environ.get("VEXA_FLOWS_API_PORT", "18200"))
    host = bind_host()
    print(f"flows-api up on {host}:{port} · vocabulary of {len(vocab.steps)} steps", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


# ── the MCP tool manifest (PRD decision 40) ───────────────────────────────────────────────────
# The domain that owns the door owns the tool. This is flows' declaration of the tools it backs —
# a NAME bound to a ROUTE, who may call it, and which deployments it exists in. The gateway's MCP
# server fetches it at startup and assembles one surface from every deployed domain's.
#
# SERVED, not baked into the assembler: the version that answers is the version that is RUNNING, so
# a deployment cannot advertise a tool this build does not actually serve. The file is committed at
# `core/flows/mcp.tools.v1.json` and this route reads it — one copy, and the tests in
# `core/gateway`'s MCP service put that same file through the assembler.
#
# OPEN, like /health: it names routes and argument names, never data and never a credential. An
# assembler that had to authenticate to discover the surface could not boot before identity did.
_MANIFEST_PATH = _pathlib.Path(__file__).resolve().parents[2] / "mcp.tools.v1.json"


@app.get("/.well-known/mcp-tools.json")
def mcp_tools_manifest():
    """This domain's MCP tool manifest — what the gateway assembles into the one MCP surface."""
    try:
        return _json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail=f"this build carries no tool manifest: {e}") from e


# ── the queue: what is waiting for ONE PERSON (PRD decision 42.2) ─────────────────────────────
# *"what is waiting — maybe it's flows?"* (founder, 2026-09-03 07:43Z; agreed.) What is waiting is
# the set of pending reactions flows already holds for a subject — nothing is unioned at the edge,
# and no other domain contributes items. They publish EVENTS; flow definitions decide what waits.
#
# AT THE END OF THE FILE ON PURPOSE. The projection lives in `flows_queue` and this is a thin
# forward, so the whole of it fits here rather than among the routes it has nothing to do with —
# and decision 15's `auth` work lands on this same module, which a route inserted mid-file would
# collide with for no benefit.
import flows_queue as _flows_queue  # noqa: E402


# THE ONLY TEXT AN AGENT READS BEFORE DECIDING TO CALL THIS, and it says WHEN — which is the one
# thing the route's own mechanics could never say. Ten ordinary sessions with the tools loaded
# never called `whats_waiting`: they were served this route's implementation notes, and a tool
# described by its implementation reads as something for somebody else. Header precedence and
# status codes answer "how do I call it correctly", a question an agent that never calls it does
# not have.
#
# IT IS A `summary`, AND THE ROUTE CARRIES NO DOCSTRING ON PURPOSE. The MCP edge derives a tool's
# description from this route's OpenAPI operation, preferring the docstring and falling back to
# the summary (`core/meetings/services/mcp/src/vexa_mcp/bind.py::_describe`). A docstring here
# would therefore be served to every agent, in front of every call, forever. The maintainer's
# half lives in the comment block below instead, where no agent pays for it by the token.
WHATS_WAITING_SUMMARY = (
    "What your person's Vexa needs right now — call it at the start of a session, after "
    "connecting, and whenever they mention a meeting; each item's `say` is what to tell them. "
    "Returns the queue for the authenticated caller.")

# ── how this route resolves its subject (maintainer's half; deliberately not agent-facing) ─────
#
# THE SUBJECT IS THE AUTHENTICATED CALLER'S, and there are two ways to be one.
#
# A PERSON authenticates with their own Vexa credential and their subject is resolved from it
# (issue #1468). Nothing they send can move it: a `subject` argument naming anyone else is 403,
# and an `X-User-Id` header is ignored outright. That header is a string a caller can type, and
# it is only ever evidence because the OPERATOR key gates it — a service vouching for a person it
# resolved. A verified credential is stronger evidence than any header, so it wins.
#
# THE OPERATOR reads one person's queue on their behalf, and keeps exactly the behaviour this
# route shipped with: `X-User-Id` is the gateway's answer and outranks `?subject=`, and
# `?subject=` is the unstamped console read. Neither ever answers with the instance — no subject
# at all is a 400, because this route answers for ONE person or for nobody.
#
# The distinction matters because this route is NOT always behind the gateway: reached through
# the MCP edge it is addressed directly, and that edge stamps no `X-User-Id` at all — it forwards
# the caller's own credential, which is the whole reason a person's credential has to open the
# door here.
#
# Not opened by `VEXA_FLOWS_TIMELINE_KEY`: that key is documented as opening the timeline and
# nothing else, and quietly widening a key's reach is how a narrow credential stops being narrow.
#
# The answer is DATA plus behavior's words: every sentence a person hears is resolved from
# `behavior/queue/`, read hot, never from this body. See `flows_queue` for why silence there is
# the filter rather than a keyword list here.
@app.get("/queue/waiting", summary=WHATS_WAITING_SUMMARY)
def queue_waiting(subject: str = "", limit: int = 50,
                  x_user_id: str = Header(default=""),
                  caller: Caller = Depends(subject_or_operator)):
    who = scoped_subject(caller, subject, stamped=x_user_id)
    flows = [{"name": f.name, "version": f.version, "on": f.on.name}
             for f in vocab.flows.values()]
    # `identity=` — the same pair the other three subject-scoped routes hand down (B1). Without it
    # this route re-asked admin-api to turn the caller's uid into their address, for a fact
    # `/internal/validate` already returned on the way in; and when admin-api was slow or down that
    # lookup came back half-empty, which drops the whole invite lineage (it carries an organizer
    # address and no uid — `flows_timeline.model.concerns`) out of the hottest read in the product.
    return _flows_queue.waiting(db, subject=who, flows=flows,
                                limit=max(1, min(int(limit), 200)),
                                identity=_as_me(caller))


@app.get("/queue/notices")
def queue_notices(subject: str = "", limit: int = 50,
                  x_user_id: str = Header(default=""),
                  caller: Caller = Depends(subject_or_operator)):
    """THIS PERSON'S STANDING NOTICES — the say text of each waiting item whose copy declared itself
    one, and nothing else.

    A standing notice is something that stays true BETWEEN calls rather than something that just
    happened, so a caller is meant to read it alongside whatever it was already doing rather than
    go looking for it. That is why this answer is the smallest one this surface has: a list of
    sentences, no reaction ids, no flow names, no steps, no typed reasons — cheap enough to ask on
    every call. A caller that wants any of those wants `GET /queue/waiting`.

    WHICH ITEMS ARE NOTICES IS BEHAVIOR'S TO DECIDE, not this route's and not any caller's: a
    say-file under `behavior/queue/` opens with `notice: true` in its front-matter, or it does not.
    That is an admin's file and an admin's edit with no deploy on either side of it — the same file
    and the same edit that already decide whether an item is spoken at all (`flows_queue`,
    `behavior/queue/README.md`). No word of any notice is written here or anywhere in this image.

    Same door as `GET /queue/waiting`, in every particular: a person is resolved from their own
    credential and cannot name anyone else, an operator passes `?subject=` or the gateway stamps
    `X-User-Id`, and no subject at all is a 400 because this route answers for ONE person.
    """
    who = scoped_subject(caller, subject, stamped=x_user_id)
    return _flows_queue.notices(db, subject=who, limit=max(1, min(int(limit), 200)),
                                identity=_as_me(caller))


# THE ENTRYPOINT GUARD IS THE LAST THING IN THIS MODULE, and that is load-bearing rather than
# tidy. It used to sit above the manifest route, and `python -m flows_integrations.flows_api` —
# the compose command, the helm command, the rig's command — runs this file AS `__main__`: the
# guard fires, `main()` blocks inside `uvicorn.run`, and NOTHING BELOW IT IS EVER EXECUTED. So the
# process that every deployment actually runs served /flows, /events and /health and answered 404
# on `/.well-known/mcp-tools.json`, while the offline suite — which IMPORTS the module, where
# `__name__` is not `__main__` and the whole file runs — proved the route existed and served the
# right four tools. Green offline, absent live, and only the first live boot of the compose service
# could tell them apart.
#
# Anything added after this line is invisible to the running service. Add routes ABOVE it.
if __name__ == "__main__":
    raise SystemExit(main())
