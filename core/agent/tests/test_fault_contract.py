"""unit.v1's `Fault` holds every fault the agent domain can send (P18, P4/P8 — arch pass 6, S65/S66).

The fault wire — `{source, kind, status, detail, remedy, …}` on a failed `done`, on the chat stream's
`error`, on agent-api's 502/503 body — crossed two process boundaries with no contract, and its
vocabulary was spelled by hand in `llm/faults.py`, `shared/runtime_fault.py`, `worker/tool_access.py`
and the terminal's `surfaces/faults.ts`. Now `core/agent/contracts/unit.v1` names it — beside the
unit's input stream, since these are its output stream's last frames — and each side imports a
vocabulary generated from it (`gen-faults.mjs`; `validate.mjs --check` fails on drift).

Pinned here, against the schema read by path:
  * the two generated Python copies, and every emitter's constants, ARE the contract's vocabulary;
  * every fault a Python emitter can produce — every runtime status, every provider status, label and
    phrase, tool access, the claude-code refusal, a failed Codex turn — conforms to `Fault`, and every
    kind the contract gives the runtime and the model provider is one an emitter actually produces;
  * the frames that carry one (`done`, `error`, the refusal body) conform to theirs.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from control_plane import unit_faults
from llm import codex, claude_code
from llm import fault_wire as llm_wire
from llm import faults
from shared import fault_wire as shared_wire
from shared import runtime_fault
from worker import tool_access

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "unit.v1"
SCHEMA = json.loads((CONTRACT / "unit.schema.json").read_text())
SOURCES = SCHEMA["$defs"]["FaultSource"]["enum"]


def _kinds(source: str) -> list[str]:
    """A source's kinds, from the `$def` named after it (`model-provider` → `ModelProviderFaultKind`)."""
    name = "".join(w.capitalize() for w in source.replace("_", "-").split("-")) + "FaultKind"
    return SCHEMA["$defs"][name]["enum"]


def _validator(shape: str) -> jsonschema.Draft202012Validator:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    return jsonschema.Draft202012Validator({"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"}, registry=registry)


def _conforms(shape: str, body: dict) -> None:
    errors = sorted(_validator(shape).iter_errors(body), key=str)
    assert not errors, f"{shape} does not conform: {[e.message for e in errors]}\n{json.dumps(body, indent=1)}"


# ── one vocabulary: the contract's ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wire", [llm_wire, shared_wire], ids=["llm", "shared"])
def test_each_generated_vocabulary_is_the_contracts(wire):
    assert list(wire.SOURCES) == SOURCES
    assert {s: list(k) for s, k in wire.KINDS.items()} == {s: _kinds(s) for s in SOURCES}


def test_every_emitter_spells_its_source_and_kinds_from_the_contract():
    assert runtime_fault.SOURCE == "runtime" and list(runtime_fault.KINDS) == _kinds("runtime")
    assert faults.SOURCE == "model-provider" and list(faults.KINDS) == _kinds("model-provider")
    assert tool_access.SOURCE == "vexa-tools" and [tool_access.ACCESS_EXPIRED] == _kinds("vexa-tools")
    refusal = claude_code.credential_conflict_fault()
    unconfined = tool_access.unconfined_fault("/workspaces/u/desk/notes.md", "/workspaces/u", "EPERM")
    assert refusal["source"] == unconfined["source"] == tool_access.WORKER_SOURCE == "agent-worker"
    assert sorted([refusal["kind"], unconfined["kind"]]) == sorted(_kinds("agent-worker"))


# ── every fault a Python emitter can produce conforms, and covers its source's vocabulary ─────

def _http(status: int, body: object = None) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError("http://runtime:8090/workloads", status, "err", {}, io.BytesIO(raw))


def _runtime_faults() -> list[dict]:
    out = []
    for op in ("spawn", "status", "list"):
        for status in (400, 401, 403, 404, 409, 418, 422, 429, 500, 501, 502, 503, 504):
            out.append(runtime_fault.from_http_error(op, _http(status, {"detail": "pods \"x\" already exists"})))
        out.append(runtime_fault.from_transport_error(op, urllib.error.URLError(ConnectionRefusedError(111, "refused"))))
        out.append(runtime_fault.from_transport_error(op, urllib.error.URLError(socket.timeout("timed out"))))
        out.append(runtime_fault.from_bad_body(op, ValueError("Expecting value")))
        out.append(runtime_fault.translate(op, RuntimeError("anything else")))
    return [f.as_dict() for f in out]


def _provider_faults() -> list[dict]:
    out = [faults.classify(status=s, provider="openrouter.ai", model="m")
           for s in (400, 401, 402, 403, 404, 408, 413, 422, 429, 500, 502, 503, 529)]
    out += [faults.classify(sdk_error=e, provider="api.anthropic.com", model="m")
            for e in ("billing_error", "authentication_failed", "rate_limit", "server_error", "invalid_request")]
    out += [faults.classify(text=t, provider="api.anthropic.com", model="")
            for t in ("API Error: 402 {\"error\":{\"message\":\"Insufficient credits\"}}",
                      "Your credit balance is too low", "rate limit exceeded", "Invalid API key",
                      "Overloaded", "HTTP 404 model not found", "Not logged in · Please run /login")]
    out += [faults.classify(text="ConnectTimeout", transport=True, provider="api.anthropic.com")]
    out += [faults.classify(kind=k, text="x", provider="chatgpt.com") for k in faults.KINDS]
    return [f.as_dict() for f in out if f is not None]


def _codex_faults() -> list[dict]:
    errors = [{"message": "m", "codexErrorInfo": label} for label in codex._CODEX_ERROR_KIND]
    errors += [{"message": "m", "codexErrorInfo": {name: {"httpStatusCode": code}}}
               for name in codex._CODEX_TRANSPORT for code in (None, 401, 429, 503)]
    out = [codex._turn_fault(e, "gpt-5-codex") for e in errors]
    return [f.as_dict() for f in out if f is not None]


EMITTED = {
    "runtime": _runtime_faults,
    "model-provider": lambda: _provider_faults() + _codex_faults(),
    "vexa-tools": lambda: [tool_access.fault(1791282600)],
    "agent-worker": lambda: [claude_code.credential_conflict_fault(),
                             tool_access.unconfined_fault("/workspaces/u/desk/notes.md", "/workspaces/u",
                                                          "Operation not permitted")],
}


@pytest.mark.parametrize("source", sorted(EMITTED))
def test_every_fault_a_python_emitter_produces_conforms(source):
    produced = EMITTED[source]()
    assert produced
    for f in produced:
        assert f["source"] == source
        _conforms("Fault", f)
    # …and the emitter produces every kind the contract gives its source: no kind is sealed that
    # nothing sends, so a renamed kind cannot leave a dead entry in the vocabulary behind it.
    assert sorted({f["kind"] for f in produced}) == sorted(_kinds(source))


def test_a_recorded_fault_conforms_with_its_time():
    fault = runtime_fault.from_http_error("spawn", _http(502)).as_dict()
    _conforms("Fault", {**fault, "at": 1791282600.125})


# ── the frames that carry one ──────────────────────────────────────────────────────────────────

def test_agent_apis_refusal_body_and_error_frame_conform():
    for status in (401, 404, 422, 429, 500, 502, 503):
        fault = runtime_fault.from_http_error("spawn", _http(status))
        _conforms("DispatchRefusal", unit_faults.answer(fault))
        _conforms("ErrorFrame", unit_faults.error_event({**fault.as_dict(), "at": 1.5}))


def test_the_worker_done_frames_conform():
    refused = claude_code.credential_conflict_fault()
    _conforms("DoneFrame", {"type": "done", "reply": "r", "sessionId": None, "ok": False, "fault": refused})
    events = list(tool_access.watch(
        [{"type": "tool-call", "tool": "mcp__vexa__search", "callId": "c"},
         {"type": "tool-result", "callId": "c", "ok": False},
         {"type": "done", "reply": "partial", "sessionId": "s", "ok": True}],
        1791282600, now=lambda: 1791282700))
    _conforms("DoneFrame", events[-1])
    failed = codex._failed_done("", "m", "thr", {"message": "m", "codexErrorInfo": "rateLimitExceeded"}, "")
    _conforms("DoneFrame", failed)


@pytest.mark.parametrize("path", sorted(p for p in (CONTRACT / "golden").glob("*.json")
                                         if p.name.split(".")[0] in {"Fault", "DoneFrame", "ErrorFrame",
                                                                     "DispatchRefusal"}),
                         ids=lambda p: p.name)
def test_every_golden_conforms_in_python_too(path):
    """validate.mjs proves the goldens under ajv; this proves them under the validator agent-api
    ships, so the two engines cannot read the contract differently unnoticed."""
    _conforms(path.name.split(".")[0], json.loads(path.read_text()))
