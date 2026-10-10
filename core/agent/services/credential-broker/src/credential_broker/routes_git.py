"""The Git credential route (credential-broker.v1 `/api/internal/git-secret`, role `git`).

agent-api keeps a person's saved Git token and the workspaces' deploy keys here, and reads them back
for the Git operations it performs itself. It is the one route that returns a stored value, and only
to the git role.

Who may read which name is decided from the gateway's signature, not from agent-api: the middleware
has verified the forwarded X-Vexa-Identity and required its subject to be the assertion's actor, so
``who["actor"]`` is the signed person and ``who["memberships"]`` the shared workspaces signed with
them. A name is then theirs when it is ``pat/<person>`` or ``deploy/user-<person>.(priv|pub)``, or
``deploy/ws-<id>.(priv|pub)`` for an ``<id>`` among those memberships.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException, Request

from .broker import Broker
from .models import GitSecretBody


_DEPLOY = re.compile(r"deploy/(user|ws)-(.+)\.(?:priv|pub)")


def owned_by(name: str, person: str, memberships: frozenset) -> bool:
    """Whether the Git credential ``name`` belongs to the signed ``person``."""
    if ".." in name:
        return False
    if name.startswith("pat/"):
        return name[len("pat/"):] == person
    match = _DEPLOY.fullmatch(name)
    if not match:
        return False
    kind, owner = match.groups()
    return owner == person if kind == "user" else owner in memberships


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/git-secret")
    def git_secret(request: Request, body: GitSecretBody):
        """Agent-api's Git credential store. Only the git role, only names the signed person owns."""
        who = b.identity(request, {"git"})
        if not owned_by(body.name, who["actor"], who.get("memberships", frozenset())):
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
