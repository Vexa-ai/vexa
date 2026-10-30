"""Settings → Models "Test" buttons — the on-demand credential tests (control_plane.config_test).

Grades the exact failure modes observed live on 2026-07-09: stale Keychain export (expired
subscription file), zero-balance external transcription token (402 per segment), rejected
token, unreachable backend, and the happy paths.
"""
import json

from control_plane import config_test as ct


# ── subscription file ─────────────────────────────────────────────────────────────────────────

def _write_creds(tmp_path, expires_ms):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"expiresAt": expires_ms}}))
    return str(p)


def test_subscription_missing_file(tmp_path):
    out = ct.test_subscription_credentials(str(tmp_path / "absent"))
    assert not out["ok"] and "HOST_CLAUDE_CREDENTIALS" in out["summary"]


def test_subscription_expired_carries_remedy(tmp_path):
    out = ct.test_subscription_credentials(_write_creds(tmp_path, 1_000_000), now=2_000.0)
    assert not out["ok"] and out.get("expired") is True
    assert ct.KEYCHAIN_REFRESH in out["summary"]  # the fix ships WITH the failure


def test_subscription_valid_reports_hours_left(tmp_path):
    out = ct.test_subscription_credentials(_write_creds(tmp_path, 10 * 3600 * 1000), now=0.0)
    assert out["ok"] and out["expires_in_hours"] == 10.0


def test_subscription_garbage_file(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text("not json")
    out = ct.test_subscription_credentials(str(p))
    assert not out["ok"] and ct.KEYCHAIN_REFRESH in out["summary"]


# ── custom endpoint ───────────────────────────────────────────────────────────────────────────

MSG_BODY = json.dumps({"type": "message", "content": [{"type": "text", "text": "pong"}]})
CHAT_BODY = json.dumps({"object": "chat.completion",
                        "choices": [{"message": {"content": "pong"}}]})


def _both_ok(url, payload, headers):
    return (200, MSG_BODY) if url.endswith("/v1/messages") else (200, CHAT_BODY)


def test_custom_endpoint_auth_failure():
    out = ct.test_custom_endpoint("https://gw.example", "bad-key",
                                  post=lambda u, p, h: (401, "{}"))
    assert not out["ok"] and "rejected" in out["summary"] and "401" in out["summary"]


def test_custom_endpoint_ok_probes_both_call_shapes():
    calls = []

    def post(url, payload, headers):
        calls.append((url, sorted(headers)))
        return _both_ok(url, payload, headers)

    out = ct.test_custom_endpoint("https://gw.example/v1/", "k", "m1", post=post)
    assert out["ok"]
    # Each shape is probed at the path its adapter really uses, with ONLY its own auth header.
    assert calls[0][0] == "https://gw.example/v1/v1/messages"
    assert "x-api-key" in calls[0][1] and "Authorization" not in calls[0][1]
    assert calls[1][0] == "https://gw.example/v1/chat/completions"
    assert "Authorization" in calls[1][1] and "x-api-key" not in calls[1][1]


def test_openai_only_gateway_is_not_green_1666():
    """The reported config: a gateway serving ONLY /chat/completions. The harness shape must fail
    loud — it used to pass because a 404 on /v1/messages silently fell back to the other dialect."""
    def post(url, payload, headers):
        return (404, "") if url.endswith("/v1/messages") else (200, CHAT_BODY)

    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert not out["ok"] and "no anthropic endpoint" in out["summary"].lower()
    assert out["shapes"]["completion"]["ok"] is True  # the OpenAI half genuinely works


def test_openai_body_at_the_messages_path_is_not_green_1666():
    """HTTP 200 carrying the OTHER dialect's body — exactly what the claude CLI reports as
    'empty or malformed response (HTTP 200)'. Status-code grading called this success."""
    out = ct.test_custom_endpoint("https://gw.example", "k",
                                  post=lambda u, p, h: (200, CHAT_BODY))
    assert not out["ok"]
    assert "OpenAI chat.completion body" in out["summary"]


def test_html_from_a_cdn_is_not_green_1666():
    out = ct.test_custom_endpoint("https://gw.example", "k",
                                  post=lambda u, p, h: (200, "<!DOCTYPE html><html>blocked"))
    assert not out["ok"] and "HTML page" in out["summary"]


def test_per_dialect_auth_and_endpoints_1666():
    """The reporter's gateway: both dialects served, different credential on each, and the
    Messages side on its own host. Configurable now — and the probe proves it end to end."""
    def post(url, payload, headers):
        # Exact URLs, so the fake also asserts each shape hit its own endpoint and path.
        if url == "https://messages.example/v1/messages":
            return (200, MSG_BODY) if headers.get("x-api-key") == "harness-key" else (401, "{}")
        assert url == "https://gw.example/v1/chat/completions"
        return (200, CHAT_BODY) if headers.get("Authorization") == "Bearer chat-key" else (401, "{}")

    out = ct.test_custom_endpoint("https://gw.example/v1", "chat-key", post=post,
                                  harness_base_url="https://messages.example",
                                  harness_api_key="harness-key")
    assert out["ok"], out["summary"]


def test_a_401_is_terminal_for_that_shape_not_retried():
    """No retry on a rejected credential: one request per call shape, full stop (#1666's ~70s
    'Working' loop is this failure re-sent every ~5s)."""
    calls = []

    def post(url, payload, headers):
        calls.append(url)
        return 401, '{"error": {"message": "Missing API key"}}'

    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert not out["ok"]
    assert calls == ["https://gw.example/v1/messages", "https://gw.example/chat/completions"]


def test_extra_headers_ride_both_call_shapes_1667():
    seen = []

    def post(url, payload, headers):
        seen.append(headers.get("x-provider-session"))
        return _both_ok(url, payload, headers)

    out = ct.test_custom_endpoint("https://gw.example", "k", post=post,
                                  headers="x-provider-session: abc123")
    assert out["ok"] and seen == ["abc123", "abc123"]
    assert "x-provider-session" in out["summary"]  # names WHICH headers were sent
    assert "abc123" not in out["summary"]          # never the value


def test_parse_extra_headers_forms():
    assert ct.parse_extra_headers("A: 1\r\nB: 2") == {"A": "1", "B": "2"}
    assert ct.parse_extra_headers({"A": "1"}) == {"A": "1"}
    assert ct.parse_extra_headers("garbage") == {}
    assert ct.parse_extra_headers("") == {}


def test_custom_endpoint_unreachable():
    def post(url, payload, headers):
        raise OSError("connection refused")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert not out["ok"] and "unreachable" in out["summary"]


def test_run_models_test_routes_custom_vs_subscription(tmp_path):
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "k"},
                             env={}, post=_both_ok)
    assert out["mode"] == "custom" and out["ok"]
    out = ct.run_models_test({}, env={}, creds_path=str(tmp_path / "absent"))
    assert out["mode"] == "subscription" and not out["ok"]
    # secrets never echo in provenance
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "SECRET",
                              "harness_api_key": "ALSO-SECRET",
                              "headers": "x-session: HEADER-SECRET"},
                             env={}, post=_both_ok)
    assert "api_key" not in out["config"] and "SECRET" not in json.dumps(out)
    assert "HEADER-SECRET" not in json.dumps(out)


# ── transcription backend ─────────────────────────────────────────────────────────────────────

def _balance(email, minutes):
    return 200, json.dumps({"email": email, "balance_minutes": minutes})


# The 2026-07-19 recurrence, as a permanent pair: two tokens, both reporting balance 0.0 —
# one exhausted (every request 402s), one billing-exempt (transcribes fine). NO balance
# threshold and NO account name can tell them apart; only the endpoint's answer to real audio
# can. Neither row names any specific account: identity must never be the oracle.

def test_transcription_exhausted_token_fails_loud_despite_valid_auth():
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "settings",
        get=lambda u, h: _balance("someone@gmail.com", 0.0),
        probe=lambda e, t: (402, '{"detail":"Insufficient balance"}'))
    assert not out["ok"] and "402" in out["summary"] and out["source"] == "settings"
    assert "NO transcript" in out["summary"], "the verdict must name the consequence"


def test_transcription_zero_balance_but_transcribing_token_is_green():
    """A billing-exempt account reports 0.0 minutes and transcribes perfectly — the round-trip
    must green it where a balance threshold would condemn it."""
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "env",
        get=lambda u, h: _balance("svc-account@example.com", 0.0),
        probe=lambda e, t: (200, '{"text":"probe"}'))
    assert out["ok"], "zero balance alone must never fail a token that transcribes"
    assert "svc-account@example.com" in out["summary"]


def test_transcription_funded_external_ok():
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "env",
        get=lambda u, h: _balance("someone@gmail.com", 42.5),
        probe=lambda e, t: (200, '{"text":"probe"}'))
    assert out["ok"] and "someone@gmail.com" in out["summary"]


def test_transcription_rejected_token():
    out = ct.run_transcription_test("https://x", "bad", "env", get=lambda u, h: (403, ""),
                                    probe=lambda e, t: (403, ""))
    assert not out["ok"] and "REJECTED" in out["summary"]


def test_transcription_backend_5xx_is_red():
    out = ct.run_transcription_test("https://t", "tok", "env", get=lambda u, h: (404, ""),
                                    probe=lambda e, t: (503, ""))
    assert not out["ok"] and "503" in out["summary"]


def test_transcription_strips_v1_path_for_balance_probe():
    seen = []
    def get(url, headers):
        seen.append(url)
        return _balance("someone@example.com", 5.0)
    ct.run_transcription_test("https://t.vexa.ai/v1/audio/transcriptions", "tok", "env", get=get,
                              probe=lambda e, t: (200, "{}"))
    assert seen == ["https://t.vexa.ai/balance"]


def test_transcription_no_backend_and_no_token():
    out = ct.run_transcription_test("", "", "env")
    assert not out["ok"] and "No transcription backend" in out["summary"]
    out = ct.run_transcription_test("https://t", "", "env")
    assert not out["ok"] and "NO token" in out["summary"]


def test_transcription_unreachable():
    def boom(endpoint, token):
        raise OSError("timeout")
    out = ct.run_transcription_test("https://t", "tok", "env", get=lambda u, h: (404, ""),
                                    probe=boom)
    assert not out["ok"] and "unreachable" in out["summary"]


def test_transcription_balance_failure_never_blocks_the_verdict():
    """/balance is a courtesy account lookup, never the oracle — a gateway without it (or one
    that errors) must not stop the round-trip from grading the backend."""
    def boom(url, headers):
        raise OSError("no /balance here")
    out = ct.run_transcription_test("https://t", "tok", "env", get=boom,
                                    probe=lambda e, t: (200, "{}"))
    assert out["ok"]


# ── C2 (#511): a non-Vexa backend is graded by the endpoint BOTS use, never "reachable" ──────────
# No /balance means "not a Vexa gateway", which is not a verdict on the operator's question. The
# round-trip runs the bot's own first-chunk request (same probe body as the boot preflight), so
# the wizard's green means "a bot will transcribe" on ANY OpenAI-compatible endpoint.

_NO_BALANCE = lambda u, h: (404, "")  # noqa: E731 — the non-Vexa signature, reused by every row


def test_transcription_openai_wrong_key_is_red():
    """A1/A2: a rejected key on an OpenAI-compatible endpoint is RED at the click — it used to
    return green-unverified and fail mid-meeting."""
    out = ct.run_transcription_test("https://api.openai.com", "sk-wrong", "settings",
                                    get=_NO_BALANCE, probe=lambda e, t: (401, ""))
    assert not out["ok"], "a rejected key must never test green"
    assert "REJECTED" in out["summary"] and out["status"] == 401
    assert out.get("unverified") is None, "there is no longer an unverified green"


def test_transcription_openai_good_key_is_green_and_names_the_endpoint():
    out = ct.run_transcription_test("https://api.openai.com", "sk-good", "settings",
                                    get=_NO_BALANCE, probe=lambda e, t: (200, '{"text":""}'))
    assert out["ok"]
    assert "/v1/audio/transcriptions" in out["summary"]


def test_transcription_openai_wrong_url_is_red():
    out = ct.run_transcription_test("https://api.openai.com/wrong", "sk-good", "env",
                                    get=_NO_BALANCE, probe=lambda e, t: (404, ""))
    assert not out["ok"] and "URL shape" in out["summary"]


def test_transcription_probe_hits_the_transcriptions_path_once():
    """C4 (A5) at this consumer: base URL and full-path URL must hit the SAME endpoint once with
    the configured token (the /balance lookup's X-API-Key is Vexa-gateway-only)."""
    for configured in ("https://api.openai.com", "https://api.openai.com/v1/audio/transcriptions"):
        seen = []
        def probe(endpoint, token):
            seen.append((endpoint, token))
            return 200, "{}"
        out = ct.run_transcription_test(configured, "sk-good", "env", get=_NO_BALANCE, probe=probe)
        assert out["ok"], f"{configured} must verify green"
        assert seen == [("https://api.openai.com/v1/audio/transcriptions", "sk-good")], (
            f"{configured} → {seen}"
        )


def test_completion_probe_follows_the_configured_provider():
    """An env-configured deployment may run the `anthropic` completion adapter (same endpoint,
    same dialect as the harness) or `claude-cli` (no HTTP completion at all). Probing
    /chat/completions regardless would fail a deployment that works."""
    calls = []

    def post(url, payload, headers):
        calls.append(url)
        return _both_ok(url, payload, headers)

    env = {"ANTHROPIC_BASE_URL": "https://gw.example", "ANTHROPIC_AUTH_TOKEN": "k"}
    out = ct.run_models_test({}, env={**env, "VEXA_LLM_PROVIDER": "anthropic"}, post=post)
    assert out["ok"] and calls == ["https://gw.example/v1/messages"]  # one shape, one request

    calls.clear()
    out = ct.run_models_test({}, env={**env, "VEXA_LLM_PROVIDER": "claude-cli"}, post=post)
    assert out["ok"] and calls == ["https://gw.example/v1/messages"]  # beats ride the CLI
    assert "completion" not in out["shapes"]

    calls.clear()
    out = ct.run_models_test({}, env=env, post=post)  # default provider = openai-compat
    assert out["ok"] and calls == ["https://gw.example/v1/messages",
                                   "https://gw.example/chat/completions"]
