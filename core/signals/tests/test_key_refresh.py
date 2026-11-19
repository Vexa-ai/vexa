"""A key the model refuses is dropped and read again once (app.dev 2026-10-16: the founder stored a
good key in the broker, but the runner kept the dead env fallback it had cached and every line was
refused HTTP 401 until the container was restarted)."""
import asyncio
import json

import httpx

from meeting_signals import policy as policies
from meeting_signals.model import Line
from meeting_signals.runner import SourceTask

REF = "broker:signals/openrouter"
MODEL = {"adapter": "openai-compatible", "route": "m", "base_url": "http://m/v1", "secret_ref": REF}


class Keys:
    """dead until forgotten; then the broker has the good one"""
    def __init__(self):
        self.value, self.forgot = "dead", []

    async def resolve(self, ref):
        return self.value

    def forget(self, ref):
        self.forgot.append(ref)
        self.value = "good"


def model(seen):
    def handler(req):
        seen.append(req.headers.get("Authorization"))
        if req.headers.get("Authorization") != "Bearer good":
            return httpx.Response(401, json={"error": "no"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"decision": 0.9})}}]})
    return httpx.MockTransport(handler)


class Run:
    def __init__(self, keys, transport):
        self.keys, self.model_transport = keys, transport

    def say(self, line):
        pass


def test_a_refused_key_is_forgotten_and_read_again_once():
    seen, keys = [], Keys()
    task = SourceTask.__new__(SourceTask)
    task.run_ = Run(keys, model(seen))
    pol, problems = policies.resolve({"transcript_signals": "on", "model_signals": MODEL})
    assert not problems
    score = asyncio.run(task._scorer(pol))
    out = asyncio.run(score({"decision": "Was a decision made?"}, Line("1", "A", "We decided.", 0, 1), []))
    assert out.scores == {"decision": 0.9}
    assert keys.forgot == [REF] and seen == ["Bearer dead", "Bearer good"]
