"""The outbound-mail credential route (credential-broker.v1 `/api/internal/mail/smtp-credential`, role `sensors`).

PRD 0.13.4 S2 (E1, FD5): each install configures its own outbound mail — host, port, user and From
in the deployment's environment — and the relay password lives HERE, named by reference
(`VEXA_MAIL_SMTP_PASSWORD_REF=broker:mail/<name>`). The operator stores it once with
`python -m credential_broker.mail_secret put <name>` (the value on stdin, never an argument).

Who reads it: flows, the deployment's one mail sender (`flows_steps.mail_secret`), with the broker
key it already holds — the `sensors` role key (ADR-0050). No new role and no new key file: the key
is flows' service identity at the broker, and this route widens it by exactly one read.

Bounds: only the `sensors` role reaches it, it carries no X-Vexa-Identity (no person is in the loop),
and it reads ``service/mail/<name>`` and no other path — the name is one lowercase segment, so it
cannot name a person's connection, a Git credential, a bot profile or the signal model's key. Every
read is audited as ``mail.smtp_credential`` with the name, never the value.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from .broker import Broker
from .models import MailCredentialBody

OWNER = "mail"


def record_path(name: str) -> str:
    return "service/mail/" + name


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/mail/smtp-credential")
    def smtp_credential(request: Request, body: MailCredentialBody):
        who = b.identity(request, {"sensors"})
        operation = uuid.uuid4().hex
        with b.lock:
            b.audit(who, body.name, "mail.smtp_credential", "requested", operation=operation)
            record = b.get(record_path(body.name), owner=OWNER)
            value = (record.data.get("value") if record else "") or ""
            if not value:
                b.audit(who, body.name, "mail.smtp_credential", "missing", operation=operation)
                raise HTTPException(404, "No mail credential is stored under this name")
            b.audit(who, body.name, "mail.smtp_credential", "success", operation=operation,
                    version=record.version)
        return {"name": body.name, "value": value}

    return router
