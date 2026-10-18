"""asks.py — the 7g inline card as a real PENDING ITEM (PRD 0.13.4 S5, H3, deny test 13).

UI-DECISIONS 7g: an agent asks the person to choose or confirm with one inline card (a fenced
```ask block in its reply). Until S5 a press arrived as ordinary chat text ``[answer <id>] …`` and
nothing on the server bound it to the card: a replay, a forged id, or an agent typing the line for
the person all read the same. Now:

* ``POST /api/asks/open {session, ask_id}`` — the person's terminal opens the card it is showing.
  agent-api reads the AGENT's own turns of that chat (the transcript, never the client's copy), finds
  the ```ask fence with that id, and records a pending item (a session proposal of kind ``ask``)
  owned by the person, bound to a fresh server nonce and the content hash of the canonical card.
* ``POST /api/asks/{pending_id}/answer {nonce, content_hash, reply, text?}`` — the press. Refused to
  every delegated caller and to anything but the terminal's session credential (D13); refused unless
  the nonce matches, the content hash equals both the item's and the card as the transcript holds it
  now, the reply is one the card offered or the person's own words, and the item is still open. Any
  card takes a typed answer (the person can always answer in their own words, up to TEXT_MAX); a
  typed answer is bound exactly like a press — nonce, content hash, single use, session credential.
  It is decided ONCE: a replay is a 409. The answer is returned as the message the person's chat
  sends (``[answer <id> #<pending id>] …``), so the agent can see it was a bound press.

The nonce is stored only as its sha256; the item is the session store's proposal row, so its one
writer is agent-api and its decision is single-use by the store's own ``already_decided`` rule.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import policy_decide as _pd
from control_plane.ceiling import refuse_delegated
from control_plane.routers.chat_read import _turns_of

_FENCE = re.compile(r"```ask[ \t]*\r?\n(.*?)```", re.S)
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LABEL_MAX = 80
TEXT_MAX = 4000


class AskOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(min_length=1, max_length=130)
    ask_id: str = Field(min_length=1, max_length=80)


class AskReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=LABEL_MAX)
    reply: Optional[str] = Field(default=None, max_length=300)


class AskAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=16, max_length=100)
    content_hash: str = Field(min_length=64, max_length=64)
    reply: Optional[AskReply] = None
    # Checked in the handler, not by the model, so an over-long answer is refused in plain words.
    text: Optional[str] = Field(default=None, max_length=TEXT_MAX * 4)


def canonical_hash(spec: dict) -> str:
    """sha256 of the card as the agent wrote it, canonically (sorted keys, compact)."""
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def find_ask(turns: list[dict], ask_id: str) -> Optional[dict]:
    """The LAST ```ask card with this id in the AGENT's turns, as parsed JSON; None when there is none.
    A person's own message never counts: an ask is the agent's to pose."""
    found = None
    for t in turns or []:
        if t.get("role") not in ("agent", "assistant"):
            continue
        for raw in _FENCE.findall(str(t.get("text") or "")):
            try:
                spec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(spec, dict) and str(spec.get("id") or "") == ask_id:
                found = spec
    return found


def offered(spec: dict) -> set[str]:
    """The labels the card offers: its chips and its primary/secondary buttons."""
    out: set[str] = set()
    for item in list(spec.get("chips") or []) + [spec.get("primary"), spec.get("secondary")]:
        if isinstance(item, str) and item.strip():
            out.add(item.strip()[:LABEL_MAX])
        elif isinstance(item, dict) and str(item.get("label") or "").strip():
            out.add(str(item["label"]).strip()[:LABEL_MAX])
    return out


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail="the session store is not configured")
        return store

    def _person(request: Request) -> str:
        refuse_delegated(request, reason="answer_is_the_person",
                         instruction="Only the person answers their own card, by pressing it in the app. "
                                     "Wait for their answer; do not answer it for them.")
        if _pd.credential_of(request.headers) != _pd.SESSION:
            raise HTTPException(status_code=403, detail={
                "refused": "session_required",
                "note": "a card is answered from the signed-in app, never with an API key (D13)"})
        return str(subject_of(request))

    def _session(subject: str, ref: str) -> dict:
        s = _store()
        if not _SESSION.fullmatch(ref):
            raise HTTPException(status_code=404, detail="chat not found")
        row = s.get(ref) or s.by_name(subject, ref)
        if row is None or not s.can_read(row["id"], subject):
            raise HTTPException(status_code=404, detail="chat not found")
        return row

    def _card(row: dict, ask_id: str) -> dict:
        try:
            turns = _turns_of(_store().load_bundle(row["id"]).get("files") or {})
        except Exception:  # noqa: BLE001 — an unreadable transcript binds nothing (fail closed)
            turns = []
        spec = find_ask(turns, ask_id)
        if spec is None:
            raise HTTPException(status_code=404, detail="no such card in this chat")
        return spec

    def _items(row: dict, subject: str, ask_id: str) -> list[dict]:
        out = []
        for p in _store().proposals(row["id"]):
            if p["kind"] != "ask" or p["owner"] != subject:
                continue
            try:
                body = json.loads(p["body"])
            except ValueError:
                continue
            if body.get("ask_id") == ask_id:
                out.append({**p, "_body": body})
        return out

    @router.post("/api/asks/open")
    def ask_open(body: AskOpen, request: Request):
        """Open the pending item behind an inline card this person is looking at."""
        subject = _person(request)
        if not _ID.fullmatch(body.ask_id):
            raise HTTPException(status_code=422, detail="not a card id")
        row = _session(subject, body.session)
        spec = _card(row, body.ask_id)
        digest = canonical_hash(spec)
        items = _items(row, subject, body.ask_id)
        done = next((p for p in items if p["status"] == "accepted" and p["_body"].get("content_hash") == digest), None)
        if done is not None:
            return {"pending_id": done["id"], "status": "answered", "spec": spec, "content_hash": digest,
                    "answer": done["_body"].get("answer")}
        for p in items:                      # a re-open retires the earlier nonce: one live item per card
            if p["status"] == "open":
                _store().decide(p["id"], subject, accept=False)
        nonce = secrets.token_urlsafe(24)
        item = _store().propose(row["id"], owner=subject, kind="ask", title=f"ask {body.ask_id}"[:300],
                                body=json.dumps({"ask_id": body.ask_id, "content_hash": digest,
                                                 "nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest()}))
        return {"pending_id": item["id"], "status": "open", "nonce": nonce, "content_hash": digest, "spec": spec}

    @router.post("/api/asks/{pending_id}/answer")
    def ask_answer(pending_id: str, body: AskAnswer, request: Request):
        """The person's press, bound to the pending item: once, by them, on the card they saw."""
        subject = _person(request)
        s = _store()
        p = s.proposal(pending_id)
        if p is None or p["kind"] != "ask" or p["owner"] != subject:
            raise HTTPException(status_code=404, detail="no such pending card")
        if p["status"] != "open":
            raise HTTPException(status_code=409, detail="already_answered")
        item = json.loads(p["body"])
        if not hmac.compare_digest(hashlib.sha256(body.nonce.encode()).hexdigest(), item.get("nonce_sha256", "")):
            raise HTTPException(status_code=409, detail="not_this_card")
        row = s.get(p["session_id"])
        if row is None or not s.can_read(row["id"], subject):
            raise HTTPException(status_code=404, detail="chat not found")
        spec = _card(row, item["ask_id"])
        now_hash = canonical_hash(spec)
        if not (hmac.compare_digest(body.content_hash, item.get("content_hash", ""))
                and hmac.compare_digest(body.content_hash, now_hash)):
            raise HTTPException(status_code=409, detail="card_changed")
        label = body.reply.label.strip() if body.reply else ""
        text = (body.text or "").strip()
        if label and label not in offered(spec):
            raise HTTPException(status_code=422, detail="That choice isn't on this card any more.")
        if len(text) > TEXT_MAX:
            raise HTTPException(status_code=422,
                                detail=f"That answer is too long ({len(text)} characters). Keep it under {TEXT_MAX}.")
        if not label and not text:
            raise HTTPException(status_code=422, detail="Type an answer or pick one of the choices.")
        said = (body.reply.reply or label) if body.reply else ""
        answer = " — ".join(x for x in (said, text) if x)
        try:
            s.decide(pending_id, subject, accept=True, body=json.dumps({**item, "answer": answer}))
        except Exception as exc:  # noqa: BLE001 — a second decision races here: the store refuses it
            raise HTTPException(status_code=409, detail="already_answered") from exc
        return {"status": "answered", "pending_id": pending_id,
                "message": f"[answer {item['ask_id']} #{pending_id}] {answer}"}

    return router
