"""The sim lane's own door into the flows engine.

The founder's hot lane (:18200, database `flows`) admits REAL invites from a REAL inbox. This
lane is a second worker + api (:18201, database `flows_sim`) running the adoption-sim worktree,
with the mailbox integration deliberately not started. Nothing this file does can reach a real
recipient: it posts facts to :18201 only, and the fan-out's domain allow-list is the sim's own
three test domains.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("SIM_FLOWS_API", "http://127.0.0.1:18201")
KEY = os.environ.get("SIM_FLOWS_KEY", "simlane")


def _req(method: str, path: str, body=None, timeout=30):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("content-type", "application/json")
    req.add_header("X-Flows-Admin-Key", KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)


def emit(event_type: str, source_event_id: str, refs: dict):
    """Admit ONE fact into the sim lane."""
    return _req("POST", "/events", {"event_type": event_type,
                                    "source_event_id": source_event_id, "refs": refs})


def reactions(status: str = ""):
    return _req("GET", f"/reactions?status={status}" if status else "/reactions")


def flow_params(name: str, on_event: str, steps: list, params: dict):
    """Submit a new active version of a flow carrying `params` — how a lever (shared vs
    personal follow-up, sharing off) is turned between revolutions without a code change."""
    return _req("POST", "/flows", {"name": name, "on_event": on_event, "steps": steps,
                                   "params": params, "activate": True})


def wait_reaction(source_event_id: str, want=("done", "failed", "cancelled"), timeout_s=1200):
    """Block until the reaction for this fact settles. Returns the row or None."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        st, body = reactions()
        rows = body.get("reactions", body) if isinstance(body, dict) else body
        for r in (rows or []):
            if str(r.get("source_event_id", "")).startswith(source_event_id):
                last = r
                if r.get("status") in want:
                    return r
        time.sleep(6)
    return last
