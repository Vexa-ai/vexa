"""flows-api — manage workflows FROM OUTSIDE, faster than any image rebuild. FastAPI, house-style
(the same shape as meeting-api/agent-api/admin-api), OpenAPI docs at /docs.

  GET  /flows                       every version (image + DB) + the step vocabulary
  POST /flows                       submit {name, on_event, steps:[names], params?, activate?}
                                    — validated against the deployed vocabulary AT SUBMISSION;
                                    auto-versioned; live in the worker within ~10 s
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

from flows import Registry, SystemClock, cancel, postgres_db, resume, retry, wake  # noqa: E402
from flows_defs import production  # noqa: E402
from flows_steps.common import db_url  # noqa: E402

API_KEY = os.environ.get("VEXA_FLOWS_API_KEY", "changeme")

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
