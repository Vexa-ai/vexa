"""The Git credential route (credential-broker.v1 `/api/internal/git-secret`, role `git`).

agent-api keeps a person's saved Git token and the workspaces' deploy keys here, and reads them back
for the Git operations it performs itself. It is the one route that returns a stored value, and only
to the git role.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from .broker import Broker
from .models import GitSecretBody


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/git-secret")
    def git_secret(request: Request, body: GitSecretBody):
        """Agent-api's Git credential store. Only the git role, only names owned by its actor."""
        who = b.identity(request, {"git"})
        owner = body.name.split("/", 1)[1].removesuffix(".priv").removesuffix(".pub")
        if ".." in body.name or who["actor"] != owner:
            raise HTTPException(403, "Git credential scope refused")
        path = "git/" + body.name
        operation = uuid.uuid4().hex
        with b.lock:
            b.audit(who, body.name, "git.credential." + body.action, "requested", operation=operation)
            stored = b.get(path)
            if body.action == "put" or (body.action == "migrate" and stored is None):
                # One broker lock serializes migration with writes and revocation; CAS also
                # refuses an external writer racing the read.
                b.put(path, {"value": body.value}, cas=stored.version if stored else 0)
                stored = b.get(path)
            b.audit(who, body.name, "git.credential." + body.action, "success", operation=operation,
                    receipt_id=stored.receipt if stored else "", version=stored.version if stored else 0)
            return {"found": stored is not None, "value": stored.data.get("value") if stored else None}

    return router
