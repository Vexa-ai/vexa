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

Auth: X-Flows-Admin-Key (env VEXA_FLOWS_API_KEY). NEVER accepts code — steps are reviewed Python
in the image; this API composes them (the n8n line we do not cross)."""
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
from flows_steps.common import db_url  # noqa: E402

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

db = postgres_db(db_url())
clock = SystemClock()
vocab = Registry()
production.build(vocab, db)

app = FastAPI(title="flows-api", version="0.1.0",
              description="Submit and manage Vexa workflows as data — no code over the wire.")


def auth(x_flows_admin_key: str = Header(default="")) -> None:
    if x_flows_admin_key != API_KEY:
        raise HTTPException(status_code=401, detail="X-Flows-Admin-Key required")


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
    return {"steps_vocabulary": [
        {"name": n, "doc": " ".join((vocab.steps[n].__doc__ or "undocumented").split())}
        for n in sorted(vocab.steps)],
        "flows": code_flows + db_flows}


@app.post("/flows", status_code=201, dependencies=[Depends(auth)])
def submit_flow(sub: FlowSubmission, x_actor: str = Header(default="api")):
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


@app.post("/events", status_code=202, dependencies=[Depends(auth)])
def admit_event(ev: EventSubmission):
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
    n = admit(db, vocab, clock, source_event_id=ev.source_event_id,
              event_type=ev.event_type, subject_refs=ev.refs)
    return {"event_type": ev.event_type, "source_event_id": ev.source_event_id,
            "reactions_created": n, "duplicate": n == 0}


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
def admit_batch(batch: SeedBatch, x_actor: str = Header(default="admin")):
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
    out, admitted, dupes = [], 0, 0
    for i, m in enumerate(batch.meetings):
        sid = m.ics_uid or f"{batch.prefix}-{stamp}-{i:04d}"
        refs = {"organizer": m.organizer, "url": m.url, "start": float(m.start),
                "ics_uid": sid, "title": m.title, "group": m.group,
                "participants": list(m.participants)}
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
    log = {"actor": x_actor, "submitted": len(batch.meetings),
           "admitted": admitted, "duplicates": dupes,
           "failed": sum(1 for r in out if "error" in r)}
    return {**log, "meetings": out}


@app.post("/flows/{name}/{version}/{action}", dependencies=[Depends(auth)])
def set_flow_status(name: str, version: int, action: str):
    if action not in ("activate", "retire"):
        raise HTTPException(status_code=404, detail="activate | retire")
    st = "active" if action == "activate" else "retired"
    rows = db.execute("UPDATE flow_version SET status=:s WHERE name=:n AND version=:v RETURNING name",
                      {"s": st, "n": name, "v": version})
    if not rows:
        raise HTTPException(status_code=404, detail="flow version not found")
    return {"name": name, "version": version, "status": st}


@app.get("/reactions", dependencies=[Depends(auth)])
def list_reactions(status: Optional[str] = None):
    q, params = "", {}
    if status and status.isalpha():
        q, params = " WHERE status = :st", {"st": status}
    rows = db.execute("SELECT reaction_id, flow, flow_version, step, status, attempt, reason, "
                      f"next_run_at FROM reaction{q} ORDER BY created_at DESC LIMIT 100", params)
    return {"reactions": [
        {"id": r, "flow": f"{fl}@{v}", "step": st, "status": s_, "attempt": a,
         "reason": why, "next_run_at": nra}
        for r, fl, v, st, s_, a, why, nra in rows]}


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


def main() -> int:  # pragma: no cover — process entrypoint
    import uvicorn
    port = int(os.environ.get("VEXA_FLOWS_API_PORT", "18200"))
    print(f"flows-api up on :{port} · vocabulary of {len(vocab.steps)} steps", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
