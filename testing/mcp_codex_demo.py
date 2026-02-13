"""
Minimal MCP client demo for local dev.

This talks to the Vexa MCP endpoint through the local API gateway:
  http://localhost:8056/mcp

Auth:
  Authorization: Bearer <VEXA_API_KEY>

Usage:
  python testing/mcp_codex_demo.py

Env overrides:
  VEXA_MCP_URL=http://localhost:8056/mcp
  VEXA_API_KEY_FILE=.local/mcp_test_api_key.txt
  VEXA_API_KEY=... (if no file)
  VEXA_DEMO_MEETING_URL=... (optional)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


def _load_api_key() -> str:
    api_key = os.environ.get("VEXA_API_KEY")
    if api_key:
        return api_key.strip()

    key_file = os.environ.get("VEXA_API_KEY_FILE", ".local/mcp_test_api_key.txt")
    p = Path(key_file)
    if p.exists():
        return p.read_text().strip()

    raise RuntimeError(
        "Missing API key. Set VEXA_API_KEY or create .local/mcp_test_api_key.txt."
    )


def _mcp_handshake(mcp_url: str, api_key: str) -> tuple[requests.Session, dict[str, str]]:
    s = requests.Session()
    r = s.get(mcp_url)
    if r.status_code != 200:
        raise RuntimeError(f"GET /mcp unexpected status {r.status_code}: {r.text[:200]}")
    sid = r.headers.get("mcp-session-id")
    if not sid:
        raise RuntimeError("Missing mcp-session-id header from GET /mcp")

    headers = {
        "content-type": "application/json",
        "mcp-session-id": sid,
        "authorization": f"Bearer {api_key}",
    }
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "codex-demo", "version": "0.0.0"},
        },
    }
    rr = s.post(mcp_url, headers=headers, data=json.dumps(init))
    if rr.status_code != 200:
        raise RuntimeError(f"initialize failed {rr.status_code}: {rr.text[:200]}")
    j = rr.json()
    if "error" in j:
        raise RuntimeError(f"initialize jsonrpc error: {j}")

    return s, headers


def _rpc(session: requests.Session, mcp_url: str, headers: dict[str, str], payload: dict) -> dict:
    r = session.post(mcp_url, headers=headers, data=json.dumps(payload))
    if r.status_code != 200:
        raise RuntimeError(f"jsonrpc http {r.status_code}: {r.text[:300]}")
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"jsonrpc error: {j}")
    return j["result"]


def main() -> int:
    mcp_url = os.environ.get("VEXA_MCP_URL", "http://localhost:8056/mcp")
    api_key = _load_api_key()

    session, headers = _mcp_handshake(mcp_url, api_key)

    tools = _rpc(session, mcp_url, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = sorted([t["name"] for t in tools.get("tools", [])])

    print("tools:", ", ".join(names))

    prompts = _rpc(session, mcp_url, headers, {"jsonrpc": "2.0", "id": 9, "method": "prompts/list", "params": {}})
    prompt_names = []
    try:
        prompt_names = sorted([p["name"] for p in prompts.get("prompts", [])])
    except Exception:
        # Different servers may wrap results differently; keep demo resilient.
        prompt_names = []
    print("prompts:", ", ".join(prompt_names) if prompt_names else "(none)")

    meeting_url = os.environ.get(
        "VEXA_DEMO_MEETING_URL",
        "https://teams.live.com/meet/9361792952021?p=IXw5JhZRdoBvKnUXPy",
    )
    parsed = _rpc(
        session,
        mcp_url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "parse_meeting_link", "arguments": {"meeting_url": meeting_url}},
        },
    )
    print("parse_meeting_link:", parsed.get("content", [{}])[0].get("text", "")[:400])

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
