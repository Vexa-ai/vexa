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


def test_transcription_unreachable_and_non_gateway():
    def boom(url, headers):
        raise OSError("timeout")
    out = ct.run_transcription_test("https://t", "tok", "env", get=boom)
    assert not out["ok"] and "unreachable" in out["summary"]
    out = ct.run_transcription_test("https://t", "tok", "env", get=lambda u, h: (404, ""))
    assert out["ok"] and out.get("unverified") is True  # reachable, token unproven — says so
