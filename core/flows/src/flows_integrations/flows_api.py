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
  GET  /timeline?subject=…          ONE PERSON'S DAY, in order — facts, receipts and the
                                    meetings table merged and scoped to them (PRD decision 31).
                                    Read-only, and it takes the operator key OR the narrower
                                    VEXA_FLOWS_TIMELINE_KEY (see `_timeline_key`).

Auth: X-Flows-Admin-Key (env VEXA_FLOWS_API_KEY). NEVER accepts code — steps are reviewed Python
in the image; this API composes them (the n8n line we do not cross).

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

import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Body, Depends, FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from flows import Registry, SystemClock, admit, cancel, postgres_db, resume, retry, wake  # noqa: E402
from flows_defs import production  # noqa: E402
from flows_integrations import instance_gate  # noqa: E402
from flows_steps.common import db_url, require_internal_secret, setting  # noqa: E402
from flows_timeline import (build_timeline, fetch_meetings, list_reactions,  # noqa: E402
                            render_preamble, render_text)

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
    """
    key = (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "VEXA_FLOWS_API_KEY is unset — flows-api refuses to start rather than serve on a "
            "default. Mint one into a mode-600 file (the ~/.storm/dburl pattern) and export it "
            "from the lane's start script; never put the value in the repo.")
    if key in ("changeme", "change-me", "default", "secret"):
        raise RuntimeError(
            f"VEXA_FLOWS_API_KEY is the placeholder {key!r} — refusing to start.")
    return key


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
INTERNAL_SECRET = require_internal_secret()

db = postgres_db(db_url())
clock = SystemClock()
vocab = Registry()
production.build(vocab, db)

app = FastAPI(title="flows-api", version="0.1.0",
              description="Submit and manage Vexa workflows as data — no code over the wire.")


def auth(x_flows_admin_key: str = Header(default="")) -> None:
    if x_flows_admin_key != API_KEY:
        raise HTTPException(status_code=401, detail="X-Flows-Admin-Key required")


def timeline_auth(x_flows_admin_key: str = Header(default="")) -> None:
    """The operator key opens everything, including this. The timeline key opens only this."""
    if x_flows_admin_key == API_KEY or (TIMELINE_KEY and x_flows_admin_key == TIMELINE_KEY):
        return
    raise HTTPException(status_code=401, detail="X-Flows-Admin-Key required")


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


@app.get("/flows", dependencies=[Depends(auth)])
def list_flows():
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
            out.append({"source_event_id": sid, "title": m.title,
                        "error": f"{type(e).__name__}: {e}"[:200]})
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


@app.get("/reactions", dependencies=[Depends(auth)])
def list_reactions(status: Optional[str] = None, subject: str = ""):
    """The operator projection — and, with ``subject``, ONE PERSON'S share of it.

    ``subject`` (a platform uid or an email address) is what makes this route usable by a
    per-person surface. Without it the control MCP was fanning the whole instance's reactions into
    every signed-in user's queue: `whats_waiting` reported other tenants' flow names, step names
    and failure reasons as this person's work, and `reactions_list` handed out every reaction id
    instance-wide — which is also how `reaction_signal` could cancel a stranger's pending join
    (R-D07, R-D12).

    Scoping reuses `flows_timeline`'s pair — the uid AND the email — for the reason that module
    documents: the invite lineage carries an organizer address and no uid, the completed lineage
    carries a uid and no address, and matching on one of them silently returns half the rows.
    """
    subj = (subject or "").strip()
    rows = list_reactions(db, subject=subj, status=status or "")
    if rows is None:
        return {"reactions": [], "subject": subj, "unresolved": True}
    return {"reactions": [
        {"id": r["reaction_id"], "flow": f"{r['flow']}@{r['flow_version']}", "step": r["step"],
         "status": r["status"], "attempt": r["attempt"], "reason": r["reason"],
         "next_run_at": r["next_run_at"]}
        for r in rows]}


@app.post("/reactions/{reaction_id}/{verb}", dependencies=[Depends(auth)])
def signal_reaction(reaction_id: str, verb: str, x_actor: str = Header(default="api"),
                    body: dict = Body(default={})):
    fns = {"retry": retry, "resume": resume, "cancel": cancel, "wake": wake}
    if verb not in fns:
        raise HTTPException(status_code=404, detail="retry | resume | cancel | wake")
    ok = fns[verb](db, reaction_id, actor=x_actor, clock=clock, reason=body.get("reason"))
    if not ok:
        raise HTTPException(status_code=409, detail=f"{verb} not applicable in current status")
    return {verb: True}


@app.get("/timeline", dependencies=[Depends(timeline_auth)])
def timeline(subject: str = "", since: str = "", until: str = "", limit: int = 20,
             meetings: bool = True, format: str = "json"):
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
    subj = (subject or "").strip()
    if not subj:
        raise HTTPException(status_code=400,
                            detail="subject is required: a platform uid, or an email address")
    if not (subj.isdigit() or "@" in subj):
        raise HTTPException(status_code=400, detail={
            "not_a_subject": subj,
            "expected": "a platform uid (digits) or an email address"})
    out = build_timeline(db, subj, since=since or None, until=until or None,
                         limit=max(1, min(int(limit or 20), 200)),
                         meetings=fetch_meetings if meetings else None)
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


def main() -> int:  # pragma: no cover — process entrypoint
    import uvicorn
    port = int(os.environ.get("VEXA_FLOWS_API_PORT", "18200"))
    print(f"flows-api up on :{port} · vocabulary of {len(vocab.steps)} steps", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
