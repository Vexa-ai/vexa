"""The languages a caller's bots can transcribe — ``GET /transcription/languages``
(transcription-language.v1 ``LanguageCatalog``), meeting-api's half.

  * THE READING — every ``ModelLookup`` and ``LanguageCatalog`` golden reads here exactly as the
    contract's validate.mjs reads it, from the same ``catalog.json``;
  * THE BACKEND — the list is for the backend a spawn for this caller would use (Settings, else the
    deployment's), only a deployment-owned backend is asked for its model, and a customer's endpoint
    is never called;
  * THE PROBE — ``GET {url}/health`` → ``model``, cached per URL.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import language_catalog as lc
from meeting_api.bot_spawn import service as spawn_service
from meeting_api.bot_spawn import transcription_language as tlang

USER = 7
ENV_URL = "http://transcription:8000"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    for key in (tlang.ENV_LANGUAGE, tlang.ENV_ALLOWED, "TRANSCRIPTION_MODEL",
                "TRANSCRIPTION_SERVICE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", ENV_URL)


# ── the reading: the contract's goldens ────────────────────────────────────────────────────────────

def _golden_dir() -> Path:
    rel = Path("meetings") / "contracts" / "transcription-language.v1" / "golden"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent / rel
    raise FileNotFoundError(str(rel))


def _goldens(prefix: str):
    return sorted(p for p in _golden_dir().glob(f"{prefix}.*.json"))


@pytest.mark.parametrize("path", _goldens("ModelLookup"), ids=lambda p: p.name)
def test_a_model_id_reads_to_the_list_the_contract_pins(path):
    golden = json.loads(path.read_text())
    listed = lc.languages_for(golden["model"])
    codes = None if listed is None else [l["code"] for l in listed]
    assert (None if codes is None else len(codes)) == golden["count"]
    for code in golden.get("has", []):
        assert code in codes
    for code in golden.get("lacks", []):
        assert codes is None or code not in codes


def _default(doc: dict):
    """The deployment tier that would report this default — through the reading meeting-api boots
    with, so a default it refuses raises here."""
    d = doc["default"]
    return tlang.deployment_tier({tlang.ENV_LANGUAGE: d["language"] or "",
                                  tlang.ENV_ALLOWED: ",".join(d["allowed_languages"])})


@pytest.mark.parametrize("path", _goldens("LanguageCatalog"), ids=lambda p: p.name)
def test_every_catalog_golden_is_the_answer_meeting_api_gives(path):
    golden = json.loads(path.read_text())
    provider = golden["model"] if golden["source"] == "provider" else None
    configured = golden["model"] if golden["source"] == "configured" else None
    assert lc.catalog(provider, configured, _default(golden)) == golden


@pytest.mark.parametrize("path", [p for p in _goldens("Refused")
                                  if json.loads(p.read_text())["shape"] == "LanguageCatalog"],
                         ids=lambda p: p.name)
def test_meeting_api_never_gives_a_refused_catalog(path):
    value = json.loads(path.read_text())["value"]
    provider = value["model"] if value["source"] == "provider" else None
    configured = value["model"] if value["source"] == "configured" else None
    try:
        default = _default(value)
    except tlang.LanguageRefused:
        return  # a deployment default meeting-api refuses at boot is never reported
    assert lc.catalog(provider, configured, default) != value


def test_a_model_the_catalog_does_not_list_falls_to_the_next_source():
    answer = lc.catalog("gpt-4o-transcribe", "medium.en", None)
    assert (answer["source"], answer["model"], [l["code"] for l in answer["languages"]]) == (
        "configured", "medium.en", ["en"])
    answer = lc.catalog("gpt-4o-transcribe", None, None)
    assert (answer["source"], answer["model"], len(answer["languages"])) == (
        "assumed", "gpt-4o-transcribe", 100)


# ── the backend: GET /transcription/languages ──────────────────────────────────────────────────────

class Probe:
    def __init__(self, model=None):
        self.model = model
        self.asked: list[tuple[str, object]] = []

    async def model_of(self, url, token):
        self.asked.append((url, token))
        return self.model


def _client(monkeypatch, probe, context=None):
    async def fetch(_user_id):
        return context or {}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)
    return TestClient(create_app(language_probe=probe))


def _get(client, user=USER):
    headers = {"x-user-id": str(user)} if user is not None else {}
    return client.get("/transcription/languages", headers=headers)


def test_the_deployment_backend_names_its_model_and_the_list_is_that_models(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok")
    probe = Probe("large-v3-turbo")
    r = _get(_client(monkeypatch, probe))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["source"], body["model"], len(body["languages"])) == ("provider", "large-v3-turbo", 100)
    assert {"code": "fr", "name": "French"} in body["languages"]
    assert body["default"] == {"language": None, "allowed_languages": []}
    assert probe.asked == [(ENV_URL, "tok")]


def test_a_backend_that_names_no_model_leaves_the_configured_one(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_MODEL", "medium.en")
    body = _get(_client(monkeypatch, Probe(None))).json()
    assert (body["source"], body["model"], body["languages"]) == (
        "configured", "medium.en", [{"code": "en", "name": "English"}])


def test_nothing_known_is_every_code_the_language_fields_accept(monkeypatch):
    body = _get(_client(monkeypatch, Probe(None))).json()
    assert (body["source"], body["model"], len(body["languages"])) == ("assumed", None, 100)


def test_the_deployment_default_is_reported_and_never_narrows_the_list(monkeypatch):
    monkeypatch.setenv(tlang.ENV_LANGUAGE, "en")
    monkeypatch.setenv(tlang.ENV_ALLOWED, "en,fr,es")
    body = _get(_client(monkeypatch, Probe("whisper-1"))).json()
    assert body["default"] == {"language": "en", "allowed_languages": ["en", "fr", "es"]}
    assert len(body["languages"]) == 99


def test_a_customer_endpoint_is_never_called_and_its_configured_model_decides(monkeypatch):
    probe = Probe("large-v3")
    context = {"transcription": {"url": "https://stt.customer.example", "token": "theirs",
                                 "model": "whisper-1", "provider": "customer"}}
    body = _get(_client(monkeypatch, probe, context)).json()
    assert probe.asked == []
    assert (body["source"], body["model"], len(body["languages"])) == ("configured", "whisper-1", 99)


def test_a_settings_backend_without_provenance_is_not_called(monkeypatch):
    probe = Probe("large-v3")
    context = {"transcription": {"url": "https://stt.unknown.example", "token": "t"}}
    body = _get(_client(monkeypatch, probe, context)).json()
    assert probe.asked == []
    assert body["source"] == "assumed"


def test_a_vexa_settings_backend_is_asked_instead_of_the_env_one(monkeypatch):
    probe = Probe("tiny.en")
    context = {"transcription": {"url": "https://transcription.vexa.example", "token": "v",
                                 "provider": "vexa"}}
    body = _get(_client(monkeypatch, probe, context)).json()
    assert probe.asked == [("https://transcription.vexa.example", "v")]
    assert (body["source"], body["model"]) == ("provider", "tiny.en")


def test_no_identity_is_401(monkeypatch):
    assert _get(_client(monkeypatch, Probe(None)), user=None).status_code == 401


# ── the probe: GET {url}/health → model ────────────────────────────────────────────────────────────

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _probe(handler, clock=None):
    return lc.HealthProbe(clock=clock or Clock(), transport=httpx.MockTransport(handler))


def test_the_probe_reads_model_from_health_and_sends_the_token():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"status": "healthy", "model": "large-v3-turbo"})

    model = asyncio.run(_probe(handler).model_of("http://stt:8000/", "tok"))
    assert model == "large-v3-turbo"
    assert seen == [("http://stt:8000/health", "Bearer tok")]


def test_a_loading_unit_still_names_its_model():
    model = asyncio.run(_probe(lambda r: httpx.Response(503, json={"model": "medium"})).model_of(
        "http://stt", None))
    assert model == "medium"


@pytest.mark.parametrize("response", [
    httpx.Response(404, json={"detail": "Not Found"}),
    httpx.Response(200, text="ok"),
    httpx.Response(200, json={"status": "healthy"}),
    httpx.Response(200, json={"model": ""}),
], ids=["404", "not-json", "no-model", "empty-model"])
def test_a_backend_that_does_not_say_names_no_model(response):
    assert asyncio.run(_probe(lambda r: response).model_of("http://stt", None)) is None


def test_an_unreachable_backend_names_no_model():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert asyncio.run(_probe(handler).model_of("http://stt", None)) is None


def test_the_answer_is_kept_per_url_for_the_ttl():
    calls = []
    clock = Clock()

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={"model": "small"})

    probe = _probe(handler, clock)

    async def run():
        await probe.model_of("http://a", None)
        await probe.model_of("http://a", None)
        await probe.model_of("http://b", None)
        clock.now += lc.PROBE_TTL_S + 1
        await probe.model_of("http://a", None)

    asyncio.run(run())
    assert calls == ["a", "b", "a"]
