"""Minimal streamable-HTTP MCP client for the rig. One interface: rule 1 of the audit."""
from __future__ import annotations
import json, os, urllib.request

RIG_URL = os.environ.get("VEXA_RIG_URL", "https://rig.dev.vexa.ai/mcp")


class Rig:
    def __init__(self, token: str, url: str = RIG_URL):
        self.token, self.url, self.sid, self._id = token, url, None, 0

    def _post(self, payload, notify=False, timeout=600):
        self._id += 1
        if not notify:
            payload["id"] = self._id
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", "Bearer " + self.token)
        if self.sid:
            req.add_header("Mcp-Session-Id", self.sid)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.sid = sid
            raw = r.read().decode()
        if not raw.strip():
            return None
        for line in raw.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return json.loads(raw)

    def connect(self):
        info = self._post({"jsonrpc": "2.0", "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "dna-replay", "version": "0"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notify=True)
        return info

    def call(self, name, **args):
        r = self._post({"jsonrpc": "2.0", "method": "tools/call",
                        "params": {"name": name, "arguments": args}})
        if r is None:
            return None
        if "error" in r:
            return {"_error": r["error"]}
        txt = "".join(x.get("text", "") for x in r.get("result", {}).get("content", [])
                      if x.get("type") == "text")
        try:
            return json.loads(txt)
        except Exception:
            return txt

    def tools(self):
        r = self._post({"jsonrpc": "2.0", "method": "tools/list", "params": {}})
        return [t["name"] for t in r["result"]["tools"]]
