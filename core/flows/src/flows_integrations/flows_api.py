"""flows-api — manage workflows FROM OUTSIDE, faster than any image rebuild. FastAPI, house-style
(the same shape as meeting-api/agent-api/admin-api), OpenAPI docs at /docs.

  GET  /flows                       every version (image + DB) + the step vocabulary
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
  GET  /timeline?subject=…          ONE PERSON'S DAY, in order — facts, receipts and the
                                    meetings table merged and scoped to them (PRD decision 31).
                                    Read-only, and it takes the operator key OR the narrower
                                    VEXA_FLOWS_TIMELINE_KEY (see `_timeline_key`).

AUTH, TWO TIERS — because two different callers reach this surface and only one of them is an
operator (issue #1468):

  * THE SUBJECT-SCOPED ROUTES (`GET /flows`, `GET /reactions`, `POST /reactions/{id}/{verb}`,
    `GET /queue/waiting`, `GET /timeline`) take EITHER the operator key OR a person's own Vexa
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
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Body, Depends, FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from flows import Registry, SystemClock, admit, cancel, db_from_url, resume, retry, wake  # noqa: E402
import flows_config  # noqa: E402
from flows_defs import production  # noqa: E402
from flows_integrations import instance_gate  # noqa: E402
from flows_steps.common import db_url, require_internal_secret, setting  # noqa: E402
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


API_KEY = _require_api_key()


def _timeline_key() -> str:
    """A SECOND key, for `GET /timeline` alone — or "" when the deployment has not minted one.

    The timeline is read-only and the agent worker asks for it on EVERY dispatch (decision 31 §1),
    so it needs a credential in every worker container. Handing those containers the OPERATOR key —
    the one that submits and activates flows — to read a list of times would widen the operator
    key's blast radius by one container per person per turn, which is the opposite of what the
    key-hardening above was for. This is a key that can do exactly one thing.

    Unset ⇒ only the operator key opens the route. That is the right default: a deployment that has
    not thought about this gets the narrower reach (nobody but the operator), never the wider one.
    """
    key = (os.environ.get("VEXA_FLOWS_TIMELINE_KEY") or "").strip()
    return "" if key in ("changeme", "change-me", "default", "secret") else key


TIMELINE_KEY = _timeline_key()
# The internal-tier identity, refused the same way and for the same reason. Read at import so an
# unconfigured deployment stops HERE rather than at the first post-meeting run.
flows_config.preflight()          # no door, no boot — see flows_config's DOORS block
INTERNAL_SECRET = require_internal_secret()

logger = logging.getLogger(__name__)

db = db_from_url(db_url())
clock = SystemClock()
vocab = Registry()
production.build(vocab, db)

import json as _json
import pathlib as _pathlib

app = FastAPI(title="flows-api", version="0.1.0",
              description="Submit and manage Vexa workflows as data — no code over the wire.")


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
    if _same_key(_operator_key(x_flows_operator_key, x_flows_admin_key), API_KEY):
        return Caller(kind="admin")
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
        raise HTTPException(status_code=503, detail=(
            f"identity could not answer who you are ({e}) — this is our side, not your key"))


def timeline_reader(x_flows_operator_key: str = Header(default=""),
                    x_flows_admin_key: str = Header(default=""),
                    authorization: str = Header(default=""),
                    x_api_key: str = Header(default="")) -> Caller:
    """`GET /timeline` alone: the narrow read-only key opens it, as well as the two tiers above."""
    if TIMELINE_KEY and _same_key(_operator_key(x_flows_operator_key, x_flows_admin_key),
                                  TIMELINE_KEY):
        return Caller(kind="admin")
    return subject_or_operator(x_flows_operator_key, x_flows_admin_key, authorization, x_api_key)


def scoped_subject(caller: Caller, requested: str) -> str:
    """The subject these rows are about — derived, never asserted.

    For the OPERATOR nothing changes: `subject` is whatever they asked for, including nothing,
    which is the unscoped console read this surface has always served.

    For a PERSON the subject is who their credential says they are. A `subject` argument is still
    accepted, because the tool schema advertises one and a client that holds its own uid or address
    will send it — but only as a spelling of themselves. Anything else is 403 rather than silently
    overridden: an argument quietly ignored is the same defect as one quietly dropped, and here it
    would be the difference between "here is your queue" and "here is someone else's".
    """
    asked = str(requested or "").strip()
    if caller.is_admin:
        return asked
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
    """
    if caller.is_admin:
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


@app.get("/flows")
def list_flows(caller: Caller = Depends(subject_or_operator)):
    code_flows = [{"name": f.name, "version": f.version, "on": f.on.name,
                   "steps": list(f.steps), "source": "image", "status": "active"}
                  for f in vocab.flows.values()]
    rows = db.execute("SELECT name, version, on_event, steps, params, status, created_by "
                      "FROM flow_version ORDER BY name, version")
    db_flows = [{"name": n, "version": v, "on": e, "steps": json.loads(st),
                 "params": json.loads(p or "{}"), "status": status,
                 "created_by": by, "source": "api"}
                for n, v, e, st, p, status, by in rows]
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
    operator key, so nobody's identity ever reached this decision. Now a person's subject comes
    from their credential and the check always runs; an operator with no subject keeps the unscoped
    console behaviour, because the admin console steers reactions it does not own.
    """
    fns = {"retry": retry, "resume": resume, "cancel": cancel, "wake": wake}
    if verb not in fns:
        raise HTTPException(status_code=404, detail="retry | resume | cancel | wake")
    subj = scoped_subject(caller, subject)
    if subj:
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
    out = build_timeline(db, subj, since=since or None, until=until or None,
                         limit=max(1, min(int(limit or 20), 200)),
                         meetings=fetch_meetings if meetings else None,
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


def _friction_id() -> str:
    """`fr_<16 hex>` — short enough to paste into a fix reference by hand, same convention the
    (now-retired) agent-api store used, kept for continuity across the cutover."""
    return f"fr_{secrets.token_hex(8)}"


@app.post("/friction", status_code=201)
def report_friction(session: str = "", what_i_tried: str = "", what_happened: str = "",
                    severity: str = "annoyance", meeting_id: str = "", tool: str = "",
                    deployment: str = "", worker_image: str = "", kind: str = "",
                    x_user_id: str = Header(default=""),
                    caller: Caller = Depends(subject_or_operator)):
    """Tell us what did not work. Any signed-in caller may file, from any client — a Claude Code
    session, the rig, a worker turn — whether or not this deployment runs the agent domain at all.

    `session`, `what_i_tried`, `what_happened` and `severity` are REQUIRED. `session` most of all:
    it is the chat or meeting session this happened in, and its absence is exactly the gap this
    carrier exists to close — the founder's own 13 reports from one live call could not be tied
    back to the call that produced them because nothing on the old path carried an id for it. A
    report with no session is refused rather than accepted with one more field nobody can join on
    later (`friction_since=""` on the timeline is the only thing separating them then).

    The subject is the caller's own credential, same as every other person-scoped route here
    (`reactions_list`, `timeline`); an operator with no bearer may stamp `X-User-Id`, same as
    `queue_waiting` — the gateway's answer when it forwards a resolved caller rather than a raw
    bearer. The bare operator key, unstamped, attributes a report to nobody, which is refused.
    """
    subj = scoped_subject(caller, "")
    if caller.is_admin:
        subj = (x_user_id or "").strip() or subj
    if not subj:
        raise HTTPException(status_code=401, detail=(
            "report_friction needs your Vexa credential — this edge cannot attribute a report to "
            "nobody"))
    sess = session.strip()
    if not sess:
        raise HTTPException(status_code=400, detail=(
            "session is required — the chat or meeting session this happened in. A report with no "
            "session cannot be tied back to the conversation that produced it, which is the exact "
            "gap this carrier exists to close."))
    tried, happened = what_i_tried.strip()[:FRICTION_TEXT_MAX], what_happened.strip()[:FRICTION_TEXT_MAX]
    if not tried or not happened:
        raise HTTPException(status_code=400, detail=(
            "what_i_tried and what_happened are both required — half-formed is fine, empty is not"))
    sev = severity.strip().lower()
    if sev not in FRICTION_SEVERITIES:
        raise HTTPException(status_code=400,
                            detail={"severity": severity, "expected": list(FRICTION_SEVERITIES)})
    knd = kind.strip().lower()
    if knd and knd not in FRICTION_KINDS:
        raise HTTPException(status_code=400, detail={"kind": kind, "expected": list(FRICTION_KINDS)})
    fid = _friction_id()
    refs = {"uid": subj, "session": sess[:128], "friction_id": fid,
           "what_i_tried": tried, "what_happened": happened, "severity": sev}
    for key, val in (("kind", knd), ("meeting_id", meeting_id), ("tool", tool),
                     ("deployment", deployment), ("worker_image", worker_image)):
        v = val.strip()[:200] if isinstance(val, str) else ""
        if v:
            refs[key] = v
    vocab.refresh_from_db(db)          # the friction_log flow may have been (re)submitted since boot
    # LITERAL, not `production.FRICTION_REPORTED.name` — `tests/test_queue_waiting.py::_produced()`
    # proves `publishes_events` against what this domain's source actually writes to the wire by
    # grepping for `event_type="<literal>"`, the same way it already proves `invite.received`. An
    # attribute expression would be correct at runtime and invisible to that check.
    assert production.FRICTION_REPORTED.name == "friction.reported"
    admit(db, vocab, clock, source_event_id=f"friction-{fid}",
         event_type="friction.reported", subject_refs=refs)
    return {"id": fid, "recorded": True}


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
    subj = scoped_subject(caller, "")
    if caller.is_admin:
        subj = (x_user_id or "").strip() or subj
    if not subj:
        raise HTTPException(status_code=400, detail="no subject — sign in to read your own reports")
    ts = _friction_since_epoch(since) or 0.0 if since else 0.0
    rows = friction_for_subject(db, subject=subj, since=ts,
                                limit=max(1, min(int(limit or 40), 200)),
                                identity=_as_me(caller))
    if rows is None:
        raise HTTPException(status_code=404, detail=f"nobody answers to {subj!r}")
    return {"subject": subj, "count": len(rows), "reports": rows}


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


def main() -> int:  # pragma: no cover — process entrypoint
    import uvicorn
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


@app.get("/queue/waiting")
def queue_waiting(subject: str = "", limit: int = 50,
                  x_user_id: str = Header(default=""),
                  caller: Caller = Depends(subject_or_operator)):
    """WHAT IS WAITING FOR THIS PERSON — pending reactions, with the flow that produced each.

    THE SUBJECT IS THE AUTHENTICATED CALLER'S, and there are two ways to be one.

    A PERSON authenticates with their own Vexa credential and their subject is resolved from it
    (issue #1468). Nothing they send can move it: a `subject` argument naming anyone else is 403,
    and an `X-User-Id` header is ignored outright. That header is a string a caller can type, and
    it is only ever evidence because the OPERATOR key gates it — a service vouching for a person it
    resolved. A verified credential is stronger evidence than any header, so it wins.

    THE OPERATOR reads one person's queue on their behalf, and keeps exactly the behaviour this
    route shipped with: `X-User-Id` is the gateway's answer and outranks `?subject=`, and
    `?subject=` is the unstamped console read. Neither ever answers with the instance — no subject
    at all is a 400, because this route answers for ONE person or for nobody.

    The distinction matters because this route is NOT always behind the gateway: reached through
    the MCP edge it is addressed directly, and that edge stamps no `X-User-Id` at all — it forwards
    the caller's own credential, which is the whole reason a person's credential has to open the
    door here.

    Not opened by `VEXA_FLOWS_TIMELINE_KEY`: that key is documented as opening the timeline and
    nothing else, and quietly widening a key's reach is how a narrow credential stops being narrow.

    The answer is DATA plus behavior's words: every sentence a person hears is resolved from
    `behavior/queue/`, read hot, never from this body. See `flows_queue` for why silence there is
    the filter rather than a keyword list here.
    """
    who = scoped_subject(caller, subject)
    if caller.is_admin:
        who = (x_user_id or "").strip() or who
    if not who:
        raise HTTPException(status_code=400, detail=(
            "no subject — this route answers for ONE person. A person is resolved from their own "
            "credential; an operator passes ?subject=<uid|email>, or the gateway stamps "
            "X-User-Id."))
    flows = [{"name": f.name, "version": f.version, "on": f.on.name}
             for f in vocab.flows.values()]
    return _flows_queue.waiting(db, subject=who, flows=flows,
                                limit=max(1, min(int(limit), 200)))


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
