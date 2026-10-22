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
    `POST /flows/{name}/{version}/{action}`) take the operator key. They configure the machine or
    admit facts on the instance's behalf; there is no per-person version of either. `POST /events`
    also takes a PRODUCER'S PUBLISH KEY (`PUBLISHERS`, `X-Flows-Publisher-Key`): that producer's
    own facts and sealed envelopes, and no other route.

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

There is NO instance gate on this surface (founder ruling 2026-10-08: "let's remove global setup
at all so that there is no need to setup global at all - let it be empty with no data - it's
fine"). The intakes no longer park facts and the operator verbs no longer refuse while `_global` is
unwritten; the company layer is optional."""
from __future__ import annotations

import hmac
import json
import re
import time
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
from flows import fact_envelope  # noqa: E402 — event.v2: the sealed fact envelope (D8)
from flows_integrations import fact_keys  # noqa: E402 — event.v2: the producers' public keys
from flows.admission import strip_sealed  # noqa: E402
import flows_config  # noqa: E402
import flows_obs  # noqa: E402
import flows_pages  # noqa: E402 — the page renderer `GET /flows/pages` serves (Vexa-ai/vexa#1639)
from flows_defs import production  # noqa: E402
from flows_steps.common import (db_url, internal_secret,  # noqa: E402
                                require_internal_secret, setting)
from flows_integrations import friction_dedup  # noqa: E402 — the repeat counter POST /friction keeps
from flows_integrations.subject_auth import (REFUSAL, SHARED_REFUSAL, Caller,  # noqa: E402
                                              IdentityUnavailable, SubjectUnknown, resolve)
# ALIASED, and it is not style. The route below is also called `list_reactions`, and `def` rebinds
# the module global — so the route was calling ITSELF, and every authenticated `GET /reactions`
# answered 500 (`TypeError: list_reactions() got multiple values for argument 'status'`). The 401
# in front of it hid that for as long as the route was operator-only. One name, one thing.
import config_preflight                                            # noqa: E402
from flows_timeline import (REACTION_FOUND, REACTION_MISSING,  # noqa: E402
                            build_timeline, fetch_meetings, friction_for_subject,
                            friction_sink, reaction_concerns, render_preamble, render_text)
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


#: THE PUBLISH KEYS: a producer's credential for `POST /events` and nothing else, one per producer,
#: each admitting only the facts that producer owns (`flows.v1/carriers.json`, held equal by
#: `tests/test_publish_key.py`) and only that producer's sealed envelopes. A producer that holds one
#: never holds the operator key, which submits, pauses and retires every company's flows.
PUBLISHERS = {
    "crm": ("VEXA_FLOWS_CRM_PUBLISH_KEY", frozenset({"crm.record.changed"})),
}
PUBLISHER_HEADER = "X-Flows-Publisher-Key"


def _publish_keys() -> dict:
    """``{producer: key}`` for every publish key this deployment minted. A placeholder is refused,
    exactly as the timeline key's is: a published literal is not a narrow credential."""
    keys = {}
    for producer, (env, _events) in PUBLISHERS.items():
        key = (os.environ.get(env) or "").strip()
        if key and key in _forbidden_values(env):
            raise RuntimeError(f"{env} is the placeholder {key!r} — refusing to start. Unset it, or "
                               "mint a real value; a published literal is not a narrow credential.")
        if key:
            keys[producer] = key
    return keys


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
PUBLISH_KEYS = {p: k for p, (env, _e) in PUBLISHERS.items() if (k := _configured(env))}
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
    * `_require_api_key()`, `_timeline_key()` and `_publish_keys()` — the credentials this surface
      compares against, re-read here so a booted module holds the validated value rather than the lenient
      one `_configured` left it with.
    * `flows_config.preflight()` — flows' own rule, which is NOT in the declaration: a deployment
      that cannot NAME a door does not run, because there are no host-port defaults.
    * `require_internal_secret()` and the DSN — the internal-tier identity, and the database.

    IDEMPOTENT, and it has to be: `main()` runs it before binding the port and uvicorn runs the
    lifespan after, so an ordinary boot calls it twice. The validator is pure, flows declares no
    probes so it does no I/O, and the declaration is `lru_cache`d — the second call is a dict
    comparison.
    """
    global API_KEY, TIMELINE_KEY, PUBLISH_KEYS, INTERNAL_SECRET, db
    contract_preflight()
    API_KEY = _require_api_key()
    TIMELINE_KEY = _timeline_key()
    PUBLISH_KEYS = _publish_keys()
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
# THE TRACE (logevent.v1): adopt the caller's X-Trace-Id or mint one, bind it for the request, echo
# it; `flows_steps.common.http` forwards it on every call this request makes to a Vexa service.
app.add_middleware(flows_obs.TraceMiddleware, service="flows-api")


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


def publisher(x_flows_operator_key: str = Header(default=""),
              x_flows_admin_key: str = Header(default=""),
              x_flows_publisher_key: str = Header(default="")) -> str:
    """`POST /events`'s caller: "" for the operator, else the producer whose publish key was
    presented (`PUBLISHERS`). Anything else is a 401."""
    if _same_key(_operator_key(x_flows_operator_key, x_flows_admin_key), API_KEY):
        return ""
    presented = (x_flows_publisher_key or "").strip()
    for producer, key in PUBLISH_KEYS.items():
        if presented and _same_key(presented, key):
            return producer
    raise HTTPException(status_code=401, detail=f"{OPERATOR_HEADER} or {PUBLISHER_HEADER} required")


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


#: WHAT A WORKER WITH NOBODY IN THE LOOP MAY DO HERE — every route a person's own credential reaches,
#: classified ONCE. `PERSON_ONLY` acts as the person beyond their own reading (steering a reaction
#: retries, resumes, wakes or cancels what the person scheduled: a bot sent into a meeting, a mail
#: sent, a join called off). `OPEN` reads the person's own rows, or files a friction report into
#: Vexa's own sink — the record `REFUSAL` tells a refused worker to leave. A worker whose regime is
#: not `human` is refused a `PERSON_ONLY` route with `REFUSAL`; a person, a worker in the `human`
#: regime and the operator are not. A route that reaches `subject_or_operator` and is missing here
#: is `PERSON_ONLY` — and `tests/test_subject_bearer.py` fails until it is classified. Operator-only
#: routes (`auth`) take no person's credential and are not listed.
PERSON_ONLY, OPEN = "person-only", "open"
DELEGATED_REACH = {
    ("GET", "/flows"): OPEN,
    ("GET", "/flows/runs"): OPEN,
    # a worker with nobody in the loop may not author, pause or retire anybody's flows (ADR-0046)
    ("POST", "/flows"): PERSON_ONLY,
    ("POST", "/flows/{name}/{version}/{action}"): PERSON_ONLY,
    ("POST", "/flows/{name}/claim"): PERSON_ONLY,
    ("POST", "/routines/triggers"): PERSON_ONLY,
    ("POST", "/concerns/stage"): PERSON_ONLY,
    ("POST", "/event-agents/pause"): PERSON_ONLY,
    ("POST", "/sinks"): PERSON_ONLY,
    ("POST", "/sinks/{name}/unsubscribe"): PERSON_ONLY,
    # ADR-0050: a person's sensors. Reading their health is a read of their own rows; making,
    # changing, testing (a read of their connection), enabling and connecting one is the person's.
    ("GET", "/sensors"): OPEN,
    ("POST", "/sensors"): PERSON_ONLY,
    ("POST", "/sensors/{name}/test"): PERSON_ONLY,
    ("POST", "/sensors/{name}/connect"): PERSON_ONLY,
    ("POST", "/sensors/{name}/{action}"): PERSON_ONLY,
    ("GET", "/flows/pages"): OPEN,
    ("GET", "/reactions"): OPEN,
    ("POST", "/reactions/{reaction_id}/{verb}"): PERSON_ONLY,
    ("GET", "/timeline"): OPEN,
    ("POST", "/friction"): OPEN,
    ("GET", "/friction"): OPEN,
    ("GET", "/queue/waiting"): OPEN,
    ("GET", "/queue/notices"): OPEN,
    ("GET", "/mail/address"): OPEN,
    ("GET", "/mail/delivery"): OPEN,
    # the admin account's mail design: reads are open to its chat worker, every write needs the
    # person in the loop — a test-send and a switch are acts the admin takes, not a routine
    ("GET", "/mail/kinds"): OPEN,
    ("POST", "/mail/preview/{kind}"): OPEN,
    ("PUT", "/mail/templates/{kind}"): PERSON_ONLY,
    ("POST", "/mail/test-send/{kind}"): PERSON_ONLY,
    ("PUT", "/mail/kinds/{kind}"): PERSON_ONLY,
}


def _reach(request: Optional[Request]) -> str:
    """The class of the route this request matched; `PERSON_ONLY` when it cannot be told."""
    route = request.scope.get("route") if request is not None else None
    path = getattr(route, "path", None)
    if not path:
        return PERSON_ONLY
    return DELEGATED_REACH.get((request.method.upper(), path), PERSON_ONLY)


#: The one route a worker whose turn other people read may still reach: filing a friction report
#: into Vexa's own sink reads nothing of its person's (ADR-0048 decision 4).
SHARED_OPEN = {("POST", "/friction")}
#: …and the routes that answer a shared turn with LESS instead of refusing it (friction 7,
#: 2026-10-16): `whats_waiting` in a room answers only the items of the workspaces every reader
#: can read (`flows_queue.room_workspaces`), never the person's own queue.
SHARED_NARROWED = {("GET", "/queue/waiting")}


def _route_key(request: Optional[Request]):
    route = request.scope.get("route") if request is not None else None
    path = getattr(route, "path", None)
    return (request.method.upper(), path) if path else None


def _refuse_unwatched(request: Optional[Request], caller: Caller) -> Caller:
    """403 `SHARED_REFUSAL` for a worker whose turn other people read, on every route but
    `SHARED_OPEN`; 403 `REFUSAL` for a worker with nobody in the loop on a `PERSON_ONLY` route;
    else the caller."""
    if caller.is_shared and _route_key(request) not in SHARED_OPEN | SHARED_NARROWED:
        raise HTTPException(status_code=403, detail=SHARED_REFUSAL)
    if caller.is_unwatched and _reach(request) != OPEN and not _retires_itself(request, caller):
        raise HTTPException(status_code=403, detail=REFUSAL)
    return caller


#: The lifecycle actions a routine granted `flow.retire_self` may take on ITSELF.
SELF_RETIRE_ACTIONS = ("retire", "pause")


def _retires_itself(request: Optional[Request], caller: Caller) -> bool:
    """A routine's own unwatched run retiring or pausing that same routine, which its person granted
    it (`flow.retire_self`, carried on its delegation beside the routine's name). Nothing else: not
    another flow, not activate, not a grant change. Logged as a granted use."""
    if request is None or _route_key(request) != ("POST", "/flows/{name}/{version}/{action}"):
        return False
    params = request.path_params or {}
    if str(params.get("action") or "") not in SELF_RETIRE_ACTIONS:
        return False
    if "flow.retire_self" not in caller.grants or not caller.uid:
        return False
    asked = str(request.query_params.get("scope") or "").strip()
    if asked and asked != f"user:{caller.uid}":
        return False
    if caller.flow != f"user:{caller.uid}/{params.get('name')}":
        return False
    flows_obs.log_event("routine_grant_used",
                        fields={"action": "flow.retire_self", "flow": caller.flow})
    return True


def subject_or_operator(x_flows_operator_key: str = Header(default=""),
                        x_flows_admin_key: str = Header(default=""),
                        authorization: str = Header(default=""),
                        x_api_key: str = Header(default=""),
                        request: Request = None) -> Caller:  # noqa: RUF013 — FastAPI injects it
    """WHO IS CALLING — the operator, or one person. The dependency of every subject-scoped route.

    The operator key is checked first and short-circuits: it is a local constant-time comparison,
    it is what every existing caller sends, and it must keep working while identity is down.

    A worker acting for a person resolves to that person, carrying its delegation; on a route
    `DELEGATED_REACH` does not open to it, one with nobody in the loop is refused here.
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
        caller = resolve(token, secret=INTERNAL_SECRET)
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
    return _refuse_unwatched(request, caller)


def timeline_reader(x_flows_operator_key: str = Header(default=""),
                    x_flows_admin_key: str = Header(default=""),
                    authorization: str = Header(default=""),
                    x_api_key: str = Header(default=""),
                    request: Request = None) -> Caller:  # noqa: RUF013 — FastAPI injects it
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
    return subject_or_operator(x_flows_operator_key, x_flows_admin_key, authorization, x_api_key,
                               request)


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


class FlowSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    #: the event a flow reacts to — or omit it and send `trigger` (flows.v1 `Trigger`)
    on_event: str = Field(default="", max_length=120)
    trigger: Optional[dict] = None
    steps: list[str] = Field(
        min_length=1,
        description="Step NAMES in run order, e.g. [\"agent_run\"] — strings, never objects. What a "
                    "step needs (agent_run's `instruction`, `thread`) goes in `params`.")
    params: dict = Field(default_factory=dict)
    activate: bool = True
    #: WHOSE FLOW (ADR-0046 decision 5). Taken from the caller, never trusted from here: a person
    #: may name only their own scope (or omit it); `instance` needs the operator key or the
    #: deployment's admin account (its own key, or its chat's delegation); the operator key may name
    #: a person (the routine migration).
    scope: Optional[str] = None


#: Typed refusals a submission can meet (ADR-0046 decisions 5, 6, 9). Each is a `detail.refused`.
STEP_NOT_ALLOWED, QUOTA_ACTIVE, QUOTA_INTERVAL = (
    "step_not_allowed", "quota_active_flows", "quota_min_interval")
TOO_LARGE, QUOTA_VERSIONS, QUOTA_DRAFTS = ("submission_too_large", "quota_versions", "quota_drafts")
#: The most one submission's `params` may weigh in a person's scope, as compact JSON (the
#: instruction is in there). A routine's instruction is a paragraph; 16 KB is several pages of it.
SUBMISSION_MAX_BYTES = 16 * 1024
#: How many versions, and how many of them drafts, one person's scope may hold in all. Versions are
#: minted and never overwritten, so without a ceiling one person's resubmissions grow the table
#: without bound. Generous for a person editing their own routines; the instance scope is not
#: capped (only the operator and the instance admin write it).
USER_SCOPE_MAX_VERSIONS = 500
USER_SCOPE_MAX_DRAFTS = 50


def _refuse(code: int, reason: str, **facts):
    raise HTTPException(status_code=code, detail={"refused": reason, **facts})


def _db_flows(scope_filter: Optional[set] = None) -> list:
    """Every runtime-authored version, oldest first — the `flow_version` table as dicts.

    ONE READER, because `GET /flows` and `GET /flows/pages` answer two questions about the same
    rows and a second SELECT is a second column list to keep in step. `created_at` is here for the
    page: a flow somebody wrote from the governance chat says on its own page who activated it and
    when, and neither fact is anywhere else.

    `scope_filter` keeps only the named scopes — a person reads the instance's and their own, never
    anybody else's (ADR-0046 decision 5)."""
    rows = db.execute("SELECT scope, name, version, on_event, cron, steps, params, status, "
                      "created_by, created_at FROM flow_version ORDER BY scope, name, version")
    out = []
    for sc, n, v, e, cron, st, p, status, by, at in rows:
        sc = sc or "instance"
        if scope_filter is not None and sc not in scope_filter:
            continue
        params = json.loads(p or "{}")
        trigger = ({"kind": "at", "at": params.get("at"), "cron": cron}
                   if cron and params.get("once") else
                   {"event": e, **params["_trigger"]} if isinstance(params.get("_trigger"), dict) else
                   {"kind": "schedule", "cron": cron} if cron else {"kind": "event", "event": e})
        from flows_steps import grants as _g
        granted = _g.clean([x for x in (params.get("grants") or []) if x in _g.VOCAB]) \
            if isinstance(params.get("grants"), list) else []
        out.append({"name": n, "version": v, "scope": sc, "on": e, "trigger": trigger,
                    "steps": json.loads(st), "params": params, "status": status,
                    "grants": granted, "may": _g.may_sentence(granted),
                    "created_by": by, "created_at": str(at or ""), "source": "api"})
    return out


def _readable_scopes(caller: Caller) -> Optional[set]:
    """None = every scope (the operator); else the instance's and the caller's own."""
    if caller.is_admin:
        return None
    return {"instance", f"user:{caller.uid}"} if caller.uid else {"instance"}


@app.get("/flows")
def list_flows(caller: Caller = Depends(subject_or_operator)):
    """Every flow this engine knows that you may see, and the step vocabulary flows are built out of.

    Read this when you need to know what this deployment can actually DO before promising it: each
    entry names the flow, its version, its `scope` (`instance`, or `user:<uid>` for a person's own),
    its `trigger` (an event, or a schedule's cron in UTC) and its ordered steps, and `source` says
    whether it came from the image or was submitted through the API. A person sees the instance's
    flows and their own, never anybody else's.

    `steps_vocabulary` is every step name with its own one-line description and whether a person's
    own flow may use it (`user_allowed`). A person's routine that did not run says why in
    `last_refusal` (`reason`, `text`, `at`, `next_run_at` — e.g. the daily run limit and when the
    next run can start), so "why didn't it run" has an answer. `shadowing_versions` names any runtime version that
    overrides an image version with fewer steps — the shape of a flow quietly doing less than the
    code says, surfaced here because nobody reads a startup log until they already suspect it."""
    db_flows = _with_refusals(_db_flows(_readable_scopes(caller)))
    code_flows = [{"name": f.name, "version": f.version, "scope": "instance", "on": f.on.name,
                   "trigger": {"kind": "event", "event": f.on.name},
                   "steps": list(f.steps), "source": "image", "status": "active"}
                  for (ident, _v), f in vocab.flows.items() if (ident, _v) not in vocab.db_versions]
    allowed = _user_allowed_steps(caller.uid)
    # RUNTIME VERSIONS THAT SHADOW THE IMAGE'S with fewer steps — the F57 class. Surfaced on the
    # listing rather than only in a startup log, because the log is read when somebody already
    # suspects something and this is the defect where nobody does.
    return {"shadowing_versions": vocab.shadowing_versions(),
            "steps_vocabulary": [
        {"name": n, "doc": " ".join((vocab.steps[n].__doc__ or "undocumented").split()),
         "user_allowed": n in allowed}
        for n in sorted(vocab.steps)],
        "flows": code_flows + db_flows}


def _with_refusals(flows: list) -> list:
    """Each person's flow gains `last_refusal` when a schedule fire was refused in the last day."""
    since = clock.now() - 86_400.0
    rows = db.execute("SELECT flow, subject_refs, created_at FROM reaction "
                      "WHERE event_type = 'schedule.refused' AND created_at > :t "
                      "ORDER BY created_at DESC", {"t": since})
    latest: dict = {}
    for ident, refs, at in rows:
        if ident not in latest:
            r = json.loads(refs or "{}")
            latest[ident] = {"reason": r.get("refused"), "text": r.get("text"), "at": float(at),
                             "next_run_at": r.get("next_run_at")}
    from flows.admission import runs_today
    cap = flows_config.get_int("VEXA_FLOWS_USER_RUNS_PER_DAY")
    used: dict = {}
    for f in flows:
        scope = f.get("scope", "instance")
        if scope == "instance":
            continue
        ident = f"{scope}/{f.get('name')}"
        f["kind"] = ("sink" if (f.get("steps") or [None])[0] == "post_event_card" else
                     "routine" if (f.get("trigger") or {}).get("kind") == "schedule" else "event_agent")
        hit = latest.get(ident)
        if hit:
            f["last_refusal"] = hit
        paused = (f.get("params") or {}).get("paused") if isinstance(f.get("params"), dict) else None
        if f.get("status") != "active" and isinstance(paused, dict) and paused.get("text"):
            # WHY A ROUTINE PAUSED ITSELF (Vexa-ai/vexa#1783): its chat became shared and a run
            # needed something only its owner reads. The strip says it; the owner's resume clears it.
            f["paused_reason"] = str(paused["text"])
        owner = scope.split(":", 1)[-1]
        if owner not in used:
            used[owner] = runs_today(db, owner, clock.now(), scheduled=True)
        f["runs_today"], f["runs_per_day"] = used[owner], cap
        cron = (f.get("trigger") or {}).get("cron")
        if cron:
            # SAID, NOT GUESSED: the schedule in words and its next slot, so an agent reading its
            # own flows states them instead of inferring them from the cron (founder, 2026-10-14)
            from flows.schedule import next_slot, parse
            f["schedule"] = schedule_words(cron)
            try:
                f["next_run_at"] = next_slot(parse(cron), clock.now())
            except Exception:  # noqa: BLE001 — an unreadable cron has no next run to state
                f["next_run_at"] = None
        last = _last_run(ident)
        if last:
            f["last_run"] = last
        # WHAT IS ARMED AND WHAT FEEDS IT (ADR-0050): a keyed flow's next fires, and the sensor a
        # connected chat's sink reads from, with its health — what the routine strip shows.
        params = f.get("params") or {}
        if isinstance(params.get("_trigger"), dict) and params["_trigger"].get("kind") == "relative":
            from flows import keyed as _keyed
            f["kind"] = "keyed"
            f["next_fires"] = _keyed.upcoming(db, scope, f.get("name"))
        sensor = str((params.get("filter") or {}).get("_sensor") or "") if isinstance(
            params.get("filter"), dict) else ""
        if sensor and "/" in sensor:
            from flows_sensors import pool as _pool
            sc, _, nm = sensor.rpartition("/")
            got = _pool.health(db, sc, nm)
            if got:
                f["sensor"] = got[0]
    return flows


def _hhmm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def schedule_words(cron: str) -> str:
    """A 5-field UTC cron in words: "every 30 min, 06:00–21:30 UTC", "daily at 09:00 UTC",
    "weekdays at 07:30 UTC", "hourly at :05" — else the cron itself."""
    p = str(cron or "").split()
    if len(p) != 5:
        return str(cron or "")
    mi, ho, dom, mon, dow = p
    days = "" if dow == "*" else (" on weekdays" if dow == "1-5" else f" on days {dow}")
    if dom != "*" or mon != "*":
        return cron
    import re as _re
    step = _re.fullmatch(r"\*/(\d+)", mi)
    if step:
        n = int(step.group(1))
        span = _re.fullmatch(r"(\d+)-(\d+)", ho)
        if ho == "*":
            return f"every {n} min{days}"
        if span:
            a, b = int(span.group(1)), int(span.group(2))
            last = (60 - n) if 60 % n == 0 else (59 // n) * n
            return f"every {n} min, {_hhmm(a, 0)}–{_hhmm(b, last)} UTC{days}"
    if mi.isdigit() and ho == "*":
        return f"hourly at :{int(mi):02d}{days}"
    if mi.isdigit() and ho.isdigit():
        return (f"at {_hhmm(int(ho), int(mi))} UTC{days}" if days
                else f"daily at {_hhmm(int(ho), int(mi))} UTC")
    return cron


def _last_run(ident: str):
    """The newest run of one flow — when, how it ended, and each step's last state and duration —
    for the routine strip and the flow graph. None when it never ran."""
    rows = db.execute("SELECT reaction_id, status, reason, step, created_at, updated_at FROM reaction "
                      "WHERE flow = :f AND event_type <> 'schedule.refused' "
                      "ORDER BY created_at DESC LIMIT 1", {"f": ident})
    if not rows:
        return None
    rid, status, reason, step, created, updated = rows[0]
    rec = db.execute("SELECT step, state, result, attempted_at FROM effect_receipt "
                     "WHERE reaction_id = :r ORDER BY attempted_at", {"r": rid})
    steps: dict = {}
    prev = float(created or 0)
    for st, state, result, at in rec:
        at = float(at or prev)
        steps[st] = {"state": state, "at": at, "duration_s": max(0.0, round(at - prev, 1))}
        prev = at
        res = _json_or_none(result)
        if isinstance(res, dict) and res.get("session") and "session" not in steps.get("_run", {}):
            steps["_run"] = {"session": res.get("session")}
    run = steps.pop("_run", {})
    return {"id": rid, "status": status, "reason": reason, "step": step,
            "started_at": float(created or 0), "ended_at": float(updated or 0),
            "duration_s": max(0.0, round(float(updated or 0) - float(created or 0), 1)),
            "steps": steps, **run}


def _user_allowed_steps(uid: str = "") -> set:
    """The steps a person's flow may name: `_global/POLICIES.md` `user_flow_steps` within the
    vocabulary, or the image's user-allowed marks when the policy says nothing (decision 6)."""
    from flows_steps import policies
    try:
        rules = policies.read(uid) if uid else policies.resolve(None)
    except Exception:  # noqa: BLE001 — a policy we cannot read is the image's marks
        rules = policies.resolve(None)
    return policies.user_flow_steps(rules, vocab.user_allowed, vocab.steps)


def _names_own_scope(caller: Caller, asked: str) -> bool:
    """Does `asked` name the caller's own scope — `user:<uid>`, or `user:<address>` with the address
    identity answered for this credential? Agents often know their person's address and not the
    uid; both spell the same person, and the scope is always WRITTEN with the uid."""
    if not asked.startswith("user:") or not caller.uid:
        return False
    return asked[len("user:"):].strip().lower() in caller.names


def _author_scope(caller: Caller, asked: Optional[str]) -> str:
    """THE SCOPE A SUBMISSION WRITES, decided by who is calling (ADR-0046 decision 5)."""
    asked = (asked or "").strip()
    if caller.is_admin:
        if not asked or asked == "instance":
            return "instance"
        if asked.startswith("user:") and len(asked) > 5 and "/" not in asked:
            return asked
        _refuse(400, "bad_scope", scope=asked, note="instance, or user:<uid>")
    if not caller.uid:
        raise HTTPException(status_code=403, detail="this credential cannot author flows")
    own = f"user:{caller.uid}"
    if not asked or asked == own or _names_own_scope(caller, asked):
        return own
    if asked == "instance" and caller.instance_admin:
        return "instance"
    _refuse(403, "not_your_scope", scope=asked, you_are=own,
            note="a person writes their own flows; the instance's are the operator's or the "
                 "deployment's admin account's")


def _trigger_of(sub: FlowSubmission) -> tuple[str, Optional[str]]:
    """(on_event, cron) from the submission — `trigger` when sent, else the legacy `on_event`."""
    from flows.registry import SCHEDULE_EVENT
    from flows.schedule import CronError, parse
    trig = sub.trigger or {}
    kind = str(trig.get("kind") or ("event" if sub.on_event else "")).strip()
    if kind == "schedule":
        cron = str(trig.get("cron") or "").strip()
        try:
            parse(cron)
        except CronError as e:
            _refuse(400, "bad_cron", cron=cron, note=str(e))
        return SCHEDULE_EVENT, cron
    if kind == "at":
        at = _at_epoch(trig.get("at"))
        tm = time.gmtime(at)
        return SCHEDULE_EVENT, f"{tm.tm_min} {tm.tm_hour} {tm.tm_mday} {tm.tm_mon} *"
    if kind == "relative":
        # A KEYED SCHEDULE (ADR-0050): `{kind: relative, event: "calendar.event.*", filter,
        # at: "payload.start - 60s", key: "payload.event_id"}` — one pending fire per key, moved by
        # each matching event, cancelled when one no longer passes or is cancelled.
        from flows.triggers import TriggerError, check_glob
        try:
            return check_glob(trig.get("event") or ""), None
        except TriggerError as e:
            _refuse(400, "bad_trigger", note=str(e))
    if kind == "event":
        event = str(trig.get("event") or sub.on_event or "").strip()
        if "*" in event:
            from flows.triggers import TriggerError, check_glob
            try:
                event = check_glob(event)
            except TriggerError as e:
                _refuse(400, "bad_trigger", note=str(e))
        if not event:
            _refuse(400, "no_trigger", note="send trigger {kind: event, event} or {kind: schedule, cron}")
        if event == SCHEDULE_EVENT:
            _refuse(400, "reserved_event", event=event,
                    note="schedule.due is fired by the worker; send trigger {kind: schedule, cron}")
        return event, None
    _refuse(400, "no_trigger", note="send trigger {kind: event, event}, {kind: schedule, cron} "
                                    "or {kind: at, at}")


#: How far ahead a one-shot may be set. A cron names a day of a year, so a time further out than a
#: year could not be told apart from the same day next year.
AT_HORIZON_S = 360 * 86400


def _at_epoch(raw) -> float:
    """A one-shot's time as UTC epoch seconds, on the minute — from epoch seconds or an ISO-8601
    time with its offset (`2027-03-01T13:13:00+01:00`; `Z` is UTC). Refused when it has no offset,
    is in the past, or is further out than :data:`AT_HORIZON_S`."""
    from datetime import datetime
    try:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            at = float(raw)
        else:
            text = str(raw or "").strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                _refuse(400, "bad_at", at=raw, note="say the time with its offset, e.g. "
                        "2027-03-01T13:13:00+01:00, or as UTC epoch seconds")
            at = parsed.timestamp()
    except (TypeError, ValueError):
        _refuse(400, "bad_at", at=raw, note="an ISO-8601 time with its offset, or UTC epoch seconds")
    at -= at % 60
    now = clock.now()
    if at < now - 60:
        _refuse(400, "at_in_the_past", at=at, now=now)
    if at > now + AT_HORIZON_S:
        _refuse(400, "at_too_far", at=at, maximum_s=AT_HORIZON_S)
    return at


def _grants_for(scope: str, sub: "FlowSubmission") -> list:
    """WHAT THIS ROUTINE MAY DO UNATTENDED, as the person named it (`params.grants`), checked against
    the vocabulary and the company's cap (flows phase 0). A no-LLM step that acts for the person
    needs its grant named here: `request_meeting_bot` needs `bot.request`."""
    from flows_steps import grants as g
    from flows_steps import policies
    try:
        named = g.clean((sub.params or {}).get("grants"))
    except g.GrantError as e:
        _refuse(400, "grant_unknown", unknown=e.unknown, known=list(g.VOCAB),
                note="params.grants names what this routine may do on its own")
    if "request_meeting_bot" in sub.steps and "bot.request" not in named:
        _refuse(400, "grant_required", step="request_meeting_bot", grant="bot.request",
                note="a routine that sends a bot names params.grants: [\"bot.request\"]; the person "
                     "is told \"This routine may: send bots to meetings\"")
    if not named:
        return []
    uid = scope[len("user:"):] if scope.startswith("user:") else ""
    try:
        rules = policies.read(uid) if uid else policies.resolve(None)
    except Exception:  # noqa: BLE001 — a policy we cannot read grants only the defaults
        rules = policies.resolve(None)
    over = [x for x in named if x not in g.allowed(rules)]
    if over:
        _refuse(403, "grant_not_allowed", grants=over, allowed=sorted(g.allowed(rules)),
                note="_global/POLICIES.md routine_unattended_actions does not allow it")
    return named


def _policies_mod():
    from flows_steps import policies  # noqa: PLC0415
    return policies


def _quota(name: str) -> int:
    return flows_config.get_int(name)


def _check_user_flow(scope: str, sub: FlowSubmission, cron: Optional[str], *,
                     proposed: bool = False) -> None:
    """The allow-list and the quotas a PERSON's flow meets (ADR-0046 decisions 6 and 9). A
    `proposed` flow (an agent's, D24) is filed as a draft, so it meets the draft quota."""
    uid = scope[len("user:"):]
    allowed = _user_allowed_steps(uid)
    for step in sub.steps:
        if step not in allowed:
            _refuse(403, STEP_NOT_ALLOWED, step=step, allowed=sorted(allowed),
                    note="a person's own flow may name only the steps `user_flow_steps` in "
                         "_global/POLICIES.md allows")
    if "agent_run" in sub.steps and not str((sub.params or {}).get("instruction") or "").strip():
        _refuse(400, "instruction_required", step="agent_run",
                note="agent_run needs params.instruction — what the agent should do each run")
    if cron:
        from flows.schedule import min_interval
        floor = _policies_mod().company_quota("routine_min_interval_minutes", "VEXA_FLOWS_USER_MIN_INTERVAL_S", scale=60)
        gap = min_interval(cron)
        if gap < floor:
            _refuse(403, QUOTA_INTERVAL, cron=cron, interval_s=int(gap), minimum_s=floor)
    held = db.execute("SELECT COUNT(*) FROM flow_version WHERE scope = :sc", {"sc": scope})
    if int(held[0][0] or 0) >= USER_SCOPE_MAX_VERSIONS:
        _refuse(403, QUOTA_VERSIONS, versions=int(held[0][0]), maximum=USER_SCOPE_MAX_VERSIONS,
                note="retire or delete old versions before submitting more")
    if not sub.activate or proposed:
        drafts = db.execute("SELECT COUNT(*) FROM flow_version WHERE scope = :sc "
                            "AND status = 'draft'", {"sc": scope})
        if int(drafts[0][0] or 0) >= USER_SCOPE_MAX_DRAFTS:
            _refuse(403, QUOTA_DRAFTS, drafts=int(drafts[0][0]), maximum=USER_SCOPE_MAX_DRAFTS)
    if sub.activate and not proposed:
        rows = db.execute("SELECT DISTINCT name FROM flow_version WHERE scope = :sc "
                          "AND status = 'active'", {"sc": scope})
        active = {r[0] for r in rows}
        cap = _quota("VEXA_FLOWS_USER_ACTIVE_FLOWS")
        if sub.name not in active and len(active) >= cap:
            _refuse(403, QUOTA_ACTIVE, active=len(active), maximum=cap)


@app.post("/flows", status_code=201)
def submit_flow(sub: FlowSubmission, x_actor: str = Header(default="api"),
                caller: Caller = Depends(subject_or_operator)):
    """Author a new version of a flow: a trigger — an event to react to, or a schedule — and the
    ordered steps to run. A routine is a flow of your own with a schedule and the `agent_run` step.

    The flow is YOURS: with your own credential it is written in your scope (`user:<you>`), runs
    only for events about you, and nobody else can read, change or retire it. `scope` may be
    omitted; naming anybody else's is refused. The instance's flows are written with the operator
    key or by the deployment's admin account (or its chat) asking for `scope: instance`.

    `trigger` is `{"kind": "event", "event": "<event_type>"}` or `{"kind": "schedule", "cron":
    "<minute hour day month weekday>"}` in UTC (`on_event` is still accepted for an event). `steps`
    may only name steps this deployment carries — `GET /flows` lists them under `steps_vocabulary`,
    with `user_allowed` marking the ones your own flow may use. `agent_run` needs
    `params.instruction`, what your agent should do each run, and takes `params.thread`: the id
    of the chat you are creating it from (your turn states it) — each run then continues that
    chat; without it the routine gets a chat of its own; `params` must fit in 16 KB. A refusal
    says why in `detail.refused`: `step_not_allowed` (naming the step), `quota_active_flows`,
    `quota_min_interval`, `quota_versions`, `quota_drafts`, `submission_too_large`, `bad_cron`,
    `not_your_scope`.

    Versions are minted, never overwritten. Submitting a name that already exists in the same scope
    files the NEXT version beside it; runs already in flight keep the version they started on.
    `activate` (default true) makes it live within about ten seconds; false files it as a draft.
    FROM AN AGENT TURN IT IS ALWAYS A DRAFT (`awaiting_approval: true`): tell your person it is
    waiting on their Routines board, where they read it and turn it on themselves."""
    scope = _author_scope(caller, sub.scope)
    size = len(json.dumps(sub.params, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if scope != "instance" and size > SUBMISSION_MAX_BYTES:
        _refuse(413, TOO_LARGE, bytes=size, maximum=SUBMISSION_MAX_BYTES,
                note="params (the instruction among them) must fit in 16 KB")
    on_event, cron = _trigger_of(sub)
    # THE TRIGGER BEYOND THE ROW (ADR-0050): a filter, or a relative time with its key, is stored as
    # the engine-owned `params._trigger`; a person's own `_trigger` param is never taken as one.
    sub.params = {k: v for k, v in (sub.params or {}).items() if k != "_trigger"}
    _kind = str((sub.trigger or {}).get("kind") or "")
    if _kind in ("relative", "event") and ((sub.trigger or {}).get("filter") or _kind == "relative"):
        from flows.triggers import TriggerError, check_spec
        try:
            sub.params["_trigger"] = check_spec(sub.trigger or {})
        except TriggerError as e:
            _refuse(400, "bad_trigger", note=str(e))
    at = None
    if str((sub.trigger or {}).get("kind") or "") == "at":
        # A ONE-SHOT (flows phase 0): the worker fires it at its minute, once, then retires it.
        at = _at_epoch((sub.trigger or {}).get("at"))
        sub.params = {**(sub.params or {}), "at": at, "once": True}
    missing = [s for s in sub.steps if s not in vocab.steps]
    if missing:
        raise HTTPException(status_code=400,
                            detail={"unknown_steps": missing, "vocabulary": sorted(vocab.steps)})
    granted: list = []
    proposed = caller.is_delegated
    if sub.activate and not proposed:
        _require_session_to_arm(caller, scope)
    if scope != "instance":
        _check_user_flow(scope, sub, cron, proposed=proposed)
        granted = _grants_for(scope, sub)
        params = {k: v for k, v in (sub.params or {}).items() if k != "grants"}
        sub.params = {**params, **({"grants": granted} if granted else {})}
        if "request_meeting_bot" in sub.steps and not str(
                (sub.params or {}).get("meeting_url") or "").strip():
            _refuse(400, "meeting_url_required", step="request_meeting_bot",
                    note="params.meeting_url — the meeting the bot joins")
    row = db.execute("SELECT COALESCE(MAX(version),0) FROM flow_version WHERE scope=:sc AND name=:n",
                     {"sc": scope, "n": sub.name})
    code_max = (max([v for (fn, v) in vocab.flows if fn == sub.name and (fn, v) not in
                     vocab.db_versions], default=0) if scope == "instance" else 0)
    version = max(row[0][0], code_max) + 1
    # AN AGENT PROPOSES A FLOW; A PERSON ARMS IT (D24, H8). A worker acting for a person — any
    # delegated caller, watched or not — files a DRAFT whatever `activate` it sent. It goes live
    # only when the person turns it on from their own session (the Routines board, through
    # agent-api), never from an agent turn: injected text must not be able to plant a standing
    # routine that runs every day as its owner.
    status = "active" if (sub.activate and not proposed) else "draft"
    by = x_actor if caller.is_admin else (caller.email or f"user:{caller.uid}")
    db.execute("""INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params,
                                            status, created_by, created_at)
                  VALUES (:sc,:n,:v,:e,:c,:s,:p,:st,:by,:t)""",
               {"sc": scope, "n": sub.name, "v": version, "e": on_event, "c": cron,
                "s": json.dumps(sub.steps), "p": json.dumps(sub.params), "st": status,
                "by": by, "t": clock.now()})
    if status == "active":
        _supersede(scope, sub.name, version)
    if granted:
        flows_obs.log_event("routine_grant_recorded",
                            fields={"flow": f"{scope}/{sub.name}", "version": version,
                                    "grants": granted})
    from flows_steps import grants as _g
    return {"name": sub.name, "scope": scope, "version": version, "status": status,
            "trigger": ({"kind": "at", "at": at} if at is not None else
                        {"event": on_event, **sub.params["_trigger"]} if sub.params.get("_trigger") else
                        {"kind": "schedule", "cron": cron} if cron
                        else {"kind": "event", "event": on_event}),
            "grants": granted, "may": _g.may_sentence(granted),
            "live_within_s": 10 if status == "active" else None,
            **({"awaiting_approval": True, "note": APPROVAL_NOTE} if proposed else {})}


@app.get("/flows/pages")
def flow_pages(bodies: int = 1, only: str = "", scope: str = "instance",
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
    # ONE SCOPE PER ANSWER (ADR-0046). The instance's pages land in `_global/flows/`, which every
    # person reads, so a person's own flows must never be in that answer; `scope=user:<uid>` is the
    # per-person mirror, readable by that person and the operator only.
    scope = (scope or "instance").strip()
    readable = _readable_scopes(caller)
    if readable is not None and scope not in readable:
        raise HTTPException(status_code=403, detail={"refused": "not_your_scope", "scope": scope})
    pages = flows_pages.runtime_pages(vocab, _db_flows({scope}))
    if wanted:
        pages = [p for p in pages
                 if p["file"] in wanted or f"{p['flow']}@{p['version']}" in wanted]
    if not bodies:
        pages = [{k: v for k, v in p.items() if k != "body"} for p in pages]
    return {"dir": flows_pages.PAGES_DIR[-1], "scope": scope, "pages": pages}


class EventSubmission(BaseModel):
    """A fact. Not a command — the caller says what HAPPENED and the registry decides what
    reacts. `source_event_id` is the caller's own identifier for the occurrence and is the whole
    idempotency story: the same id twice creates nothing the second time."""
    event_type: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=200)
    refs: dict = Field(default_factory=dict)
    # event.v2 (D8): the producer's SEALED fact envelope — who the fact reaches, signed per producer.
    # Optional: a fact without one admits by subject only, exactly as before.
    envelope: Optional[dict] = None


#: The producers whose sealed envelopes the intake admits (D8), each under its own public key
#: (`fact_keys.checks_from_dir`). `meetings` and `agent` are listed ahead of their producers sealing:
#: adopting the envelope there is that producer's own keypair and its public half in the verify
#: directory, nothing here.
ENVELOPE_PRODUCERS = frozenset({"crm", "meetings", "agent"})
#: The directory of the producers' PUBLIC keys, `<producer>.pem` (flows_config: a capability).
FACT_VERIFY_KEYS_DIR_ENV = "VEXA_FACT_VERIFY_KEYS_DIR"


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


@app.post("/events", status_code=202)
def admit_event(ev: EventSubmission, x_actor: str = Header(default=""),
                producer: str = Depends(publisher)):
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
    if producer and ev.event_type not in PUBLISHERS[producer][1]:
        # A PUBLISH KEY ADMITS ITS PRODUCER'S OWN FACTS (carriers.json `owner`), nothing else.
        raise HTTPException(status_code=403, detail={
            "refused": "not_this_producers_fact", "producer": producer,
            "publishes": sorted(PUBLISHERS[producer][1])})
    vocab.refresh_from_db(db)          # a DB-submitted flow may be the only reactor
    if ev.event_type in ("meeting.signal", "room.signal"):
        # ONE PRODUCER (transcript signals): a meeting signal comes from the signals service, over
        # the internal tier, at `/events/signal` — never through the operator's intake.
        raise HTTPException(status_code=403, detail={
            "refused": "signals_only", "note": "meeting.signal is posted by the signals service"})
    if not vocab.match(ev.event_type):
        raise HTTPException(status_code=400, detail={
            "no_flow_reacts_to": ev.event_type,
            "reactable_event_types": sorted({f.on.name for f in vocab.flows.values()})})
    if ev.event_type == "schedule.due":
        raise HTTPException(status_code=400, detail={
            "refused": "reserved_event", "note": "schedule.due is fired by the flows worker"})
    # a producer's key names who admitted the fact; only the operator's intake records X-Actor
    actor = f"producer:{producer}" if producer else _actor(x_actor)
    # A CALLER CANNOT CLAIM AN AUDIENCE (D8): `_audience`/`_owner`/`_visibility`/`_lifecycle` come
    # off the caller's refs first, and are set (by `admit`, from `sealed`) only from an envelope
    # whose seal verifies over THIS fact — its type, its id and its refs exactly as posted — and has
    # not expired. One that does not verify is refused, never admitted as if unsigned (P18).
    refs = {**strip_sealed(ev.refs), "admitted_by": actor}
    sealed = None
    if ev.envelope is not None:
        producers = frozenset({producer}) if producer else ENVELOPE_PRODUCERS
        sealed = fact_envelope.verify(ev.envelope, fact_keys.checks_from_dir(os.environ.get(FACT_VERIFY_KEYS_DIR_ENV), producers), producers,
                                      fact_envelope.fact(ev.event_type, ev.source_event_id, ev.refs))
        if sealed is None:
            raise HTTPException(status_code=403, detail={"refused": "envelope_unverified"})
    if ev.event_type == "thread.message":
        _check_message_policy(refs)
    refusals: list = []
    n = admit(db, vocab, clock, source_event_id=ev.source_event_id,
              event_type=ev.event_type, subject_refs=refs, refusals=refusals,
              sealed=sealed, **_admission_limits())
    flows_obs.log_event("event_admitted", fields={"event_type": ev.event_type,
                                                  "reactions_created": n, "duplicate": n == 0})
    if ev.event_type == "thread.message" and not n and refusals:
        # A MESSAGE THE GUARDS REFUSED IS SAID, NOT SWALLOWED (ADR-0046 decisions 8, 9, 13): the
        # sending agent is waiting on this answer to tell its person whether it was delivered.
        raise HTTPException(status_code=429, detail={"refused": refusals[0]})
    return {"event_type": ev.event_type, "source_event_id": ev.source_event_id,
            "admitted_by": actor, "reactions_created": n, "duplicate": n == 0,
            **({"refused": refusals} if refusals else {})}


@app.post("/events/signal", status_code=202)
def admit_signal(event: dict = Body(...), x_internal_secret: str = Header(default=""),
                 x_vexa_service: str = Header(default="")):
    """A MEETING SIGNAL, from the signals service only (transcript signals, 2026-10-15).

    The body is the event.v1 `meeting.signal` the service built (`subject` = the meeting's owner,
    `data` = the buffer, its score and why it woke). It is admitted as one fact, keyed by the
    buffer's segments so a redelivery wakes nobody twice; the company flow `meeting_signal` decides
    what it does. The caller is the internal tier naming itself `signals`; anyone else — a person,
    a worker, the operator's key — is refused, because a forged signal would put words in the
    agent's meeting chat."""
    from flows_steps import signals as _signals
    if (not INTERNAL_SECRET or not _same_key(x_internal_secret, INTERNAL_SECRET)
            or (x_vexa_service or "").strip().lower() != _signals.SIGNALS_SERVICE):
        raise HTTPException(status_code=403, detail={
            "refused": "signals_only", "note": "meeting.signal is posted by the signals service"})
    try:
        cleaned = _signals.clean(event)
    except _signals.SignalRefused as exc:
        raise HTTPException(status_code=400, detail={"refused": "invalid_signal", "note": str(exc)})
    name = cleaned["name"]
    vocab.refresh_from_db(db)
    if not vocab.match(name):
        # the company retired its flow: a wake does nothing, said rather than queued into silence
        return {"event_type": name, "reactions_created": 0, "duplicate": False,
                "refused": ["no_flow_reacts"]}
    sid = _signals.source_id(cleaned)
    where = ({"session": cleaned["data"]["session"]} if "session" in cleaned["data"]
             else {"meeting_row": cleaned["data"]["meeting_row"]})
    refs = {"uid": cleaned["subject"], **where, "signal": cleaned["data"], "admitted_by": "service:signals"}
    refusals: list = []
    n = admit(db, vocab, clock, source_event_id=sid, event_type=name,
              subject_refs=refs, refusals=refusals, **_admission_limits())
    flows_obs.log_event("event_admitted", fields={"event_type": name,
                                                  "reactions_created": n, "duplicate": n == 0})
    return {"event_type": name, "source_event_id": sid, "reactions_created": n,
            "duplicate": n == 0, **({"refused": refusals} if refusals else {})}


def _admission_limits() -> dict:
    """The loop guard and the per-person caps, from `config.v1` — the same numbers the worker's
    own `emit` admits under (ADR-0046 decisions 8, 9)."""
    return {"loop_limit": flows_config.get_int("VEXA_FLOWS_LOOP_LIMIT"),
            "max_hops": flows_config.get_int("VEXA_FLOWS_MAX_HOPS"),
            "runs_per_day": _policies_mod().company_quota("routine_runs_per_day", "VEXA_FLOWS_USER_RUNS_PER_DAY"),
            "messages_per_day": flows_config.get_int("VEXA_FLOWS_USER_MESSAGES_PER_DAY")}


def _check_message_policy(refs: dict) -> None:
    """WHO MAY MESSAGE WHOM is `_global/POLICIES.md`, read by the one policy reader (ADR-0046
    decision 13). A refusal names the key that refused it."""
    from flows_steps import policies
    try:
        rules = policies.read(str(refs.get("sender_uid") or ""))
    except Exception:  # noqa: BLE001 — an unreadable policy is the defaults, which are the safe side
        rules = policies.resolve(None)
    key = policies.message_refusal(rules, refs)
    if key:
        raise HTTPException(status_code=403, detail={
            "refused": key, "policy": f"_global/POLICIES.md#{key}"})


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

    NO ENVELOPE HERE (event.v2, D8): a seed row is a meeting, not a producer's fact, and its refs are
    built from fixed fields — so it can carry neither a sealed audience nor a forged one, and every
    row admits by subject only.
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
    return {**log, "meetings": out}


#: The lifecycle verbs, and the status each one writes. `pause` and `resume` are the words a
#: routine's thread uses; they are `retire` and `activate` — a retired version is kept and may be
#: activated again, so a pause loses nothing.
FLOW_ACTIONS = {"activate": "active", "resume": "active", "retire": "retired", "pause": "retired"}

#: What an agent turn is told when it submits a flow (D24): filed, not live.
APPROVAL_NOTE = ("Filed as a draft. A routine an agent writes runs only after your person turns it "
                 "on themselves, from their Routines board. Tell them it is waiting there; do not "
                 "try to activate it.")
#: …and what it gets when it tries to arm one anyway.
APPROVAL_REFUSAL = {
    "refused": "person_approval_required",
    "instruction": "Activating or resuming a flow is your person's act, from their own session. "
                   "Tell them the routine is waiting on their Routines board; do not retry it "
                   "another way.",
}


#: …and what a person's API key gets when it tries to arm one of their routines (D13): arming is
#: done from a signed-in session, and a key — whatever its scopes — is not one.
SESSION_REFUSAL = {
    "refused": "session_required",
    "instruction": "Turning a routine on is done by its person from their signed-in session (the "
                   "Routines board). An API key cannot do it, whatever its scopes. File it with "
                   "activate=false and turn it on from the board.",
}


def _require_session_to_arm(caller: Caller, target: str) -> None:
    """A PERSON'S OWN ROUTINE IS ARMED FROM A SESSION (D13). Applies to a person's own credential on
    a person's flow; the operator key and the instance's flows keep their own rules."""
    if caller.kind == "subject" and not caller.is_delegated and target != "instance" \
            and not caller.is_session:
        raise HTTPException(status_code=403, detail=SESSION_REFUSAL)


@app.post("/flows/{name}/{version}/{action}")
def set_flow_status(name: str, version: int, action: str, scope: str = "",
                    caller: Caller = Depends(subject_or_operator)):
    """Activate, pause, resume or retire ONE version of a flow, addressed by name and version.

    `action` is `activate`, `retire`, `pause` (the same as retire) or `resume` (the same as
    activate). Retiring stops that version being chosen — a schedule stops firing — without deleting
    it or disturbing runs already going; it can be activated again, so this is the safe way to take
    a flow out of service.

    With your own credential this acts on YOUR flow (`scope` defaults to `user:<you>`); another
    person's flow is refused, and the instance's needs the operator key or the instance
    administrator. Use `GET /flows` to see which versions exist and which one is active."""
    if action == REVOKE_GRANTS:
        return _revoke_grants(_lifecycle_scope(caller, scope), name, version)
    if action not in FLOW_ACTIONS:
        raise HTTPException(status_code=404,
                            detail="activate | retire | pause | resume | revoke_grants")
    target = _lifecycle_scope(caller, scope)
    st = FLOW_ACTIONS[action]
    if st == "active" and caller.is_delegated:
        # ARMING IS THE PERSON'S ACT (D24, H8). An agent turn may pause or retire a flow — a
        # narrowing — but never activate or resume one, its own drafts included.
        raise HTTPException(status_code=403, detail=APPROVAL_REFUSAL)
    if st == "active":
        _require_session_to_arm(caller, target)
    if st == "active" and target != "instance":
        rows = db.execute("SELECT steps, cron FROM flow_version WHERE scope=:sc AND name=:n "
                          "AND version=:v", {"sc": target, "n": name, "v": version})
        if rows:
            sub = FlowSubmission(name=name, steps=json.loads(rows[0][0]) or ["agent_run"],
                                 on_event="x.y", params={"instruction": "-"})
            _check_user_flow(target, sub, rows[0][1])
    if st == "active":
        rows = _supersede(target, name, version)
        if rows:
            _clear_pause(target, name, version)
    else:
        rows = db.execute("UPDATE flow_version SET status=:s WHERE scope=:sc AND name=:n "
                          "AND version=:v RETURNING name", {"s": st, "sc": target, "n": name,
                                                            "v": version})
    if not rows:
        raise HTTPException(status_code=404, detail="flow version not found")
    return {"name": name, "scope": target, "version": version, "status": st}


def _clear_pause(scope: str, name: str, version: int) -> None:
    """A resumed routine is no longer paused for the reason it paused itself (Vexa-ai/vexa#1783)."""
    rows = db.execute("SELECT params FROM flow_version WHERE scope=:sc AND name=:n AND version=:v",
                      {"sc": scope, "n": name, "v": version})
    params = json.loads(rows[0][0] or "{}") if rows else {}
    if isinstance(params, dict) and params.pop("paused", None) is not None:
        db.execute("UPDATE flow_version SET params=:p WHERE scope=:sc AND name=:n AND version=:v",
                   {"p": json.dumps(params), "sc": scope, "n": name, "v": version})


#: Withdraw what a routine may do unattended (flows phase 0). A narrowing written in place on the
#: version: the routine keeps its schedule and instruction, and its next run carries no grant.
REVOKE_GRANTS = "revoke_grants"


def _revoke_grants(scope: str, name: str, version: int) -> dict:
    rows = db.execute("SELECT params FROM flow_version WHERE scope=:sc AND name=:n AND version=:v",
                      {"sc": scope, "n": name, "v": version})
    if not rows:
        raise HTTPException(status_code=404, detail="flow version not found")
    params = json.loads(rows[0][0] or "{}")
    dropped = params.pop("grants", None) or []
    db.execute("UPDATE flow_version SET params=:p WHERE scope=:sc AND name=:n AND version=:v",
               {"p": json.dumps(params), "sc": scope, "n": name, "v": version})
    flows_obs.log_event("routine_grant_revoked",
                        fields={"flow": f"{scope}/{name}", "version": version, "grants": dropped})
    return {"name": name, "scope": scope, "version": version, "grants": [], "revoked": dropped,
            "may": ""}


def _supersede(scope: str, name: str, version: int) -> list:
    """ONE ACTIVE VERSION PER FLOW. ``version`` becomes the active one and every other active version
    of the same (scope, name) is retired — in ONE statement, so there is no moment with two running
    and none with zero. A schedule fires once per active version, so a resubmitted routine used to
    run once per copy. Returns the row for ``version`` (empty when it does not exist)."""
    rows = db.execute(
        "UPDATE flow_version SET status = CASE WHEN version = :v THEN 'active' ELSE 'retired' END "
        "WHERE scope = :sc AND name = :n AND (version = :v OR status = 'active') "
        "RETURNING name, version", {"sc": scope, "n": name, "v": version})
    return [r for r in rows if int(r[1]) == int(version)]


_CHAT = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class Claim(BaseModel):
    #: the chat that takes the routine on — the caller's current chat (its turn states the id)
    thread: str = Field(min_length=1, max_length=128, pattern=_CHAT)


def _routine_facts(scope: str, name: str) -> dict:
    """WHAT IS TRUE NOW, read back after a change: the active version, the chat it runs in, its
    trigger and when it next runs (UTC) — what a reply may state, never what it hoped."""
    from flows.schedule import next_slot, parse
    rows = db.execute("SELECT version, cron, on_event, params FROM flow_version WHERE scope=:sc "
                      "AND name=:n AND status='active' ORDER BY version DESC",
                      {"sc": scope, "n": name})
    if not rows:
        return {"name": name, "scope": scope, "active": None}
    version, cron, on_event, params = rows[0]
    p = json.loads(params or "{}")
    nxt = None
    if cron:
        try:
            nxt = next_slot(parse(cron), clock.now())
        except Exception:  # noqa: BLE001 — a cron that cannot be read has no next run to state
            nxt = None
    return {"name": name, "scope": scope, "active": version, "active_versions": len(rows),
            "thread": p.get("thread") or None,
            "trigger": {"kind": "schedule", "cron": cron} if cron else {"kind": "event", "event": on_event},
            "next_run_at": nxt}


def _announce(scope: str, name: str, thread: str, text: str, version: int = 0) -> bool:
    """One line in the chat that owns the routine, through agent-api (which also marks the chat as a
    routine's at once). False when the agent domain is not here; the claim stands either way."""
    try:
        from flows_steps import common, routine as _routine
        if not common.domain_present("agent"):
            return False
        code, _ = _routine.post_notice(scope.split(":", 1)[-1], thread, text, name=name, scope=scope,
                                       version=version)
        return 200 <= int(code or 0) < 300
    except Exception:  # noqa: BLE001
        return False


@app.post("/flows/{name}/claim")
def claim_routine(name: str, body: Claim, caller: Caller = Depends(subject_or_operator)):
    """CLAIM A ROUTINE FOR THIS CHAT — one operation, no new version (founder, 2026-10-13: "claiming
    must be ONE clean operation"). The active version's chat binding (`params.thread`) moves to
    `thread`; its schedule, instruction and version stay as they are, and the runs already made stay
    in the chat they were made in. A chat may own several routines. The answer is read back after
    the change: the active version, the chat it now runs in and its next run — state only these.
    Your own routines only; a worker with nobody in the loop is refused."""
    scope = _lifecycle_scope(caller, "")
    if scope == "instance":
        raise HTTPException(status_code=403, detail={"refused": "not_a_routine",
                                                     "note": "only a person's own flow runs in a chat"})
    rows = db.execute("SELECT version, params FROM flow_version WHERE scope=:sc AND name=:n "
                      "AND status='active' ORDER BY version DESC LIMIT 1", {"sc": scope, "n": name})
    if not rows:
        raise HTTPException(status_code=404, detail={"refused": "no_active_routine", "name": name})
    version, params = rows[0]
    p = json.loads(params or "{}")
    before = p.get("thread") or None
    p["thread"] = body.thread
    db.execute("UPDATE flow_version SET params=:p WHERE scope=:sc AND name=:n AND version=:v",
               {"p": json.dumps(p), "sc": scope, "n": name, "v": version})
    facts = _routine_facts(scope, name)
    when = (time.strftime("%H:%M UTC", time.gmtime(facts["next_run_at"]))
            if facts.get("next_run_at") else "on its event")
    facts["announced"] = _announce(scope, name, body.thread,
                                   f"This chat now runs the routine {name} (version {version}); "
                                   f"next run {when}." + (f" Earlier runs stay in chat {before}."
                                                          if before and before != body.thread else ""),
                                   version=version)
    facts["moved_from"] = before
    return facts


class TriggerAdd(BaseModel):
    thread: str = Field(min_length=1, max_length=128, pattern=_CHAT)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    #: `{"kind": "schedule", "cron": "*/15 * * * *"}` or `{"kind": "event", "event": "<type>"}`
    trigger: dict
    instruction: str = Field(min_length=1, max_length=8000)


@app.post("/routines/triggers", status_code=201)
def add_routine_trigger(body: TriggerAdd, x_actor: str = Header(default="api"),
                        caller: Caller = Depends(subject_or_operator)):
    """GIVE A CHAT ANOTHER TIMER — one more routine it owns (a schedule; an event-triggered agent is a
    different thing, not made here). A chat is a standing concern (production monitoring, a
    pipeline): each wake is a turn in it with its history as memory. Same checks as `POST /flows`
    (your scope, the step allow-list, quotas); the answer is read back after the change."""
    if (body.trigger or {}).get("kind") != "schedule":
        raise HTTPException(status_code=422, detail={
            "refused": "routine_needs_schedule",
            "note": "a routine runs on a schedule; an event-triggered agent is not a routine"})
    made = submit_flow(FlowSubmission(name=body.name, trigger=body.trigger, steps=["agent_run"],
                                      params={"instruction": body.instruction, "thread": body.thread}),
                       x_actor=x_actor, caller=caller)
    facts = _routine_facts(made["scope"], made["name"])
    if made.get("awaiting_approval"):
        # proposed by an agent turn (D24): nothing wakes until the person turns it on
        _announce(made["scope"], made["name"], body.thread,
                  f"Proposed a timer {body.name} for this chat. It waits on your Routines board "
                  "until you turn it on.")
        return {**facts, "awaiting_approval": True, "note": APPROVAL_NOTE}
    _announce(made["scope"], made["name"], body.thread,
              f"This chat now also wakes on {body.name} "
              + (f"({made['trigger'].get('cron')} UTC)." if made["trigger"].get("cron")
                 else f"(event {made['trigger'].get('event')}).") )
    return facts


class StageMove(BaseModel):
    thread: str = Field(min_length=1, max_length=128, pattern=_CHAT)
    stage: str = Field(pattern=r"^(opened|active|waiting|resolved|archived)$")
    note: str = Field(default="", max_length=400)


@app.post("/concerns/stage")
def move_concern(body: StageMove, caller: Caller = Depends(subject_or_operator)):
    """MOVE A CONCERN CHAT along its lifecycle: opened → active → waiting (on someone) → resolved →
    archived. Say `waiting` when the next move is somebody else's, `resolved` when the concern's
    exit condition is met (the recap was sent, the incident cleared) or the person closes it. The
    chat shows its stage and the home counts what is waiting. Your own chats only."""
    scope = _lifecycle_scope(caller, "")
    if scope == "instance":
        raise HTTPException(status_code=403, detail={"refused": "not_a_person"})
    from flows_steps import common, routine as _routine
    if not common.domain_present("agent"):
        raise HTTPException(status_code=503, detail={"refused": "agent:not_present"})
    code, out = _routine.post_stage(scope.split(":", 1)[-1], body.thread, body.stage, body.note)
    if not 200 <= int(code or 0) < 300:
        raise HTTPException(status_code=502, detail={"refused": "stage_not_saved", "status": code})
    return {"thread": body.thread, "stage": body.stage,
            "saved": bool((out or {}).get("stage") == body.stage) if isinstance(out, dict) else False}


@app.post("/event-agents/pause")
def pause_event_agents(everyone: bool = False, caller: Caller = Depends(subject_or_operator)):
    """THE KILL SWITCH: pause every running event agent — yours, or (the operator key or the instance
    administrator, `everyone=true`) every person's. Routines are untouched. Each paused version can be
    resumed one by one (`flow_lifecycle`)."""
    if everyone and not caller.is_admin:
        raise HTTPException(status_code=403, detail={"refused": "admin_only"})
    where = "scope LIKE 'user:%'" if everyone else "scope = :sc"
    params = {} if everyone else {"sc": _lifecycle_scope(caller, "")}
    rows = db.execute(f"UPDATE flow_version SET status='retired' WHERE {where} AND status='active' "
                      "AND (cron IS NULL OR cron = '') RETURNING scope, name, version", params)
    return {"paused": [{"scope": a, "name": b, "version": c} for a, b, c in rows]}


class Subscribe(BaseModel):
    thread: str = Field(min_length=1, max_length=128, pattern=_CHAT)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    event: str = Field(min_length=1, max_length=120)
    #: `{ref: text}` — every pair must appear in that ref of the event (case-insensitive)
    filter: dict = Field(default_factory=dict)
    wake: str = Field(default="never", pattern=r"^(never|per_event|batch:\d{1,4}|match:.{1,80})$")
    instruction: str = Field(default="", max_length=4000)


@app.post("/sinks", status_code=201)
def chat_subscribe(body: Subscribe, x_actor: str = Header(default="api"),
                   caller: Caller = Depends(subject_or_operator)):
    """SUBSCRIBE A CHAT TO AN EVENT STREAM — a sink. Every matching event lands in the chat as one
    compact card (no agent run, never counted as one; capped per chat per hour). The chat's agent
    wakes only as `wake` says: never (a pure log), per_event, batch:<minutes>, match:<text>. This
    is how a concern chat keeps its own timeline. Read back after the change."""
    made = submit_flow(FlowSubmission(name=body.name, trigger={"kind": "event", "event": body.event},
                                      steps=["post_event_card"],
                                      params={"thread": body.thread, "filter": body.filter,
                                              "wake": body.wake, **({"instruction": body.instruction}
                                                                    if body.instruction else {})}),
                       x_actor=x_actor, caller=caller)
    return {**_routine_facts(made["scope"], made["name"]), "kind": "sink", "filter": body.filter,
            "wake": body.wake}


# ── sensors: the person's own (ADR-0050 decision 6) ────────────────────────────────────────────

class SensorBody(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    #: a building block: `calendar` (Google Calendar, through the broker) — `GET /sensors` lists them
    kind: str = Field(min_length=1, max_length=40)
    #: a predicate over each event (`payload.video_url exists and payload.self_response != 'declined'`)
    filter: str = Field(default="", max_length=512)
    connection_id: str = Field(default="", max_length=64)
    interval_s: Optional[float] = Field(default=None, ge=1, le=86400)
    scope: Optional[str] = None


class SensorChange(BaseModel):
    filter: Optional[str] = Field(default=None, max_length=512)
    connection_id: Optional[str] = Field(default=None, max_length=64)
    interval_s: Optional[float] = Field(default=None, ge=1, le=86400)


class SensorConnect(BaseModel):
    thread: str = Field(min_length=1, max_length=128, pattern=_CHAT)
    wake: str = Field(default="per_event", pattern=r"^(never|per_event|batch:\d{1,4}|match:.{1,80})$")
    instruction: str = Field(default="", max_length=4000)


def _sensor_scope(caller: Caller, asked: Optional[str]) -> str:
    return _lifecycle_scope(caller, asked or "")


def _sensor_kinds() -> dict:
    from flows_sensors.calendar import KINDS
    return KINDS


def _sensor_call(fn, *a, **kw):
    from flows_sensors.store import SensorError
    try:
        return fn(*a, **kw)
    except SensorError as e:
        _refuse(e.code, e.reason, **e.facts)


def _sensor_view(scope: str, name: str) -> dict:
    from flows_sensors import pool
    rows = pool.health(db, scope, name)
    return rows[0] if rows else {"name": name, "scope": scope, "status": None}


@app.get("/sensors")
def sensor_list(scope: str = "", caller: Caller = Depends(subject_or_operator)):
    """Your sensors — each with its kind, filter, status (draft · active · degraded · stopped),
    whether this version passed its test, its last events and when it checks next — and the kinds a
    sensor can be made of. A sensor turns what changes in the world (your calendar) into events with
    no model call and never acts; it runs only after a test of its current version."""
    from flows_sensors import pool
    sc = _sensor_scope(caller, scope)
    return {"scope": sc, "sensors": pool.health(db, sc),
            "kinds": [{"kind": k.kind, "event_types": list(k.event_types), "connection": k.connection,
                       "min_interval_s": k.min_interval_s} for k in _sensor_kinds().values()]}


@app.post("/sensors", status_code=201)
def sensor_create(body: SensorBody, caller: Caller = Depends(subject_or_operator)):
    """Make a sensor of your own, as a DRAFT: a `kind` (e.g. `calendar`), an optional `filter` over
    each event, optionally which `connection_id` and how often to check. Then `sensor_test` it,
    enable it (`sensor_action enable`) and connect it to a chat (`sensor_connect`). Read back."""
    from flows_sensors import store
    sc = _sensor_scope(caller, body.scope)
    _sensor_call(store.create, db, _sensor_kinds(), sc, name=body.name, kind=body.kind,
                 filter=body.filter, connection_id=body.connection_id, interval_s=body.interval_s,
                 by=caller.email or f"user:{caller.uid}", now=clock.now())
    return _sensor_view(sc, body.name)


@app.post("/sensors/{name}/test")
def sensor_test(name: str, scope: str = "", caller: Caller = Depends(subject_or_operator)):
    """Dry-run a sensor over your recent real data, with NO side effects: the events it WOULD emit
    and the ones it dropped, with the reason for each. A passing test is what lets this version be
    enabled. Show the person both lists before enabling."""
    from flows_sensors import store
    sc = _sensor_scope(caller, scope)
    return _sensor_call(store.test, db, _sensor_kinds(), sc, name, now=clock.now())


@app.post("/sensors/{name}/connect")
def sensor_connect(name: str, body: SensorConnect, scope: str = "",
                   caller: Caller = Depends(subject_or_operator)):
    """Connect a sensor's events to a chat (this one: `thread` is the id your turn states): each event
    lands there as one card, and the chat's agent wakes as `wake` says (per_event · batch:<minutes> ·
    match:<text> · never) with `instruction`. The chat's agent then proposes; it does not act alone."""
    from flows_sensors import store
    sc = _sensor_scope(caller, scope)
    s = store.get(db, sc, name)
    if s is None:
        _refuse(404, "not_found", name=name)
    kind = _sensor_kinds().get(s.kind)
    event = (kind.event_types[0].rsplit(".", 1)[0] + ".*") if kind and kind.event_types else "*"
    made = chat_subscribe(Subscribe(thread=body.thread, name=f"sensor-{name}"[:80], event=event,
                                    filter={"_sensor": f"{sc}/{name}"}, wake=body.wake,
                                    instruction=body.instruction),
                          caller=caller)
    return {"sensor": _sensor_view(sc, name), "connected": made}


@app.post("/sensors/{name}/{action}")
def sensor_action(name: str, action: str, body: SensorChange = SensorChange(), scope: str = "",
                  caller: Caller = Depends(subject_or_operator)):
    """`enable` (only a version that passed `sensor_test`), `pause`, `update` (a new version from the
    body's filter / connection_id / interval_s — back to draft until tested again) or `delete`."""
    from flows_sensors import store
    sc = _sensor_scope(caller, scope)
    if action == "enable":
        _sensor_call(store.enable, db, sc, name, now=clock.now())
    elif action == "pause":
        _sensor_call(store.pause, db, sc, name)
    elif action == "update":
        b = body or SensorChange()
        _sensor_call(store.update, db, _sensor_kinds(), sc, name, filter=b.filter,
                     interval_s=b.interval_s, connection_id=b.connection_id)
    elif action == "delete":
        if not store.delete(db, sc, name):
            _refuse(404, "not_found", name=name)
        return {"name": name, "scope": sc, "deleted": True}
    else:
        raise HTTPException(status_code=404, detail="enable | pause | update | delete")
    return _sensor_view(sc, name)


@app.post("/sinks/{name}/unsubscribe")
def chat_unsubscribe(name: str, caller: Caller = Depends(subject_or_operator)):
    """STOP A CHAT'S SUBSCRIPTION to an event stream, by the sink's name: its running version is
    retired, so no new event cards arrive and the chat's agent is no longer woken by that stream.
    The cards already posted stay in the chat as its timeline. Your own sinks only; a worker with
    nobody in the loop is refused. Use `flows_list` to see a chat's sinks (`kind: sink`)."""
    scope = _lifecycle_scope(caller, "")
    rows = db.execute("UPDATE flow_version SET status='retired' WHERE scope=:sc AND name=:n "
                      "AND status='active' RETURNING version", {"sc": scope, "n": name})
    if not rows:
        raise HTTPException(status_code=404, detail={"refused": "no_active_sink", "name": name})
    return {"name": name, "scope": scope, "unsubscribed": [r[0] for r in rows]}


def _lifecycle_scope(caller: Caller, asked: str) -> str:
    asked = (asked or "").strip()
    if caller.is_admin:
        return asked or "instance"
    if not caller.uid:
        raise HTTPException(status_code=403, detail="this credential cannot change flows")
    own = f"user:{caller.uid}"
    if not asked or asked == own or _names_own_scope(caller, asked):
        return own
    if asked == "instance" and caller.instance_admin:
        return "instance"
    raise HTTPException(status_code=403, detail={"refused": "not_your_flow", "scope": asked,
                                                 "you_are": own})


@app.get("/flows/runs")
def flow_runs(subject: str = "", limit: int = 50,
              caller: Caller = Depends(subject_or_operator)):
    """THE RUNS OF YOUR OWN FLOWS, newest first — each with the receipts its steps left.

    Only flows you own are here (`user:<you>/…`): a routine's fires, an event flow of yours, and
    what each step did (`receipts`: step, state, result). Nobody else's runs are ever in this
    answer, and the instance's flows are not either — their runs are about many people. The
    operator key must name the person (`subject`)."""
    uid = scoped_subject(caller, subject)
    limit = max(1, min(int(limit or 50), 200))
    rows = db.execute(
        "SELECT reaction_id, flow, flow_version, event_type, step, status, reason, created_at, "
        "updated_at FROM reaction WHERE flow LIKE :p ORDER BY created_at DESC LIMIT :lim",
        {"p": f"user:{uid}/%", "lim": limit})
    runs = []
    for rid, flow, ver, et, step, status, reason, created, updated in rows:
        rec = db.execute("SELECT step, state, result FROM effect_receipt WHERE reaction_id = :r "
                         "ORDER BY attempted_at", {"r": rid})
        runs.append({"id": rid, "flow": flow.split("/", 1)[-1], "version": ver, "trigger": et,
                     "step": step, "status": status, "reason": reason, "started_at": created,
                     "updated_at": updated,
                     "receipts": [{"step": a, "state": b, "result": _json_or_none(c)}
                                  for a, b, c in rec]})
    return {"subject": uid, "runs": runs}


def _json_or_none(text):
    try:
        return json.loads(text) if text else None
    except (TypeError, ValueError):
        return None


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

    Read-only: it admits nothing, signals nothing and writes nothing.

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
        out = {**out, "timezone": tz or "UTC", "timezone_set": bool(tz),
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

    NOT AS A REPLY TO THE PERSON. When the person you are talking to says something did not work
    ("no it does not fix", "the links don't open"), that is a reply to what you last did: re-check
    it and answer them first. Never answer them with this call, and never ask them to explain a
    report they did not make. File it when they ask you to, or once your re-check shows the failure
    is ours — in addition to answering them, not instead.

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
    # ONE ROW PER EDGE PER HOUR (dedup). A reporter stuck in a loop files the same report on every
    # pass; the first is admitted below as before, and a repeat with the same (uid, tool,
    # normalised reason, UTC hour) only bumps a counter on it — `friction_dedup` is the one writer
    # of that counter, and this route is its one caller.
    tool_sent = _field("tool", tool)
    tool_sent = tool_sent.strip()[:200] if isinstance(tool_sent, str) else ""
    now = clock.now()
    dkey = friction_dedup.dedup_key(uid=subj, tool=tool_sent, what_happened=happened,
                                    what_i_tried=tried, now=now)
    fid, seen = friction_dedup.record_occurrence(db, key=dkey, friction_id=_friction_id(),
                                                 uid=subj, now=now)
    if seen > 1:
        return {"id": fid, "recorded": False, "duplicate_of": fid, "occurrences": seen,
                "kind": knd, "severity": sev, "session": sess,
                "instruction": (f"already reported {seen} times; do not report it again this run")}
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
    if created:
        friction_dedup.mark_admitted(db, key=dkey, friction_id=fid)
    if not created:
        friction_dedup.forget(db, key=dkey)   # nothing to fold repeats into — let the next try admit
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


@app.get("/friction/all", dependencies=[Depends(auth)])
def friction_all(since: str = "", kind: str = "", person: str = "", status: str = "",
                 limit: int = 50):
    """EVERY friction report on this deployment, newest first — the sink, read whole.

    Founder ruling 2026-10-11: the deployment's admin account reads friction from the sink. This is
    that read, and it is the one instance-wide read on this surface: OPERATOR-GATED (the operator
    key, never a person's credential), and the MCP edge spends that key for the admin account only
    (`friction_list`, `auth: admin`). Everybody else reads their own reports with `friction_so_far`.

    Filters, all optional: `since` (epoch or ISO-8601), `kind` (the reporter's own word, case-
    insensitive), `person` (a uid or an address — whose reports), `status` (`open` | `fixed`).
    Every free-text field comes back SCRUBBED of credential-shaped text (`flows_timeline.redaction`):
    the sink stores a report as it was sent, and a token somebody pasted into one must not reach the
    person reading everybody's."""
    st = (status or "").strip().lower()
    if st and st not in ("open", "fixed"):
        raise HTTPException(status_code=400, detail="status is open, fixed, or empty for both")
    ts = (_friction_since_epoch(since) or 0.0) if since else 0.0
    rows = friction_sink(db, since=ts, kind=kind, subject=(person or "").strip(), status=st,
                         limit=max(1, min(int(limit or 50), 500)))
    if rows is None:
        raise HTTPException(status_code=404, detail=f"nobody answers to {person!r}")
    return {"count": len(rows), "filters": {"since": since, "kind": kind, "person": person,
                                            "status": st},
            "reports": rows}


#: The events a mail INTO the mailbox can start — the inbound half of the mail settings.
_INBOUND_MAIL_EVENTS = ("invite.received", "mail.reply")
#: The outbound rules, in the order the settings name them: kind of mail → the rule that gates it.
_OUTBOUND_MAIL_RULES = (("minutes", "minutes_mail"), ("prep", "prep_and_invite_mail"),
                        ("attendee_follow_up", "report_to_participants"))


@app.get("/mail/settings", dependencies=[Depends(auth)])
def mail_settings():
    """THIS DEPLOYMENT'S MAIL, as it stands right now — what the mailbox accepts, what it sends and
    which flows react to inbound mail. A READ; it changes nothing.

    The runtime answers live in `_global/POLICIES.md` (`mail_domains`, `bot_joins_on_invite`,
    `minutes_mail`, `prep_and_invite_mail`, `report_to_participants`, `external_participants`,
    `attendee_domains`) and the mail wording in `_global/mail/<name>.md` — both written only by the
    deployment's admin account (`workspace_write` on `_global`), never by this service. The
    mailbox's address, credentials, relay and rate limits are deployment configuration: the address
    is shown, every credential is reported as configured or not and NEVER as a value.

    Operator-gated; the MCP edge spends the key for the admin account only (`mail_settings`)."""
    from flows_integrations import mail_policy
    from flows_steps import mailkinds, mailtext, policies
    from flows_steps.common import ws_file
    rules = policies.read_company()
    addr = (flows_config.get("VEXA_MAIL_ADDR") or flows_config.get("VEXA_GRAPH_MAILBOX")
            or "").strip().lower()
    page = tuple(rules.get("mail_domains") or ())
    allowed = sorted(mail_policy.allow_domains(addr, rules))
    source = ("POLICIES.md mail_domains" if page else
              "VEXA_FLOWS_MAIL_DOMAINS" if flows_config.get("VEXA_FLOWS_MAIL_DOMAINS") else
              "the mailbox's own domain")
    inbox = (flows_config.get("VEXA_MAIL_INBOX") or "imap").strip()
    configured = {
        "imap": bool(addr and flows_config.get("VEXA_MAIL_APP_PASSWORD")),
        "mailpit": bool(addr),
        "graph": bool(flows_config.get("VEXA_GRAPH_MAILBOX")
                      and flows_config.get("VEXA_GRAPH_CLIENT_SECRET")),
    }.get(inbox, False)
    templates = []
    for name in sorted(mailtext.DEFAULTS):
        try:
            override = bool((ws_file(policies.COMPANY_READER, f"mail/{name}.md", "_global") or "").strip())
        except Exception:  # noqa: BLE001 — an unreachable `_global` reads as "the baked wording"
            override = False
        templates.append({"name": name, "path": f"_global/mail/{name}.md",
                          "source": "_global" if override else "baked"})
    db_flows = _db_flows()
    inbound = [{"name": f.name, "version": f.version, "on": f.on.name, "source": "image",
                "status": "active"} for f in vocab.flows.values()
               if f.on.name in _INBOUND_MAIL_EVENTS]
    inbound += [{k: f[k] for k in ("name", "version", "on", "source", "status")}
                for f in db_flows if f["on"] in _INBOUND_MAIL_EVENTS]
    return {
        "mailbox": {"address": addr, "inbox": inbox, "credentials_configured": configured,
                    "quarantine_reply": flows_config.get_bool("VEXA_FLOWS_MAIL_QUARANTINE_REPLY"),
                    "rate_limits": {
                        "per_sender": flows_config.get_int("VEXA_FLOWS_MAIL_RATE_PER_SENDER"),
                        "global": flows_config.get_int("VEXA_FLOWS_MAIL_RATE_GLOBAL"),
                        "window_s": flows_config.get_int("VEXA_FLOWS_MAIL_RATE_WINDOW_S")}},
        "inbound": {"allow_list": allowed, "allow_list_source": source,
                    "bot_joins_on_invite": bool(rules.get("bot_joins_on_invite", True)),
                    "flows": inbound},
        "outbound": {kind: bool(rules.get(rule, True)) for kind, rule in _OUTBOUND_MAIL_RULES}
                    | {"external_participants": bool(rules.get("external_participants", True)),
                       "attendee_domains": list(rules.get("attendee_domains") or ())},
        "templates": templates,
        "kinds": {k: mailkinds.switch(rules, k) for k in mailkinds.KINDS},
        "sender": {"from": flows_config.get("VEXA_MAIL_SMTP_FROM") or (f"Vexa <{addr}>" if addr else ""),
                   "reply_to": flows_config.get("VEXA_MAIL_REPLY_TO"),
                   "relay": flows_config.get("VEXA_MAIL_RELAY") or "unset (VEXA_MAIL_SMTP_HOST as configured)"},
        "inbound_filter": flows_config.get("VEXA_MAIL_INBOUND_FILTER"),
        "policy_problems": list(rules.get("problems") or ()),
        "how_to_change": (
            "Runtime answers: edit the front matter of _global/POLICIES.md (the admin account's "
            "workspace_write) — mail_domains, bot_joins_on_invite, minutes_mail, "
            "prep_and_invite_mail, report_to_participants, external_participants, "
            "attendee_domains. Wording: _global/mail/<name>.md. A flow version that reacts to "
            "inbound mail: flow_lifecycle. The mailbox address, its credentials, the relay and the "
            "rate limits are the operator's deployment configuration and are not changed here."),
    }


# ── THE GLOBAL ACCOUNT DESIGNS THE MAIL (`mail_admin`, founder 2026-10-14) ───────────────────────

class _MailTemplate(BaseModel):
    content: str


class _MailPreview(BaseModel):
    sample: Optional[dict] = None
    draft: Optional[str] = None


class _MailTest(BaseModel):
    sample: Optional[dict] = None


class _MailSwitch(BaseModel):
    state: str


class _SignIn(BaseModel):
    to: str
    link: str
    minutes: int = 15


def _mail_admin(caller: Caller) -> Caller:
    """The deployment's ADMIN ACCOUNT, speaking for itself (identity's `admin_account`) — the only
    caller that designs the company's mail. The operator key may read, never act as somebody."""
    if caller.is_admin or caller.instance_admin:
        return caller
    raise HTTPException(status_code=403, detail=(
        "only the deployment's admin account designs the company's mail (Company mode); your own "
        "mail settings are in Settings, and mail_address tells you Vexa's invite address"))


def _mail_call(fn):
    from flows_integrations.mail_admin import MailAdminError
    try:
        return fn()
    except MailAdminError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'\""))


def _reader(caller: Caller) -> str:
    from flows_steps import policies as _p
    return caller.uid or _p.COMPANY_READER


@app.get("/mail/kinds")
def mail_kinds(caller: Caller = Depends(subject_or_operator)):
    """Every kind of mail this deployment can send, for the admin account: its switch (on, off,
    admins), its template's current version and whether that version was test-sent, the variables a
    template may use, and what sends it today."""
    from flows_integrations import mail_admin
    _mail_admin(caller)
    return {"kinds": _mail_call(lambda: mail_admin.kinds(db, _reader(caller))),
            "how": "mail_template_edit → mail_preview → mail_test_send → mail_kind_set"}


@app.put("/mail/templates/{kind}")
def mail_template_put(kind: str, body: _MailTemplate, caller: Caller = Depends(subject_or_operator)):
    """Rewrite one mail kind's template (`subject: …`, `---`, body; {{variables}}; no URL) into
    `_global/mail/<template>.md` as the admin account. The edit is a new template version."""
    from flows_integrations import mail_admin
    from flows_steps.common import ws_write
    _mail_admin(caller)
    if not caller.uid:
        raise HTTPException(status_code=403, detail="only the admin account writes _global/mail")
    return _mail_call(lambda: mail_admin.edit(kind, body.content, uid=caller.uid,
                                              write_file=ws_write))


@app.post("/mail/preview/{kind}")
def mail_preview(kind: str, body: _MailPreview, caller: Caller = Depends(subject_or_operator)):
    """Render one mail kind — subject and body — with its sample values (`sample` overrides them),
    from the live template or an unsaved `draft`. Sends nothing."""
    from flows_steps import outbound as _out
    _mail_admin(caller)
    return _mail_call(lambda: _out.preview(kind, uid=_reader(caller), sample=body.sample,
                                           draft=body.draft))


@app.post("/mail/test-send/{kind}")
def mail_test_send(kind: str, body: _MailTest, caller: Caller = Depends(subject_or_operator)):
    """Send the current template of one mail kind to the admin account's OWN address (the only
    recipient this route has) and record that version as test-sent — what lets it be switched on."""
    from flows_integrations import mail_admin
    _mail_admin(caller)
    return _mail_call(lambda: mail_admin.test_send(db, kind, uid=_reader(caller),
                                                   email=caller.email, sample=body.sample))


@app.put("/mail/kinds/{kind}")
def mail_kind_put(kind: str, body: _MailSwitch, caller: Caller = Depends(subject_or_operator)):
    """Switch one mail kind on, off or admins-only in `_global/POLICIES.md`. Refused for on/admins
    until its current template version was test-sent; refused for switching the sign-in link off."""
    from flows_integrations import mail_admin
    from flows_steps.common import ws_file, ws_write
    _mail_admin(caller)
    if not caller.uid:
        raise HTTPException(status_code=403, detail="only the admin account writes _global/POLICIES.md")
    return _mail_call(lambda: mail_admin.set_kind(db, kind, body.state, uid=caller.uid,
                                                  read_file=ws_file, write_file=ws_write))


@app.get("/mail/delivery")
def mail_delivery(caller: Caller = Depends(subject_or_operator)):
    """WHAT HAPPENS TO MAIL HERE: `delivered`, or `captured` (the capture double — nobody receives
    it). A producer that hands a mail over (agent-api's invite) asks this before it tells a person
    the mail went out (live defect 2026-10-14)."""
    from flows_steps import emailx as _mx
    mode = _mx.delivery_mode()
    return {"delivery": mode,
            "means": ("mail reaches the recipient's mailbox" if mode == "delivered" else
                      "mail is captured by the deployment's mail double and reaches nobody — give "
                      "people their links yourself")}


@app.get("/mail/address")
def mail_address(caller: Caller = Depends(subject_or_operator)):
    """THE ADDRESS TO INVITE VEXA AT — every person may read it (it is not a setting, it is how
    the product is used), not only the admin account (friction 2026-10-14)."""
    addr = (flows_config.get("VEXA_MAIL_ADDR") or flows_config.get("VEXA_GRAPH_MAILBOX")
            or "").strip().lower()
    if not addr:
        return {"address": None, "how": "this deployment has no Vexa mailbox; send a bot from the "
                                         "chat with a meeting link instead"}
    return {"address": addr,
            "how": f"add {addr} as a guest to a calendar event with a meeting link — the bot "
                   f"joins at the start. Replies to mail from Vexa reach the same mailbox."}


@app.post("/mail/sign-in")
def mail_sign_in(body: _SignIn, x_internal_secret: str = Header(default="")):
    """THE SIGN-IN LINK, through the one outbound path — the terminal's door (internal tier). The
    kind is locked on and never capped here; a failure is the terminal's cue to send its own baked
    mail, so a sign-in never depends on this route."""
    from flows_steps import outbound as _out
    if not INTERNAL_SECRET or not _same_key(x_internal_secret, INTERNAL_SECRET):
        raise HTTPException(status_code=403, detail="internal tier only")
    if "@" not in body.to or not body.link.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="to and link are required")
    out = _out.send("sign-in", body.to.strip(), values={"minutes": str(int(body.minutes))},
                    link=body.link, db=db)
    if not out.sent:
        raise HTTPException(status_code=502, detail=f"not sent: {out.result}")
    return {"sent": True, "delivery": out.delivery, "template_version": out.template_version}


class _PlatformMail(BaseModel):
    kind: str
    to: str
    uid: str = ""
    values: Optional[dict] = None
    link: Optional[str] = None


#: The kinds a platform producer (agent-api, meeting-api) may send through `POST /mail/send`. Each
#: is a mail a PERSON's act produced (an invite redeemed, a chat shared, a call started); the switch,
#: cap, template and audit all still apply. Reports and digests are a flow's, not a producer's.
_PLATFORM_KINDS = frozenset({"workspace-joined", "workspace-invite", "chat-share", "call-invite",
                             "meeting-share", "bot-joined", "bot-failed"})


@app.post("/mail/send")
def mail_send(body: _PlatformMail, x_internal_secret: str = Header(default="")):
    """ONE MAIL OF ONE KIND, for a platform producer on the internal tier — the same outbound path
    every flow step uses: the company's switch, the cap, the template, the audit. A kind that is off
    answers 200 with `sent: false` and why; nothing is an error the producer must handle."""
    from flows_steps import outbound as _out
    if not INTERNAL_SECRET or not _same_key(x_internal_secret, INTERNAL_SECRET):
        raise HTTPException(status_code=403, detail="internal tier only")
    if body.kind not in _PLATFORM_KINDS:
        raise HTTPException(status_code=422, detail=f"{body.kind} is not sent by a platform producer")
    if "@" not in body.to or (body.link and not body.link.startswith(("https://", "http://"))):
        raise HTTPException(status_code=422, detail="to must be an address and link an http(s) URL")
    values = {k: re.sub(r"[\x00-\x1f\x7f]+", " ", str(v))[:300] for k, v in (body.values or {}).items()}
    out = _out.send(body.kind, body.to.strip(), uid=body.uid, values=values, link=body.link, db=db)
    return {"sent": out.sent, "result": out.result, "detail": out.detail,
            "delivery": out.delivery, "template_version": out.template_version}


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
    flows_obs.set_service("flows-api")
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
# the summary (`core/gateway/services/mcp/src/vexa_mcp/bind.py::_describe`). A docstring here
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
    # A TURN OTHER PEOPLE READ (ADR-0048 decision 4) gets the room's subset, never the person's
    # queue: only items naming a workspace on its shared ceiling (`SHARED_NARROWED`).
    room = (_flows_queue.room_workspaces((caller.delegation or {}).get("workspaces"), uid=caller.uid)
            if caller.is_shared else None)
    return _flows_queue.waiting(db, subject=who, flows=flows,
                                limit=max(1, min(int(limit), 200)),
                                identity=_as_me(caller), room=room)


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
