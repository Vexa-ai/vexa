"""Test helpers: an agent's ```ask card in a chat's transcript, and the person's press of it (S5, 7g).

The press goes through the real routes (`/api/asks/open`, `/api/asks/{pending}/answer`) from the
person's own session, so what a test hands an act as `pressed` is exactly what the server recorded."""
from __future__ import annotations

import json

RECORDS = ".claude/sessions/conversation.turns.jsonl"


def offer(store, session_id: str, *cards: dict) -> None:
    """The chat's transcript holds the agent's turns offering ``cards``, in order."""
    recs = [{"user_text": "go on", "ts": float(i + 1), "reply": "```ask\n" + json.dumps(c) + "\n```\n"}
            for i, c in enumerate(cards)]
    store.save_bundle(session_id, store.head(session_id),
                      {RECORDS: "\n".join(json.dumps(r) for r in recs).encode()})


def press(client, session_id: str, ask_id: str, label: str, headers: dict) -> str:
    """The person presses ``label`` on card ``ask_id``; the pending id the act is handed."""
    item = client.post("/api/asks/open", json={"session": session_id, "ask_id": ask_id}, headers=headers).json()
    body = {"nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": label}}
    r = client.post(f"/api/asks/{item['pending_id']}/answer", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return item["pending_id"]


def card(ask_id: str, question: str, primary: str = "Yes", secondary: str = "Not now") -> dict:
    return {"id": ask_id, "question": question, "primary": {"label": primary}, "secondary": {"label": secondary}}
