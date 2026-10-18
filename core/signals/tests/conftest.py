import asyncio
import json

import httpx
import pytest


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
