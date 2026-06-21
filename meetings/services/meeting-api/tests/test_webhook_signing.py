"""O-MTG-2 eval (signing) — HMAC sign/verify + webhook.v1 envelope/header conformance.

Asserts: a verifier that recomputes HMAC over `ts.payload` ACCEPTS a valid signature and
REJECTS a tampered one (tampered body, tampered timestamp, wrong secret, missing header);
the built envelope + headers conform to the sealed webhook.v1 schema.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from meeting_api.webhooks import (
    build_envelope,
    build_headers,
    sign_payload,
    verify_signature,
)

SECRET = "whsec_demo_secret"


# --- webhook.v1 schema (the seam) ------------------------------------------------------

def _webhook_schema() -> dict:
    rel = Path("meetings") / "contracts" / "webhook.v1" / "webhook.schema.json"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return json.loads((parent / rel).read_text())
    raise FileNotFoundError("webhook.v1 schema not found")


_SCHEMA = _webhook_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def _conforms(obj: dict, shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


# --- sign / verify ---------------------------------------------------------------------

def test_valid_signature_accepted():
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    body = json.dumps(env).encode()
    headers = build_headers(SECRET, body, timestamp="1771401720")
    assert verify_signature(body, headers, SECRET)


def test_tampered_body_rejected():
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    body = json.dumps(env).encode()
    headers = build_headers(SECRET, body, timestamp="1771401720")
    tampered = body + b" "  # one byte different
    assert not verify_signature(tampered, headers, SECRET)


def test_tampered_timestamp_rejected():
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    body = json.dumps(env).encode()
    headers = build_headers(SECRET, body, timestamp="1771401720")
    headers["X-Webhook-Timestamp"] = "1771401999"  # replay the sig under a new ts
    assert not verify_signature(body, headers, SECRET)


def test_wrong_secret_rejected():
    env = build_envelope("bot.failed", {"meeting": {"id": 2}})
    body = json.dumps(env).encode()
    headers = build_headers(SECRET, body, timestamp="1771401720")
    assert not verify_signature(body, headers, "the_wrong_secret")


def test_missing_signature_header_rejected():
    body = b'{"x":1}'
    assert not verify_signature(body, {"X-Webhook-Timestamp": "1"}, SECRET)
    assert not verify_signature(body, {}, SECRET)


def test_signature_is_ts_dot_payload():
    """The signed content is exactly `<ts>.` + body (the parent's wire scheme)."""
    body = b'{"hello":"world"}'
    ts = "1771401720"
    got = build_headers(SECRET, body, timestamp=ts)["X-Webhook-Signature"]
    assert got == sign_payload(body, SECRET, ts)
    assert got.startswith("sha256=")


def test_no_signature_headers_without_secret():
    body = b"{}"
    headers = build_headers(None, body)
    assert "X-Webhook-Signature" not in headers
    assert headers["Content-Type"] == "application/json"


# --- webhook.v1 conformance ------------------------------------------------------------

def test_built_envelope_conforms():
    env = build_envelope("meeting.completed", {"meeting": {"id": 1, "status": "completed"}})
    _conforms(env, "Envelope")


def test_built_headers_conform():
    body = json.dumps(build_envelope("bot.failed", {"meeting": {"id": 2}})).encode()
    headers = build_headers(SECRET, body, timestamp="1771401720")
    _conforms(headers, "SignatureHeaders")


@pytest.mark.parametrize(
    "name", ["Envelope.meeting-completed", "Envelope.bot-failed", "SignatureHeaders.signed"]
)
def test_webhook_goldens_conform(name):
    rel = Path("meetings") / "contracts" / "webhook.v1" / "golden" / f"{name}.json"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            golden = json.loads((parent / rel).read_text())
            _conforms(golden, name.split(".")[0])
            return
    pytest.fail(f"golden {name} not found")
