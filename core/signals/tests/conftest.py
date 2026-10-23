import asyncio
import json

import httpx
import pytest


#: The model endpoints the suite's policies name (``http://m/v1``, ``http://llm/v1`` …) are internal
#: hosts the operator allowed — the on-prem model server case — reached at a private address. The
#: tests about the guard itself (``test_model_endpoint_guard.py``) set their own.
SUITE_MODEL_HOSTS = "m l llm h"
SUITE_MODEL_ADDRESS = "10.0.0.5"


@pytest.fixture(autouse=True)
def _allowed_suite_model_hosts(monkeypatch):
    from meeting_signals import model
    monkeypatch.setenv(model.ALLOW_ENV, SUITE_MODEL_HOSTS)
    monkeypatch.setattr(model, "resolver", lambda host: [SUITE_MODEL_ADDRESS])


def run(coro):
    return asyncio.run(coro)


def keyword_model(calls: list):
    """An OpenAI-compatible endpoint that scores by keyword and counts its calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        text = user["transcript"]["NEW SPEECH"][0]["text"].lower()
        calls.append(text)
        scores = {}
        for label in user["labels"]:
            hit = {"addressed_to_vexa": "vexa", "decision": "decide", "action_item": "will"}.get(label)
            scores[label] = 0.9 if hit and hit in text else 0.01
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(scores)}}],
                                          "usage": {"total_tokens": 100}})
    return httpx.MockTransport(handler)


@pytest.fixture
def anyrun():
    return run
