"""P18 at the model-provider edge — a provider that refuses a turn ends it with a TYPED fault.

The next failure a person hits on the 0.13.2 demo stack is OpenRouter answering 402, out of credit.
Untyped, that is "Model inference failed: 402 from …: {json}" on one harness and, on the other, a
synthetic `API Error: 402 {…}` printed into the chat as if the agent had said it — or nothing at all
when the CLI dies before its stream starts. Either way the person cannot tell an unpaid account from
a broken agent: the STT 402 of ADR-0010, one hop along.

Pinned here:
  * `llm.faults.classify` per kind — unpaid 402 · unauthorized 401/403 · rate_limited 429 ·
    unavailable 5xx/timeout · refused other 4xx — with the provider host, the status, a safe detail
    and the remedy;
  * both harnesses put it on the turn's `done` as `fault` (claude-code from the CLI's stream, its
    synthetic message or its plain-text death; openai-agent from the HTTP status);
  * the worker ends the turn with it — `done{fault}` then `turn-complete` on the out-stream — and
    does not "heal" it as a stale resume (which would drop the chat's session and ask twice).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from llm import faults
from llm.claude_code import ClaudeCodeHarness, parse_stream_json
from llm.openai_agent import OpenAIAgentHarness
from worker import engine
from worker.worker import serve

OPENROUTER_402 = {"error": {"message": "Insufficient credits. Add more using "
                                      "https://openrouter.ai/settings/credits", "code": 402}}


# ── the classifier, one kind at a time ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, kind", [
    (402, "unpaid"), (401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"),
    (500, "unavailable"), (502, "unavailable"), (503, "unavailable"), (529, "unavailable"),
    (400, "refused"), (404, "refused"), (422, "refused"),
])
def test_each_status_is_one_kind(status, kind):
    f = faults.classify(status=status, text="", provider="openrouter.ai", model="m")
    assert (f.source, f.kind, f.status, f.provider, f.model) == ("model-provider", kind, status,
                                                                  "openrouter.ai", "m")
    assert f.remedy


def test_unpaid_names_the_provider_and_how_to_pay_it():
    f = faults.classify(status=402, text=json.dumps(OPENROUTER_402), provider="openrouter.ai")
    assert f.kind == "unpaid"
    assert f.detail.startswith("Insufficient credits")
    assert "Add credits at openrouter.ai" in f.remedy
    assert "out of credits (402)" in f.sentence()
    assert set(f.as_dict()) == {"source", "kind", "provider", "model", "status", "detail", "remedy"}


@pytest.mark.parametrize("status, said", [
    (400, '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too '
          'low to access the Anthropic API."}}'),
    (429, '{"error":{"message":"You exceeded your current quota, please check your plan and billing '
          'details.","type":"insufficient_quota"}}'),
])
def test_an_empty_balance_is_unpaid_whatever_status_the_provider_chose(status, said):
    f = faults.classify(status=status, text=said, provider="api.anthropic.com")
    assert (f.kind, f.status) == ("unpaid", status)


def test_a_timeout_with_no_status_is_unavailable():
    f = faults.classify(text="ConnectTimeout", transport=True, provider="openrouter.ai")
    assert f.kind == "unavailable" and f.status is None


def test_a_status_printed_as_prose_is_read():
    f = faults.classify(text='API Error: 402 ' + json.dumps(OPENROUTER_402), provider="openrouter.ai")
    assert (f.kind, f.status) == ("unpaid", 402)
    assert "API Error" not in f.detail and "{" not in f.detail


def test_the_detail_never_carries_a_credential():
    f = faults.classify(status=401, text="invalid api key sk-or-v1-abcdef0123456789abcdef", provider="h")
    assert "sk-or-v1-abcdef" not in json.dumps(f.as_dict())


def test_text_that_is_no_provider_failure_is_not_one():
    assert faults.classify(text="context window exceeded") is None


# ── claude-code: the CLI's stream ──────────────────────────────────────────────────────────────

def _cli_402_stream() -> list[str]:
    """What `claude -p --output-format stream-json` prints when OpenRouter answers 402."""
    err = "API Error: 402 " + json.dumps(OPENROUTER_402)
    return [json.dumps(x) for x in (
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "anthropic/claude-sonnet-4"},
        {"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant",
                                          "content": [{"type": "text", "text": err}]}},
        {"type": "result", "subtype": "success", "is_error": True, "result": err, "session_id": "s1"},
    )]


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")


def test_claude_code_ends_a_402_turn_with_a_typed_unpaid_fault(openrouter):
    evs = list(parse_stream_json(_cli_402_stream()))
    assert not [e for e in evs if e["type"] == "message-delta"]     # no raw `API Error` bubble
    (done,) = evs
    assert done["ok"] is False and done["sessionId"] == "s1"
    assert done["fault"] == {
        "source": "model-provider", "kind": "unpaid", "provider": "openrouter.ai",
        "model": "anthropic/claude-sonnet-4", "status": 402,
        "detail": done["fault"]["detail"], "remedy": done["fault"]["remedy"]}
    assert done["fault"]["detail"].startswith("Insufficient credits")
    assert "Add credits at openrouter.ai" in done["fault"]["remedy"]
    assert "out of credit" in done["reply"]


def test_claude_code_reads_the_cli_s_own_error_label(openrouter):
    lines = [json.dumps({"type": "assistant", "error": "billing_error",
                         "message": {"content": [{"type": "text", "text": "Credit balance is too low"}]}}),
             json.dumps({"type": "result", "is_error": True, "result": "Credit balance is too low"})]
    (done,) = parse_stream_json(lines)
    assert done["fault"]["kind"] == "unpaid"


def test_claude_code_types_a_402_the_cli_printed_as_plain_text_and_died(openrouter):
    """stderr is merged into stdout and used to be skipped as malformed JSON — the turn then simply
    stopped, with nothing anywhere saying why."""
    lines = ["Error: API Error: 402 " + json.dumps(OPENROUTER_402)]
    (done,) = parse_stream_json(lines)
    assert done["ok"] is False and done["fault"]["kind"] == "unpaid"
    assert done["fault"]["provider"] == "openrouter.ai"


def test_claude_code_auth_keeps_its_rewrite_and_gains_a_type(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    lines = [json.dumps({"type": "result", "subtype": "error", "is_error": True,
                         "result": "Not logged in · Please run /login", "session_id": "s3"})]
    (done,) = parse_stream_json(lines)
    assert done["fault"]["kind"] == "unauthorized" and done["fault"]["provider"] == "api.anthropic.com"
    assert "/login" not in json.dumps(done["fault"])


@pytest.mark.parametrize("text, kind", [
    ("API Error: 429 rate limit exceeded", "rate_limited"),
    ("API Error: 529 Overloaded", "unavailable"),
    ("API Error: Request timed out.", "unavailable"),
    ('API Error: 401 {"error":{"message":"User not found.","code":401}}', "unauthorized"),
    ('API Error: 400 {"error":{"message":"invalid model id"}}', "refused"),
])
def test_claude_code_types_each_kind(openrouter, text, kind):
    lines = [json.dumps({"type": "result", "is_error": True, "result": text, "session_id": "s"})]
    (done,) = parse_stream_json(lines)
    assert done["fault"]["kind"] == kind


# ── openai-agent: the HTTP status ──────────────────────────────────────────────────────────────

def _openai(handler) -> OpenAIAgentHarness:
    return OpenAIAgentHarness(transport=httpx.MockTransport(handler),
                              base_url="https://openrouter.ai/api/v1", model="qwen/qwen3")


@pytest.mark.parametrize("stream", ["1", "0"])
def test_openai_agent_ends_a_402_turn_with_a_typed_unpaid_fault(tmp_path, monkeypatch, stream):
    monkeypatch.setenv("VEXA_AGENT_STREAM", stream)
    h = _openai(lambda request: httpx.Response(402, json=OPENROUTER_402))
    h.prepare(tmp_path)
    done = list(h.run_turn(Path(tmp_path), "hi"))[-1]
    assert done["type"] == "done" and done["ok"] is False
    f = done["fault"]
    assert (f["source"], f["kind"], f["provider"], f["model"], f["status"]) == (
        "model-provider", "unpaid", "openrouter.ai", "qwen/qwen3", 402)
    assert "out of credit" in done["reply"] and "{" not in done["reply"]


@pytest.mark.parametrize("status, kind", [(401, "unauthorized"), (429, "rate_limited"),
                                          (503, "unavailable"), (400, "refused")])
def test_openai_agent_types_each_kind(tmp_path, status, kind):
    h = _openai(lambda request: httpx.Response(status, text="no"))
    h.prepare(tmp_path)
    assert list(h.run_turn(Path(tmp_path), "hi"))[-1]["fault"]["kind"] == kind


def test_openai_agent_types_a_provider_that_never_answers(tmp_path):
    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    h = _openai(boom)
    h.prepare(tmp_path)
    f = list(h.run_turn(Path(tmp_path), "hi"))[-1]["fault"]
    assert f["kind"] == "unavailable" and f["status"] is None


def test_openai_agent_types_an_error_frame_inside_a_200(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "1")
    body = "data: " + json.dumps({"error": {"message": "Insufficient credits", "code": 402}}) + "\n\n"
    h = _openai(lambda request: httpx.Response(200, text=body,
                                               headers={"content-type": "text/event-stream"}))
    h.prepare(tmp_path)
    assert list(h.run_turn(Path(tmp_path), "hi"))[-1]["fault"]["kind"] == "unpaid"


# ── the worker ends the turn with it ───────────────────────────────────────────────────────────

class _Stream:
    def __init__(self):
        self.out = []

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        return []

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


def test_the_worker_ends_a_402_turn_with_the_unpaid_fault_and_completes_it(tmp_path, openrouter):
    """A provider 402 must END the turn, typed — not hang, not vanish. The real claude-code adapter
    over the CLI's own 402 output, through the real serve loop."""
    harness = ClaudeCodeHarness(exec_fn=lambda argv, cwd: iter(_cli_402_stream()))
    s = _Stream()
    serve(s, out_topic="unit:u:out", in_topic="unit:u:in",
          turn=lambda prompt: harness.run_turn(tmp_path, prompt),
          start={"entrypoint": {"inline": "hi"}}, idle_ms=10)
    evs = s.events()
    assert [e["type"] for e in evs] == ["turn-accepted", "done", "turn-complete"]
    assert evs[1]["fault"]["kind"] == "unpaid" and evs[1]["fault"]["status"] == 402
    assert evs[2]["turn_id"] == evs[1]["turn_id"] == "t0"


def test_a_provider_fault_is_not_healed_as_a_stale_resume(tmp_path, monkeypatch):
    """The engine re-runs a turn whose FIRST event is `done.ok=False` without its session, on the
    theory that the harness refused the resume. A 402 refuses whatever the session: re-running it
    asks the provider twice and throws the chat's resume pointer away."""
    calls = []

    def fake_run(work, prompt, harness, **kw):
        calls.append(kw.get("session"))
        yield {"type": "done", "ok": False, "reply": "out of credit", "sessionId": None,
               "fault": {"source": "model-provider", "kind": "unpaid", "status": 402}}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts",
                        lambda: [{"slug": "desk-1", "path": str(tmp_path), "write": True, "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: "")

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    sess_file = engine._session_file(engine._continuity_root(tmp_path), engine.DEFAULT_CHAT_SESSION)
    sess_file.parent.mkdir(parents=True, exist_ok=True)
    sess_file.write_text("s-old")
    evs = list(engine.run_turn_over_workspace(tmp_path, "hi", harness=H(), commit=False))
    assert calls == ["s-old"]                                  # asked once, on its own session
    assert sess_file.read_text() == "s-old"                    # the chat's memory survived
    assert evs[-1]["fault"]["kind"] == "unpaid"



def test_a_claude_code_turn_with_no_base_url_names_anthropic_not_unknown(monkeypatch):
    """The subscription route (and a catalog route to Anthropic) stamps ANTHROPIC_BASE_URL empty:
    the CLI's own default endpoint is where the turn went, so a fault names it."""
    from llm.errors import provider_host
    from llm.faults import classify

    for value in (None, ""):
        if value is None:
            monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_BASE_URL", value)
        assert provider_host() == "api.anthropic.com"
        assert classify(status=402, text="credit balance is too low").provider == "api.anthropic.com"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
    assert provider_host() == "openrouter.ai"
    assert provider_host("") == "unknown"          # an explicit empty endpoint names nothing
