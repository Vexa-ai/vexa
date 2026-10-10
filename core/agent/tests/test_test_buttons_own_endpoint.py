"""THE TEST BUTTONS NEVER SEND A DEPLOYMENT CREDENTIAL TO A PERSON'S OWN ENDPOINT.

Settings → Models → Test and Settings → Transcription → Test probe the backend a turn or a bot would
use, from agent-api — which holds the deployment's model and transcription credentials. Each must
send exactly the credential that backend would carry, and nothing of the deployment's:

1. models: a person's own `base_url` with no key is probed with no key (openai-agent), or not at all
   (claude-code, which refuses a keyless own endpoint) — never with agent-api's ANTHROPIC_* or
   VEXA_LLM_* values. Asserted on the wire (`urllib.request.urlopen`, and the person's-endpoint
   probe `config_test._subject_post`), through the route.
2. transcription: the pair a bot spawned now would use (`bot_spawn`'s rule): a person's URL with
   its own token, empty meaning none; with no URL of theirs, the deployment's URL with the
   deployment's token — a person's token never goes to the deployment's URL either.

L2: the real routes over a loopback stub of admin-api's bot-context; no outside call.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from control_plane import config_test
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

DEPLOYMENT = {"ANTHROPIC_AUTH_TOKEN": "deployment-anthropic-token",
              "ANTHROPIC_API_KEY": "deployment-anthropic-key",
              "VEXA_LLM_API_KEY": "deployment-llm-key",
              "CLAUDE_CODE_OAUTH_TOKEN": "deployment-oauth",
              "TRANSCRIPTION_SERVICE_URL": "https://stt.deployment.example",
              "TRANSCRIPTION_SERVICE_TOKEN": "deployment-stt-token"}
OWN_MODEL_URL = "https://openrouter.ai/api/v1"          # in the default allow-list
OWN_STT_URL = "https://stt.person.example"


class _Runtime:
    def spawn(self, *a, **k):
        return "w"


class _Identity:
    def mint(self, *a, **k):
        return "tok"


class _ModelConfig:
    def __init__(self, cfg):
        self.cfg = cfg

    def resolve(self, subject):
        return dict(self.cfg)


def _client(tmp_path, *, model_config=None, admin_url=""):
    settings = load_settings(workspaces_dir=str(tmp_path), admin_api_url=admin_url,
                             internal_api_secret="internal-test-secret")
    d = Dispatcher(settings, _Runtime(), _Identity(), model_config=model_config)
    return TestClient(create_app(d))


@pytest.fixture
def deployment(monkeypatch):
    for k, v in DEPLOYMENT.items():
        monkeypatch.setenv(k, v)
    for k in ("ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL", "VEXA_MODEL_BASE_URL_ALLOW", "VEXA_RUNNER"):
        monkeypatch.delenv(k, raising=False)


# ── 1. models ───────────────────────────────────────────────────────────────────────────────────

def _wire(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "headers": dict(req.header_items()),
                     "body": (req.data or b"").decode()})
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    # A person's own endpoint is probed through `config_test._subject_post` (httpx, no redirects,
    # the URL guard for a wildcard-admitted host); the wire is recorded there for that route.
    def subject_post(url, payload, headers):
        sent.append({"url": url, "headers": dict(headers), "body": json.dumps(payload)})
        return 200, "{}"

    monkeypatch.setattr(config_test, "_subject_post", subject_post)
    return sent


def _no_deployment_value(requests: list[dict]) -> None:
    text = json.dumps(requests)
    for value in DEPLOYMENT.values():
        if not value.startswith("https://"):
            assert value not in text, f"{value} was sent"


def test_a_keyless_own_model_endpoint_is_probed_with_no_key(tmp_path, deployment, monkeypatch):
    sent = _wire(monkeypatch)
    cfg = {"mode": "custom", "base_url": OWN_MODEL_URL, "api_key": "", "model": "m",
           "runner": "openai-agent"}
    out = _client(tmp_path, model_config=_ModelConfig(cfg)).get(
        "/api/models/test", headers={"X-User-Id": "u_1"}).json()
    assert sent and all(r["url"].startswith(OWN_MODEL_URL.rsplit("/v1", 1)[0]) for r in sent)
    _no_deployment_value(sent)
    assert out["route"] == "subject"


def test_a_keyless_own_model_endpoint_on_claude_code_is_not_probed(tmp_path, deployment, monkeypatch):
    sent = _wire(monkeypatch)
    cfg = {"mode": "custom", "base_url": OWN_MODEL_URL, "api_key": "", "model": "m",
           "runner": "claude-code"}
    out = _client(tmp_path, model_config=_ModelConfig(cfg)).get(
        "/api/models/test", headers={"X-User-Id": "u_1"}).json()
    assert sent == [] and out["ok"] is False


# ── 2. transcription ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def bot_context():
    """A loopback admin-api answering bot-context with whatever the test sets."""
    state: dict = {"transcription": None}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"transcription": state["transcription"]} if state["transcription"]
                              else {}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def _probed(tmp_path, monkeypatch, bot_context) -> tuple[str, str, str]:
    seen: list[tuple] = []
    monkeypatch.setattr(config_test, "run_transcription_test",
                        lambda url, token, source, **k: seen.append(("vexa", url, token)) or {"ok": True})
    monkeypatch.setattr(config_test, "run_customer_transcription_test",
                        lambda url, token, source, **k: seen.append(("customer", url, token)) or {"ok": True})
    _client(tmp_path, admin_url=bot_context["url"]).get("/api/transcription/test",
                                                         headers={"X-User-Id": "u_1"})
    assert len(seen) == 1
    return seen[0]


def test_a_persons_stt_url_without_a_token_is_probed_with_no_token(tmp_path, deployment, monkeypatch,
                                                                    bot_context):
    bot_context["transcription"] = {"url": OWN_STT_URL, "provider": "customer"}
    assert _probed(tmp_path, monkeypatch, bot_context) == ("customer", OWN_STT_URL, "")


def test_a_persons_stt_token_never_goes_to_the_deployments_url(tmp_path, deployment, monkeypatch,
                                                                bot_context):
    bot_context["transcription"] = {"token": "person-stt-token", "provider": "vexa"}
    assert _probed(tmp_path, monkeypatch, bot_context) == (
        "vexa", DEPLOYMENT["TRANSCRIPTION_SERVICE_URL"], DEPLOYMENT["TRANSCRIPTION_SERVICE_TOKEN"])


def test_a_persons_stt_url_and_token_go_together(tmp_path, deployment, monkeypatch, bot_context):
    bot_context["transcription"] = {"url": OWN_STT_URL, "token": "person-stt-token",
                                    "provider": "customer"}
    assert _probed(tmp_path, monkeypatch, bot_context) == ("customer", OWN_STT_URL, "person-stt-token")


def test_with_nothing_configured_the_deployment_pair_is_probed(tmp_path, deployment, monkeypatch,
                                                                bot_context):
    assert _probed(tmp_path, monkeypatch, bot_context) == (
        "vexa", DEPLOYMENT["TRANSCRIPTION_SERVICE_URL"], DEPLOYMENT["TRANSCRIPTION_SERVICE_TOKEN"])
