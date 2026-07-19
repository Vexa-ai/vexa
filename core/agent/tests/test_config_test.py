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

def test_custom_endpoint_auth_failure():
    out = ct.test_custom_endpoint("https://gw.example", "bad-key",
                                  post=lambda u, p, h: (401, "{}"))
    assert not out["ok"] and "Authentication FAILED" in out["summary"]


def test_custom_endpoint_ok_anthropic_dialect():
    calls = []
    def post(url, payload, headers):
        calls.append(url)
        return 200, "{}"
    out = ct.test_custom_endpoint("https://gw.example/", "k", "m1", post=post)
    assert out["ok"] and calls == ["https://gw.example/v1/messages"]


def test_custom_endpoint_falls_back_to_openai_dialect():
    def post(url, payload, headers):
        return (404, "") if url.endswith("/v1/messages") else (200, "{}")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert out["ok"]


def test_custom_endpoint_unreachable():
    def post(url, payload, headers):
        raise OSError("connection refused")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert not out["ok"] and "unreachable" in out["summary"]


def test_run_models_test_routes_custom_vs_subscription(tmp_path):
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "k"},
                             env={}, post=lambda u, p, h: (200, "{}"))
    assert out["mode"] == "custom" and out["ok"]
    out = ct.run_models_test({}, env={}, creds_path=str(tmp_path / "absent"))
    assert out["mode"] == "subscription" and not out["ok"]
    # secrets never echo in provenance
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "SECRET"},
                             env={}, post=lambda u, p, h: (200, "{}"))
    assert "api_key" not in out["config"] and "SECRET" not in json.dumps(out)


# ── transcription backend ─────────────────────────────────────────────────────────────────────

def _balance(email, minutes):
    return 200, json.dumps({"email": email, "balance_minutes": minutes})


def test_transcription_internal_token_billing_exempt():
    out = ct.run_transcription_test("https://transcription.vexa.ai", "tok", "env",
                                    get=lambda u, h: _balance("internal@vexa.ai", 0.0))
    assert out["ok"] and "billing-exempt" in out["summary"]


def test_transcription_zero_balance_external_fails_loud():
    out = ct.run_transcription_test("https://transcription.vexa.ai", "tok", "settings",
                                    get=lambda u, h: _balance("someone@gmail.com", 0.0))
    assert not out["ok"] and "402" in out["summary"] and out["source"] == "settings"


def test_transcription_funded_external_ok():
    out = ct.run_transcription_test("https://transcription.vexa.ai", "tok", "env",
                                    get=lambda u, h: _balance("someone@gmail.com", 42.5))
    assert out["ok"] and "42.5" in out["summary"]


def test_transcription_rejected_token():
    out = ct.run_transcription_test("https://x", "bad", "env", get=lambda u, h: (403, ""))
    assert not out["ok"] and "REJECTED" in out["summary"]


def test_transcription_strips_v1_path_for_balance_probe():
    seen = []
    def get(url, headers):
        seen.append(url)
        return _balance("internal@vexa.ai", 0.0)
    ct.run_transcription_test("https://t.vexa.ai/v1/audio/transcriptions", "tok", "env", get=get)
    assert seen == ["https://t.vexa.ai/balance"]


def test_transcription_no_backend_and_no_token():
    out = ct.run_transcription_test("", "", "env")
    assert not out["ok"] and "No transcription backend" in out["summary"]
    out = ct.run_transcription_test("https://t", "", "env")
    assert not out["ok"] and "NO token" in out["summary"]


def test_transcription_unreachable():
    def boom(url, headers):
        raise OSError("timeout")
    out = ct.run_transcription_test("https://t", "tok", "env", get=boom)
    assert not out["ok"] and "unreachable" in out["summary"]


# ── C2 (#511): a non-Vexa backend is graded by the endpoint BOTS use, never "reachable" ──────────
# No /balance means "not a Vexa gateway", which is not a verdict on the operator's question. The
# fallback runs the bot's own first-chunk request, so the wizard's green means "a bot will
# transcribe" on ANY OpenAI-compatible endpoint.

_NO_BALANCE = lambda u, h: (404, "")  # noqa: E731 — the non-Vexa signature, reused by every row


def test_transcription_openai_wrong_key_is_red():
    """A1/A2: a rejected key on an OpenAI-compatible endpoint is RED at the click — it used to
    return green-unverified and fail mid-meeting."""
    out = ct.run_transcription_test("https://api.openai.com", "sk-wrong", "settings",
                                    get=_NO_BALANCE, post=lambda u, p, h: (401, ""))
    assert not out["ok"], "a rejected key must never test green"
    assert "REJECTED" in out["summary"] and out["status"] == 401
    assert out.get("unverified") is None, "there is no longer an unverified green"


def test_transcription_openai_good_key_is_green_and_names_the_endpoint():
    out = ct.run_transcription_test("https://api.openai.com", "sk-good", "settings",
                                    get=_NO_BALANCE, post=lambda u, p, h: (400, ""))
    assert out["ok"], "auth accepted (400 = our empty body rejected, not our key)"
    assert "/v1/audio/transcriptions" in out["summary"]


def test_transcription_openai_wrong_url_is_red():
    out = ct.run_transcription_test("https://api.openai.com/wrong", "sk-good", "env",
                                    get=_NO_BALANCE, post=lambda u, p, h: (404, ""))
    assert not out["ok"] and "URL shape" in out["summary"]


def test_transcription_fallback_sends_bearer_to_the_transcriptions_path():
    """C4 (A5) at this consumer: base URL and full-path URL must hit the SAME endpoint once, with
    the Bearer header the bot sends (the /balance probe's X-API-Key is Vexa-gateway-only)."""
    for configured in ("https://api.openai.com", "https://api.openai.com/v1/audio/transcriptions"):
        seen = []
        def post(url, payload, headers):
            seen.append((url, headers.get("Authorization")))
            return 400, ""
        out = ct.run_transcription_test(configured, "sk-good", "env", get=_NO_BALANCE, post=post)
        assert out["ok"], f"{configured} must verify green"
        assert seen == [("https://api.openai.com/v1/audio/transcriptions", "Bearer sk-good")], (
            f"{configured} → {seen}"
        )
