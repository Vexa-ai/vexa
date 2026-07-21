#!/usr/bin/env python3
"""stub_llm.py — the e2e rig's LOCAL OpenAI-compatible backend + Minutes-Hub callback sink.

One process, three faces, zero dependencies beyond the stdlib (it must run under ANY python
in the lite container):

  * ``POST /v1/chat/completions``      — the summarizer's backend (collector/summarizer.py posts
                                         here when SUMMARY_SERVICE_URL points at this stub); returns
                                         a canned chat completion so ``data.summary`` gets written
                                         without a network or a model.
  * ``POST /v1/audio/transcriptions``  — the STT face. The e2e bot never posts audio; this exists so
                                         TRANSCRIPTION_SERVICE_URL can point at a REAL listener (the
                                         spawn config gate requires one) and so a future audio leg
                                         has somewhere honest to land.
  * ``POST /api/minutes/callback/v1``  — the Hub face. The engine's outbox drain
                                         (zaki_control/callbacks.py drain_once) requires the sealed
                                         ACK ``{api_version, event_id, status: accepted}`` — a bare
                                         200 is treated as NON-delivery and retried forever, which
                                         would wedge every capture short of settlement. Events are
                                         recorded and served back on ``GET /_events`` so the driver
                                         can assert the terminal usage settlement the Hub would see.

  * ``GET /health``                    — readiness for the driver's wait loop.

Run: ``python3 stub_llm.py`` (PORT env, default 8099). The e2e driver execs it INSIDE the lite
container so every URL is plain loopback — no extra image, no network topology to get wrong.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_EVENTS: list[dict] = []
_LOCK = threading.Lock()

SUMMARY_TEXT = (
    "## TL;DR\nSynthetic e2e meeting: the rig discussed the capture pipeline and closed it.\n"
    "## Key points\n- The scripted bot emitted speaker-attributed segments.\n"
    "- The collector persisted them and the archive indexed them.\n"
    "## Decisions\n- Ship the e2e rig as a PR gate.\n"
    "## Action items\n- rig — keep the lane green.\n"
    "## Open questions\n- None.\n"
)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict | list) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path == "/health":
            self._json(200, {"status": "ok", "service": "zaki-e2e-stub"})
        elif self.path == "/_events":
            with _LOCK:
                self._json(200, list(_EVENTS))
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        body = self._read_body()
        if self.path == "/v1/chat/completions":
            self._json(200, {
                "id": "cmpl-zaki-e2e", "object": "chat.completion",
                "model": body.get("model") or "zaki-e2e-stub",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": SUMMARY_TEXT},
                }],
            })
        elif self.path == "/v1/audio/transcriptions":
            self._json(200, {"text": "synthetic e2e speech", "language": "en", "segments": []})
        elif self.path == "/api/minutes/callback/v1":
            with _LOCK:
                _EVENTS.append(body)
            # The sealed CallbackAck — drain_once only marks delivered on exactly this shape.
            self._json(200, {
                "api_version": "zaki-control.v1",
                "event_id": body.get("event_id") or "unknown-event",
                "status": "accepted",
            })
        else:
            self._json(404, {"error": "unknown path"})

    def log_message(self, fmt: str, *args) -> None:  # quiet: the driver reads /_events, not logs
        pass


def main() -> None:
    port = int(os.environ.get("PORT") or 8099)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
