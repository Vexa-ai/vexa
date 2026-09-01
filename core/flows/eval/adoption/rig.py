"""Thin MCP + mailpit client for the adoption sampler.

One door in (the control MCP over HTTP, with a per-identity token) and one door out (mailpit's
REST API, read-only — messages are NEVER deleted; another session shares this mailbox).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

MCP_URL = os.environ.get("SIM_MCP_URL", "http://localhost:18310/mcp")
MAILPIT = os.environ.get("SIM_MAILPIT", "http://127.0.0.1:8025")


class MCP:
    """One MCP session. `token` is the identity — never uid 57's."""

    def __init__(self, token: str = "", url: str = MCP_URL):
        self.token, self.url, self.sid, self.n = token, url, None, 0

    def _headers(self):
        h = {"content-type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    def _post(self, body, timeout=120):
        self.n += 1
        req = urllib.request.Request(self.url, method="POST",
                                     data=json.dumps(body).encode(), headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self.sid = sid
                return r.status, self._parse(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:800]

    @staticmethod
    def _parse(raw):
        if "data: " in raw:
            raw = [l[6:] for l in raw.splitlines() if l.startswith("data: ")][-1]
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return raw[:800]

    def init(self):
        st, r = self._post({"jsonrpc": "2.0", "id": self.n + 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "adoption-sim", "version": "0"}}})
        try:
            urllib.request.urlopen(urllib.request.Request(
                self.url, method="POST", headers=self._headers(),
                data=json.dumps({"jsonrpc": "2.0",
                                 "method": "notifications/initialized"}).encode()),
                timeout=20).read()
        except Exception:  # noqa: BLE001
            pass
        return st, r

    def call(self, name, timeout=180, **args):
        st, r = self._post({"jsonrpc": "2.0", "id": self.n + 1, "method": "tools/call",
                            "params": {"name": name, "arguments": args}}, timeout=timeout)
        if isinstance(r, dict) and "result" in r:
            content = r["result"].get("content") or []
            txt = "".join(c.get("text", "") for c in content if c.get("type") == "text")
            try:
                return json.loads(txt)
            except Exception:  # noqa: BLE001
                return {"_text": txt}
        return {"_status": st, "_raw": r}


# ── mailpit (read-only) ────────────────────────────────────────────────────────────────────
def _get(path):
    with urllib.request.urlopen(MAILPIT + path, timeout=30) as r:
        return json.loads(r.read().decode())


def messages_for(addr: str, limit: int = 200) -> list[dict]:
    """Every message addressed to `addr`, newest first. Search, never list-and-filter:
    the mailbox is shared with another session and holds thousands of unrelated messages."""
    q = urllib.parse.quote(f'to:"{addr}"')
    try:
        d = _get(f"/api/v1/search?query={q}&limit={limit}")
    except Exception:  # noqa: BLE001
        return []
    return d.get("messages", [])


def body_of(msg_id: str) -> dict:
    return _get(f"/api/v1/message/{msg_id}")


def full_touch(m: dict) -> dict:
    """A message rendered the way the human meets it: subject + the text they actually read."""
    d = body_of(m["ID"])
    text = (d.get("Text") or "").strip()
    if not text:
        text = re.sub(r"<[^>]+>", " ", d.get("HTML") or "")
    links = re.findall(r"https?://[^\s<>\"\)]+", text)
    return {"id": m["ID"], "subject": m.get("Subject", ""),
            "from": (m.get("From") or {}).get("Address", ""),
            "to": [t.get("Address") for t in (m.get("To") or [])],
            "created": m.get("Created"),
            "text": text[:6000], "links": links,
            "has_attachment": bool(m.get("Attachments"))}


import urllib.parse  # noqa: E402  (used by messages_for)


def wait_for(addr: str, predicate, timeout_s: int = 300, poll: int = 6):
    """Poll mailpit until a message matching `predicate` reaches `addr`, or give up."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        for m in messages_for(addr):
            if predicate(m):
                return m
        time.sleep(poll)
    return None
