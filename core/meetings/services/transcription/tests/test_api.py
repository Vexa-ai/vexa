"""OpenAI-compatible transcription endpoint — auth + request-validation contract.

The happy path (real model inference) is a GPU/integration concern, exercised by the deploy
unit's smoke, not the unit gate. Here we pin the seam the bot's whisper client relies on:
token auth and multipart validation, both reachable without loading a model.
"""
from __future__ import annotations


def test_transcribe_requires_token_when_configured(client, monkeypatch):
    import transcription.main as svc

    monkeypatch.setattr(svc, "API_TOKEN", "secret-token")
    # No X-API-Key / Bearer → rejected before any model work.
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFF....", "audio/wav")},
        data={"model": "large-v3-turbo"},
    )
    assert r.status_code == 401


def test_transcribe_rejects_missing_file(client, monkeypatch):
    import transcription.main as svc

    # No token configured → auth open; FastAPI multipart validation rejects the missing file.
    monkeypatch.setattr(svc, "API_TOKEN", "")
    r = client.post("/v1/audio/transcriptions", data={"model": "large-v3-turbo"})
    assert r.status_code == 422
