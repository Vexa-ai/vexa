"""routers/agent_messages.py — agents speak to each other (ADR-0046 decision 13).

An agent's address is its chat thread. `POST /api/agents/message` — the `message_agent` tool — takes
the thread to write to (and, for another person's thread, whose it is), the text, and the thread
the caller is writing FROM, which is where any answer comes back. This route checks the two things
only this domain knows and hands the rest to flows:

  * the sending thread is the caller's own — the caller is the signed identity (ADR-0041), and a
    thread is checked against that person's session index, never taken on the caller's word;
  * the target thread exists, and — across two people — that it is a conversation both of them
    are in: a stored session (`s-…`) where the sender takes part with write and the other person
    takes part, inside a workspace where the sender holds a writing role and the other person is a
    member. Another person's own threads — their `main`, a routine's `flow-…` thread, a session the
    sender is not in or only reads — are never an address, because a delivered message starts a
    turn there with nobody watching. Every way a cross-person address fails answers the same
    refusal, so the answer says nothing about which of that person's threads exist;
  * WHO IS SENDING. A worker running with nobody in the loop (the delegation's regime is not
    `human`) sends as a routine unless the thread it names is answering a message delivered into it
    within the last hour — that thread's chain is then the one it carries forward, so neither the
    routine rule nor the hop count is reset by naming a different thread.

It then publishes `thread.message`. flows' intake applies the policy keys in `_global/POLICIES.md`,
the loop guard and the per-person message count, and its `agent_messages` flow queues the text as a
turn in the target thread, marked as from this agent. Everything is asynchronous: there is no
ask-and-wait, so two agents cannot deadlock, and a reply arrives as a later turn in the sender's
thread.

THE ORIGIN CHAIN. A conversation's hops ride every message: a delivered message stamps its chain on
the receiving thread (`chats._chat`, `agent_chain` in the session index), and a message sent FROM
that thread carries it forward plus one hop. flows refuses the hop that reaches the limit, which is
what ends two agents answering each other forever."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import ceiling
from control_plane import publish as publish_mod
from control_plane.bodies import SessionId

#: How long a delivered message's chain stays the chain of the thread it landed in. A reply long
#: after the conversation ended starts a new one.
CHAIN_TTL_S = 3600.0
MESSAGE_MAX = 8000
#: Workspace roles that write. A reader (`viewer`) reads a workspace and does not speak into it, so
#: membership at that role is not what makes two people's agents able to talk.
WRITE_ROLES = frozenset({"owner", "contributor", "member"})
#: Session roles that write (session_store.ROLES minus `viewer`).
SESSION_WRITE_ROLES = frozenset({"owner", "member"})
#: The one answer to every cross-person address that cannot be delivered — absent, not shared, not a
#: conversation the sender is in. Identical by design: it names no fact about the other person.
CROSS_PERSON_REFUSAL = {
    "refused": "agent_messages_shared",
    "note": "another person's agent is reached only in a conversation (`s-…`) you both take part "
            "in and you can write to, inside a workspace you both belong to where you are not a "
            "reader"}


class AgentMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread: SessionId = Field(min_length=1, description="the chat thread to write to — the "
                              "receiving agent's address")
    text: str = Field(min_length=1, max_length=MESSAGE_MAX,
                      description="what to tell the other agent; it arrives as a turn in its thread")
    from_thread: SessionId = Field(min_length=1, description="the chat thread you are writing from, "
                                   "as the turn context states it — where the answer comes back")
    person: Optional[str] = Field(default=None, max_length=200,
                                  description="whose thread it is, when it is not your own person's "
                                              "(their user id); omit for your own person's threads")


def _thread_of(sess, subject: str, session: str, store=None) -> Optional[dict]:
    """``subject``'s thread ``session`` — from their chat index, or (ADR-0048) from the session
    store: a thread of their own by name, or a stored session (``s-…``) they take part in. A
    stored thread bound to a flow is a routine's, whatever the index row says."""
    row = next((r for r in sess.list(subject) if r.get("session") == session), None)
    if store is None:
        return row
    from control_plane import session_store as store_mod, session_sync  # noqa: PLC0415
    try:
        st = session_sync.resolve(store, subject, session, create=False)
    except Exception:  # noqa: BLE001 — an unreadable store answers with the index alone
        st = None
    if st is None:
        return row
    bound = store_mod.read_flow(st.get("flow"))
    out = dict(row or {"session": session})
    out["session_id"] = st["id"]
    if bound and not out.get("routine"):
        out["routine"] = {**bound, "step": "agent_run"}
    return out


def _shared(mindex, sender: str, target: str) -> bool:
    """A workspace both belong to in which the SENDER holds a writing role. The other person's role
    does not matter; the sender's does — a reader of a workspace does not speak into it."""
    def ids(s, roles=None):
        try:
            return {str(m.get("workspace_id")) for m in mindex.list(s) if m.get("workspace_id")
                    and (roles is None or str(m.get("role") or "").strip().lower() in roles)}
        except Exception:  # noqa: BLE001 — an index that cannot answer shares nothing
            return set()
    return bool(ids(sender, WRITE_ROLES) & ids(target))


def cross_person_deliverable(store, sender: str, target: str, thread: str) -> bool:
    """MAY ``sender``'s agent start a turn in ``target``'s ``thread``? Only in a stored session
    (``s-…``) that no flow owns, where ``target`` takes part and ``sender`` takes part with write.
    Everything else of ``target``'s — their own threads by name, a routine's thread, a session the
    sender is not in or only reads — is theirs alone. Fails closed on any store error. Shared with
    the delivery side (`routers/chats.py`), so the address checked at send is the one honoured at
    delivery."""
    from control_plane import session_store as store_mod, session_sync  # noqa: PLC0415
    if store is None or not str(thread or "").startswith(session_sync.STORE_ID_PREFIX):
        return False
    try:
        row = store.get(thread)
        if row is None or store_mod.read_flow(row.get("flow")):
            return False
        return (store.role_of(row["id"], target) is not None
                and store.role_of(row["id"], sender) in SESSION_WRITE_ROLES)
    except Exception:  # noqa: BLE001 — a store that cannot answer delivers nothing across people
        return False


def _publish(source_event_id: str, refs: dict) -> tuple[int, dict]:
    """POST the fact to flows' intake and return its answer — unlike `publish.publish`, which
    swallows, because the sending agent is told whether its message was accepted."""
    base, key = publish_mod._flows_base(), publish_mod._flows_key()
    if not base or not key:
        return 501, {"refused": "flows:not_present",
                     "note": "this deployment runs no flows, so agents cannot message each other"}
    body = json.dumps({"event_type": publish_mod.EVENT_THREAD_MESSAGE, "source_event_id": source_event_id,
                       "refs": refs}).encode()
    req = urllib.request.Request(f"{base}/events", data=body, method="POST", headers={
        "content-type": "application/json", "X-Flows-Operator-Key": key,
        "X-Actor": f"agent:{refs.get('sender_uid')}"})
    try:
        with urllib.request.urlopen(req, timeout=publish_mod.TIMEOUT_S * 2) as r:  # noqa: S310
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return e.code, detail if isinstance(detail, dict) else {"detail": detail}
    except Exception:  # noqa: BLE001
        return 503, {"refused": "flows:unreachable", "note": "send it again in a moment"}


def build(**d) -> APIRouter:
    router = APIRouter()
    sess = d["sess"]
    mindex = d["mindex"]
    subject_of = d["subject_of"]
    store = d.get("session_store")

    @router.post("/api/agents/message", status_code=202)
    def message_agent(body: AgentMessageBody, request: Request):
        """Send a message to another agent: it arrives as a turn in that agent's chat thread,
        marked as from you, and its answer comes back later as a turn in your own thread — nothing
        waits for it. Address the agent by its chat thread (`thread`), say which thread you are
        writing from (`from_thread`, from your turn context), and name `person` only for a thread
        that belongs to somebody else — which works only for a conversation (`s-…`) you both take
        part in and you can write to, inside a workspace you both belong to where you are not a
        reader; their own threads and routines are never an address.
        Refusals are typed: `not_your_thread`, `no_such_thread`, and the policy and guard reasons
        flows answers with (`agent_messages_own`, `agent_messages_shared`,
        `agent_messages_from_routines`, `loop_max_hops`, `quota_messages_per_day`)."""
        sender = subject_of(request)
        mine = _thread_of(sess, sender, body.from_thread, store)
        if mine is None:
            raise HTTPException(status_code=403, detail={
                "refused": "not_your_thread", "from_thread": body.from_thread,
                "note": "from_thread must be one of your own chat threads"})
        target = str(body.person or sender).strip()
        if target != sender:
            # Decided before anything is looked up in the other person's threads, and one answer
            # for every way it fails.
            if not (_shared(mindex, sender, target)
                    and cross_person_deliverable(store, sender, target, body.thread)):
                raise HTTPException(status_code=403, detail=dict(CROSS_PERSON_REFUSAL))
        theirs = _thread_of(sess, target, body.thread, store)
        if theirs is None:
            if target != sender:
                raise HTTPException(status_code=403, detail=dict(CROSS_PERSON_REFUSAL))
            raise HTTPException(status_code=404, detail={
                "refused": "no_such_thread", "thread": body.thread})
        routine = mine.get("routine") or None
        chain: list = []
        try:
            stamp = json.loads(sess.meta(sender, body.from_thread).get("agent_chain") or "{}")
            if time.time() - float(stamp.get("at") or 0) < CHAIN_TTL_S:
                chain = [str(x) for x in stamp.get("chain") or []]
        except Exception:  # noqa: BLE001 — no chain is a new conversation
            chain = []
        # A worker with nobody in the loop is a routine for this rule unless the thread it names is
        # answering a delivered message (it then carries that conversation's chain on).
        unattended = ceiling.is_unwatched(request) and not chain
        message_id = uuid.uuid4().hex[:16]
        label = (routine or {}).get("flow") and f"routine {routine['flow']}" or (
            request.headers.get("x-user-email") or f"user {sender}")
        refs = {"uid": target, "thread": body.thread, "text": body.text,
                "message_id": message_id, "sender_uid": sender,
                "sender_thread": body.from_thread, "sender_label": str(label)[:120],
                "shared": target == sender or _shared(mindex, sender, target),
                # a ROUTINE is the sender when the thread is a routine's, or when the caller runs
                # with nobody in the loop and is not answering a delivered message; a turn running
                # unwatched because a message was delivered into an ordinary chat is not one
                "sender_routine": bool(routine) or unattended,
                "_origin": chain + [f"thread:{sender}/{body.from_thread}"]}
        code, answer = _publish(f"msg:{sender}:{message_id}", refs)
        if target != sender and code == 403 and (answer or {}).get("refused") == "agent_messages_shared":
            answer = dict(CROSS_PERSON_REFUSAL)
        if 200 <= code < 300:
            return {"sent": True, "message_id": message_id, "to": {"person": target,
                    "thread": body.thread}, "reply_comes_back_to": body.from_thread}
        raise HTTPException(status_code=code if code in (403, 429, 501, 503) else 502,
                            detail=answer)

    return router
