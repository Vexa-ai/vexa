"""The signal-model key route (credential-broker.v1 `/api/internal/signals/model-key`, role `signals`).

The transcript-signals service scores live transcript lines with a model the COMPANY picked
(`_global/POLICIES.md` `model_signals`). Its key is a company secret, so it lives here and nowhere
else: the policy names it (`secret_ref: broker:signals/<name>`), the operator stores it once with
`python -m credential_broker.signals_key put <name>` (the value on stdin, never an argument), and
the signals service reads it with its own role key. Nothing in signals' environment holds it.

Bounds: only the `signals` role reaches this route, it carries no X-Vexa-Identity (no person is in
the loop), and it reads ``service/signals/<name>`` and no other path — the name is a single
lowercase segment, so it cannot name a person's connection, a Git credential or a bot profile.
Every read is audited as ``signals.model_key`` with the name, never the value.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from .broker import Broker
from .models import SignalsModelKeyBody

OWNER = "signals"


def record_path(name: str) -> str:
    return "service/signals/" + name


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/signals/model-key")
    def model_key(request: Request, body: SignalsModelKeyBody):
        who = b.identity(request, {"signals"})
        operation = uuid.uuid4().hex
        with b.lock:
            b.audit(who, body.name, "signals.model_key", "requested", operation=operation)
            record = b.get(record_path(body.name), owner=OWNER)
            value = (record.data.get("value") if record else "") or ""
            if not value:
                b.audit(who, body.name, "signals.model_key", "missing", operation=operation)
                raise HTTPException(404, "No signal-model key is stored under this name")
            b.audit(who, body.name, "signals.model_key", "success", operation=operation,
                    version=record.version)
        return {"name": body.name, "value": value}

    return router
