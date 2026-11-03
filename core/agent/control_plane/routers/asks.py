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

The turn reads a mark the way a reader would — any case, spacing or width — and every one the
server cannot vouch for reaches it in its typed form (``bind_answer_marks``). The act a card asks for
is done only on the press itself, never on its text: ``pressed_card`` reads the record behind a
``#<pending>``, and the session store spends it once (``SessionStore.spend``).

The nonce is stored only as its sha256; the item is the session store's proposal row, so its one
writer is agent-api and its decision is single-use by the store's own ``already_decided`` rule.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
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
#: An answer line as the turn reads it: `[answer <card id> #<pending item>] …`, matched on the
#: folded text (`_folded`) in any case and spacing, so every spelling a reader takes for a mark is one.
_ANSWER_MARK = re.compile(r"\[\s*answer\s+([A-Za-z0-9._:-]{1,80})\s*#\s*([^\]\s]{1,128})\s*\]", re.I)
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


def _folded(text: str) -> "tuple[str, list[int]]":
    """``text`` as a reader takes it — each character NFKC-folded (fullwidth brackets and signs are
    their ASCII selves), invisible format characters dropped — with, for every folded character, the
    index of the character of ``text`` it came from."""
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":
            continue
        for f in unicodedata.normalize("NFKC", ch):
            chars.append(f)
            where.append(i)
    return "".join(chars), where


def carries_answer_mark(text: str) -> bool:
    """Does ``text`` hold anything a reader takes for a card answer with its `#<pending>` part?"""
    return bool(text) and _ANSWER_MARK.search(_folded(text)[0]) is not None


def _vouched(store, subject: str, session_id: Optional[str], ask_id: str, pending: str) -> bool:
    try:
        p = store.proposal(pending) if store is not None else None
        item = json.loads(p["body"]) if p else {}
    except Exception:  # noqa: BLE001 — an unreadable record vouches for nothing
        p, item = None, {}
    return (p is not None and p.get("kind") == "ask" and p.get("status") == "accepted"
            and str(p.get("owner")) == str(subject) and item.get("ask_id") == ask_id
            and session_id is not None and str(p.get("session_id")) == str(session_id))


def bind_answer_marks(store, subject: str, session_id: Optional[str], text: str) -> str:
    """``text`` with every `[answer <id> #<pending>]` mark that the server cannot vouch for reduced to
    its unbound form `[answer <id>]`, which the agent treats as typed (confirm first).

    A mark is vouched for only when the pending item exists, is an answered card of THIS chat, was
    answered by THIS speaker, and is the card the mark names; a vouched mark reaches the turn in the
    one form the server writes. The text of a message is anyone's to type; the pending item is the
    server's record of a press."""
    if not text:
        return text
    folded, where = _folded(text)
    out, end = [], len(text)
    for m in reversed(list(_ANSWER_MARK.finditer(folded))):
        ask_id, pending = m.group(1), m.group(2)
        lo, hi = where[m.start()], where[m.end() - 1] + 1
        mark = (f"[answer {ask_id} #{pending}]" if _vouched(store, subject, session_id, ask_id, pending)
                else f"[answer {ask_id}]")
        out.append(text[hi:end])
        out.append(mark)
        end = lo
    out.append(text[:end])
    return "".join(reversed(out))


def pressed_card(store, pending_id: str, *, subject: str, session_id: str) -> Optional[dict]:
    """The card behind a press, when ``pending_id`` is THIS speaker's answered, unspent card of THIS
    chat and the card the transcript holds is still the one they answered: ``{"spec", "label"}``,
    ``label`` the offered choice they pressed ("" for an answer in their own words). Else None."""
    try:
        p = store.proposal(pending_id) if store is not None and pending_id else None
        item = json.loads(p["body"]) if p else {}
        if not (p is not None and p.get("kind") == "ask" and p.get("status") == "accepted"
                and str(p.get("owner")) == str(subject) and str(p.get("session_id")) == str(session_id)):
            return None
        spec = find_ask(_turns_of(store.load_bundle(session_id).get("files") or {}), item.get("ask_id") or "")
    except Exception:  # noqa: BLE001 — a record or transcript that cannot be read vouches for nothing
        return None
    if spec is None or not hmac.compare_digest(canonical_hash(spec), str(item.get("content_hash") or "")):
        return None
    return {"spec": spec, "label": str(item.get("label") or "")}


def _label_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get("label") or "").strip()[:LABEL_MAX]
    return str(item or "").strip()[:LABEL_MAX]


def _shown(v, cap: int = 300) -> str:
    return v.strip()[:cap] if isinstance(v, str) else ""


def rendered_face(spec: dict) -> str:
    """The card's text exactly as the person's app draws it (the terminal's ``parseInlineCard``, with
    its caps): the question, the summary lines, the chip and button labels and the answer field's
    placeholder — one line each. The ``id``, a button's ``reply`` and any other field are never on
    screen, so they are not on the face."""
    lines = [_shown(spec.get("question"))]
    for x in (spec.get("summary") or [])[:8] if isinstance(spec.get("summary"), list) else []:
        if isinstance(x, list):
            lines += [_shown(x[0] if len(x) > 0 else "", 60), _shown(x[1] if len(x) > 1 else "", 200)]
        else:
            lines.append(_shown(x))
    chips = spec.get("chips") if isinstance(spec.get("chips"), list) else []
    lines += [_label_of(c) for c in chips[:6]]
    lines += [_label_of(spec.get("primary")), _label_of(spec.get("secondary"))]
    if isinstance(spec.get("free"), str):
        lines.append(_shown(spec["free"], 80))
    return "\n".join(x for x in lines if x)


def press_names(card: dict, names) -> bool:
    """Does the card the person pressed name one of ``names`` (an address, an account id, a name)
    ON ITS FACE — what the person read before pressing — as a whole token? A name inside a longer
    word or address (``142`` in ``1420``, ``bob@x.test`` in ``evilbob@x.test``) is not that name."""
    face = unicodedata.normalize("NFKC", rendered_face(card["spec"])).casefold()
    for n in names:
        n = unicodedata.normalize("NFKC", n or "").strip().casefold()
        if len(n) >= 3 and re.search(r"(?<![\w@.+-])" + re.escape(n) + r"(?![\w@+-]|\.\w)", face):
            return True
    return False


def pressed_choice(card: dict) -> str:
    """Which of the card's buttons the press was: ``primary``, ``secondary``, or "" (a chip, or an
    answer in the person's own words)."""
    label = card["label"]
    if label and label == _label_of(card["spec"].get("primary")):
        return "primary"
    if label and label == _label_of(card["spec"].get("secondary")):
        return "secondary"
    return ""


#: The refusal every act a card suggests answers when it arrives without the person's press.
PRESS_REQUIRED = "press_required"


def chat_of_unit(store, subject: str, unit: str) -> Optional[str]:
    """The chat row a worker's unit runs in (``agent-<subject>-chat-<thread>[-g…][-auto]``), or None
    when the unit is not one of ``subject``'s chats (a room, a routine, no unit)."""
    prefix = f"agent-{subject}-chat-"
    if store is None or not str(unit or "").startswith(prefix):
        return None
    thread = re.sub(r"(-g[0-9A-Za-z]+)?(-read)?(-auto|-signal)?$", "", unit[len(prefix):])
    try:
        from control_plane import session_sync  # noqa: PLC0415
        row = session_sync.resolve(store, str(subject), thread, create=False)
    except Exception:  # noqa: BLE001 — an unreadable index binds no chat
        return None
    return str(row["id"]) if row else None


def aliases(people, principal: str, *also: str) -> set:
    """Every way a card's face may name ``principal``: the id, the name and address the people
    directory holds for them (``people``: ``people_directory.mentionable`` rows), and ``also``."""
    out = {str(principal or "")} | {str(a or "") for a in also}
    for p in people or []:
        if str(p.get("subject")) == str(principal):
            out |= {str(p.get("name") or ""), str(p.get("email") or "")}
    return {a for a in out if a.strip()}


def require_press(store, request, pending: Optional[str], *, subject: str, targets: list,
                  act: str, session_id: Optional[str] = None,
                  choices: tuple = ("primary",)) -> None:
    """AN ACT A CARD SUGGESTS HAPPENS ONLY ON THE PERSON'S PRESS (D13, UI 7g, security gate M1).

    ``pending`` is the ``#<pending>`` of the speaker's answered card; it must be their own, unspent,
    of THIS chat (``session_id``, else the chat the caller's unit runs in, else the card's own chat),
    pressed on one of ``choices``, and its face must name every one of ``targets`` (each a set of
    aliases: one of them on the face is that target named). It is spent here: one press, one act.
    Raises the one 403 ``press_required`` refusal otherwise. The caller decides who needs a press;
    the person's own session never does."""
    s = store
    sid = session_id
    if sid is None:
        from control_plane import identity_token  # noqa: PLC0415
        sid = chat_of_unit(s, subject, identity_token.session_of(request.headers))
    if sid is None and s is not None and pending:
        try:
            p = s.proposal(pending)
            sid = str(p["session_id"]) if p else None
        except Exception:  # noqa: BLE001
            sid = None
    card = pressed_card(s, pending or "", subject=str(subject), session_id=sid) if sid else None
    choice = pressed_choice(card) if card is not None else ""
    named = card is not None and all(press_names(card, t) for t in targets if t)
    if card is None or not named or (choice or "chip") not in choices or not s.spend(pending, str(subject)):
        raise HTTPException(status_code=403, detail={
            "refused": PRESS_REQUIRED,
            "instruction": (f"{act} needs the person's press: offer a ```ask card whose question names "
                            "who it reaches, and call this again with `pressed` set to the #<pending> of "
                            "their pressed answer. A typed line, another card, or a press already used "
                            "does nothing.")})


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
        if not s.can_write(row["id"], subject):
            # a card is addressed to the chat's people who may act in it, never to a viewer
            raise HTTPException(status_code=403, detail={
                "refused": "not_addressed_to_you", "note": "only a member of this chat answers its cards"})
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
        done = next((p for p in items if p["status"] in ("accepted", "spent") and p["_body"].get("content_hash") == digest), None)
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
        if not s.can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail={
                "refused": "not_addressed_to_you", "note": "only a member of this chat answers its cards"})
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
            s.decide(pending_id, subject, accept=True,
                     body=json.dumps({**item, "answer": answer, "label": label}))
        except Exception as exc:  # noqa: BLE001 — a second decision races here: the store refuses it
            raise HTTPException(status_code=409, detail="already_answered") from exc
        return {"status": "answered", "pending_id": pending_id,
                "message": f"[answer {item['ask_id']} #{pending_id}] {answer}"}

    return router
