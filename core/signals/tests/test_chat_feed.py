"""The room adapter: the fixed rule tier, the agent's own words as context only, and a room woken
end to end over fakeredis."""
import asyncio
import json

import fakeredis
import httpx

from meeting_signals.chat_feed import ChatBriefReader, ChatFeed, parse, stream
from meeting_signals.keys import KeySource
from meeting_signals.runner import Runner
from meeting_signals.wake import FlowsEmitter
from conftest import keyword_model

S = "pchat-1"
OWNER = "17"
MODEL = {"adapter": "openai-compatible", "route": "m", "base_url": "http://m/v1", "secret_ref": "none"}


def msg(i, text, kind="person", name="Ana", reply_kind=None, agent_name="Vexa"):
    p = {"message_id": f"m-{i}", "session": S, "subject": OWNER, "author": {"kind": kind, "id": name.lower(), "name": name},
         "text": text, "at": 1760000000.0 + i, "agent_name": agent_name}
    if reply_kind:
        p["reply_to"] = {"message_id": "m-0", "author_kind": reply_kind}
    return {"payload": json.dumps(p)}


def mode(fields, prev=None):
    return parse("1-0", fields, json.loads(prev["payload"]) if prev else None).mode


def test_the_fixed_rule_tier():
    assert mode(msg(1, "@vexa can you check this")) == "addressed"
    assert mode(msg(1, "ask Juno about it", agent_name="Juno")) == "addressed"
    assert mode(msg(1, "fine", reply_kind="agent")) == "addressed"
    assert mode(msg(2, "and the dates?"), prev=msg(1, "Here is the plan.", kind="agent", name="Vexa")) == "addressed"
    assert mode(msg(2, "and the dates?"), prev=msg(1, "Here is the plan.")) == "score"
    assert mode(msg(1, "the vexation is real")) == "score"
    assert mode(msg(1, "Here is the plan.", kind="agent", name="Vexa")) == "context"


def agent():
    def handler(req):
        assert req.url.path == f"/internal/chats/{S}/listen" and req.url.params["subject"] == OWNER
        return httpx.Response(200, json={"brief": {"labels": [{"name": "decision", "wake": True}]}, "version": 1,
                                         "chat_session": S, "subject": OWNER,
                                         "policy": {"transcript_signals": "on", "model_signals": MODEL}})
    return httpx.MockTransport(handler)


def test_a_room_is_discovered_and_woken_and_the_agents_words_are_never_buffered():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []

        def flows(req):
            sent.append(json.loads(req.content))
            return httpx.Response(202, json={})
        src = ChatFeed(r, ChatBriefReader("http://agent", "s", transport=agent()), consumer="A")
        runner = Runner(r, [src], KeySource(env={}), FlowsEmitter("http://flows", "s", transport=httpx.MockTransport(flows)),
                        me="A", usd_per_mtok=2.0, model_transport=keyword_model(calls))
        await r.xadd(stream(S), msg(1, "morning all"))
        await runner.discover_once()
        await runner.supervise_once()
        task = runner.tasks[f"chat:{S}"]
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.05)
        await r.xadd(stream(S), msg(2, "Here is the plan.", kind="agent", name="Vexa"))
        await r.xadd(stream(S), msg(3, "and when do we start?", name="Bo"))
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        return calls, sent
    calls, sent = asyncio.run(go())
    assert calls == ["morning all"]                      # the agent's line and the addressed one: no model call
    d = sent[0]["data"]
    assert sent[0]["subject"] == OWNER and d["source"] == {"kind": "chat", "id": S} and d["session"] == S
    assert d["reason"] == "addressed" and [b["segment_id"] for b in d["buffer"]] == ["m-1", "m-3"]
