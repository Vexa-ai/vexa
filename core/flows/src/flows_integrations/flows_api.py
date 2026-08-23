"""flows-api — manage workflows FROM OUTSIDE, faster than any image rebuild.

  GET  /flows                      list every version (code + DB) with status
  POST /flows                      submit {name, on_event, steps:[names], params?, activate?}
                                   — validated against the image's step vocabulary AT SUBMISSION;
                                   version auto-bumps; active in the worker within ~10s
  POST /flows/{name}/{v}/activate  · POST /flows/{name}/{v}/retire
  GET  /reactions[?status=…]       the operator projection
  POST /reactions/{id}/{retry|resume|cancel}   the signal verbs (audited rows)

Auth: X-Flows-Admin-Key (env VEXA_FLOWS_API_KEY, default 'changeme' in dev). NEVER accepts code —
steps are reviewed Python in the image; this API composes them (the n8n line we do not cross)."""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows import Registry, SystemClock, cancel, postgres_db, resume, retry  # noqa: E402
from flows_defs import production  # noqa: E402
from flows_steps.common import db_url  # noqa: E402
import os  # noqa: E402

API_KEY = os.environ.get("VEXA_FLOWS_API_KEY", "changeme")
PORT = int(os.environ.get("VEXA_FLOWS_API_PORT", "18200"))

db = postgres_db(db_url())
clock = SystemClock()
vocab = Registry()
production.build(vocab, db)          # the image's step vocabulary + code-defined flows


class H(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, indent=1).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self) -> bool:
        if self.headers.get("X-Flows-Admin-Key") != API_KEY:
            self._send(401, {"detail": "X-Flows-Admin-Key required"})
            return False
        return True

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if not self._auth():
            return
        if self.path.startswith("/flows"):
            code_flows = [{"name": f.name, "version": f.version, "on": f.on.name,
                           "steps": list(f.steps), "source": "image", "status": "active"}
                          for f in vocab.flows.values()]
            rows = db.execute("SELECT name, version, on_event, steps, params, status, created_by "
                              "FROM flow_version ORDER BY name, version")
            db_flows = [{"name": n, "version": v, "on": e, "steps": json.loads(st),
                         "params": json.loads(p or "{}"), "status": status,
                         "created_by": by, "source": "api"}
                        for n, v, e, st, p, status, by in rows]
            self._send(200, {"steps_vocabulary": sorted(vocab.steps),
                             "flows": code_flows + db_flows})
        elif self.path.startswith("/reactions"):
            q = ""
            if "status=" in self.path:
                want = self.path.split("status=")[1].split("&")[0]
                q = f" WHERE status = '{want}'" if want.isalpha() else ""
            rows = db.execute("SELECT reaction_id, flow, flow_version, step, status, attempt, "
                              f"reason, next_run_at FROM reaction{q} ORDER BY created_at DESC LIMIT 100")
            self._send(200, {"reactions": [
                {"id": r, "flow": f"{fl}@{v}", "step": st, "status": s_, "attempt": a,
                 "reason": why, "next_run_at": nra}
                for r, fl, v, st, s_, a, why, nra in rows]})
        else:
            self._send(404, {"detail": "GET /flows · GET /reactions"})

    def do_POST(self):
        if not self._auth():
            return
        body = {}
        n = int(self.headers.get("content-length") or 0)
        if n:
            try:
                body = json.loads(self.rfile.read(n))
            except Exception:  # noqa: BLE001
                return self._send(400, {"detail": "invalid JSON"})
        parts = [p for p in self.path.split("/") if p]

        if parts == ["flows"]:
            name = str(body.get("name", "")).strip()
            on_event = str(body.get("on_event", "")).strip()
            steps = body.get("steps") or []
            params = body.get("params") or {}
            if not (name and on_event and isinstance(steps, list) and steps):
                return self._send(400, {"detail": "need name, on_event, steps[]"})
            missing = [s_ for s_ in steps if s_ not in vocab.steps]
            if missing:
                return self._send(400, {"detail": f"unknown steps {missing}",
                                        "vocabulary": sorted(vocab.steps)})
            row = db.execute("SELECT COALESCE(MAX(version),0) FROM flow_version WHERE name=:n",
                             {"n": name})
            code_max = max([v for (fn, v) in vocab.flows if fn == name], default=0)
            version = max(row[0][0], code_max) + 1
            status = "active" if body.get("activate", True) else "draft"
            db.execute("""INSERT INTO flow_version (name, version, on_event, steps, params, status,
                                                    created_by, created_at)
                          VALUES (:n,:v,:e,:s,:p,:st,:by,:t)""",
                       {"n": name, "v": version, "e": on_event, "s": json.dumps(steps),
                        "p": json.dumps(params), "st": status,
                        "by": self.headers.get("X-Actor", "api"), "t": clock.now()})
            return self._send(201, {"name": name, "version": version, "status": status,
                                    "live_within_s": 10 if status == "active" else None})

        if len(parts) == 4 and parts[0] == "flows" and parts[3] in ("activate", "retire"):
            st = "active" if parts[3] == "activate" else "retired"
            rows = db.execute("UPDATE flow_version SET status=:s WHERE name=:n AND version=:v "
                              "RETURNING name", {"s": st, "n": parts[1], "v": int(parts[2])})
            return self._send(200 if rows else 404, {"status": st if rows else "not found"})

        if len(parts) == 3 and parts[0] == "reactions" and parts[2] in ("retry", "resume", "cancel"):
            fn = {"retry": retry, "resume": resume, "cancel": cancel}[parts[2]]
            ok = fn(db, parts[1], actor=self.headers.get("X-Actor", "api"), clock=clock,
                    reason=body.get("reason"))
            return self._send(200 if ok else 409, {parts[2]: bool(ok)})

        self._send(404, {"detail": "POST /flows · /flows/{n}/{v}/activate|retire · /reactions/{id}/retry|resume|cancel"})


def main() -> int:
    print(f"flows-api up on :{PORT} · vocabulary of {len(vocab.steps)} steps", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
