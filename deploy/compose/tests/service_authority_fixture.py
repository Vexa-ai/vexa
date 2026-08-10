"""Bounded service-authority.v1 fixture for the Compose acceptance oracle.

This is a generic contract peer, not a hosted billing policy or Stripe double.
It validates the exact-body HMAC, allows admission, and denies the first active
continuation so the stock meeting-api can prove its durable one-minute stop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


HOST = "0.0.0.0"
PORT = 9880
SECRET = os.environ.get(
    "AUTHORITY_SECRET",
    "compose-service-authority-fixture-secret",
)
MAX_REQUEST_AGE_SECONDS = 30

_lock = threading.Lock()
_observations: dict[str, Any] = {
    "admit": 0,
    "continue": 0,
    "signature_failures": 0,
    "request_ids": [],
    "service_identities": [],
    "decision_ids": [],
    "request_keys": [],
}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "service-authority-fixture"

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep the fixture quiet: request bodies carry service identities."""

    def _reply(self, status: int, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP hook
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
            return
        if self.path == "/observations":
            with _lock:
                snapshot = json.loads(json.dumps(_observations))
            self._reply(200, snapshot)
            return
        self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP hook
        if self.path != "/v1/service-authority":
            self._reply(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            timestamp = self.headers.get("X-Webhook-Timestamp", "")
            supplied = self.headers.get("X-Webhook-Signature", "")
            bearer = self.headers.get("Authorization", "")
            expected = hmac.new(
                SECRET.encode("utf-8"),
                timestamp.encode("ascii") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            fresh = abs(time.time() - int(timestamp)) <= MAX_REQUEST_AGE_SECONDS
            authenticated = (
                bearer == f"Bearer {SECRET}"
                and fresh
                and hmac.compare_digest(supplied, f"sha256={expected}")
            )
        except (TypeError, ValueError, UnicodeError):
            authenticated = False
        if not authenticated:
            with _lock:
                _observations["signature_failures"] += 1
            self._reply(401, {"error": "invalid_signature"})
            return

        try:
            request = json.loads(body)
            action = request["action"]
            request_id = request["request_id"]
            service_identity = request["service_identity"]
            if action not in ("admit", "continue"):
                raise ValueError("unsupported action")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid_request"})
            return

        allow = action == "admit"
        decision_id = (
            "compose-authority:"
            + hashlib.sha256(
                f"{action}:{request_id}".encode("utf-8"),
            ).hexdigest()
        )
        with _lock:
            _observations[action] += 1
            _observations["request_ids"].append(request_id)
            _observations["service_identities"].append(service_identity)
            _observations["decision_ids"].append(decision_id)
            _observations["request_keys"].append(sorted(request))

        response = {
            "authority_version": "service-authority.v1",
            "decision_id": decision_id,
            "request_id": request_id,
            "service_identity": service_identity,
            "allow": allow,
            "reason": (
                "compose_fixture_allow"
                if allow
                else "compose_fixture_limit"
            ),
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        if not allow:
            response["stop_scope"] = "billable_service"
        self._reply(200, response)


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
