"""End to end over fakeredis: hints → verified feed → scores → annotations → wakes → end, and a
second instance taking a source over from a dead one without scoring any line twice."""
import asyncio
import json

import fakeredis
import httpx
import pytest

from meeting_signals.keys import KeySource
from meeting_signals.meeting_feed import HINTS, BriefReader, MeetingFeed, feed
import base64
from meeting_signals.runner import Runner, SourceTask
from meeting_signals.store import LeaseLost, Store
from meeting_signals.wake import FlowsEmitter
from conftest import keyword_model

MODEL = {"adapter": "openai-compatible", "route": "m", "base_url": "http://m/v1", "secret_ref": "none"}
ROW = "42"


OWNER = "17"
ON = {"transcript_signals": "on", "model_signals": MODEL}


def agent(policy, asked=None):
    def handler(req):
        if asked is not None:
            asked.append(dict(req.url.params))
        assert req.headers["X-Internal-Secret"] == "s" and req.url.path == f"/internal/meetings/{ROW}/listen"
        if req.url.params.get("subject") != OWNER:
            return httpx.Response(404, json={"refused": "meeting_not_found"})
        return httpx.Response(200, json={"brief": {"labels": [{"name": "decision", "wake": True},
                                                              {"name": "action_item", "wake": True}]},
                                         "version": 3, "default": False, "chat_session": "meet-42",
                                         "subject": OWNER, "policy": policy})
    return httpx.MockTransport(handler)


def flows(sent):
    def handler(req):
        assert req.url.path == "/events/signal"
        assert req.headers["X-Internal-Secret"] == "s" and req.headers["X-Vexa-Service"] == "signals"
        sent.append(json.loads(req.content))
        return httpx.Response(202, json={"reactions_created": 1})
    return httpx.MockTransport(handler)


def make(r, me, calls, sent, policy=None, asked=None):
    src = MeetingFeed(r, BriefReader("http://agent", "s", transport=agent(ON if policy is None else policy, asked)),
                      consumer=me)
    return Runner(r, [src], KeySource(env={}), FlowsEmitter("http://flows", "s", transport=flows(sent)),
                  me=me, usd_per_mtok=2.0, model_transport=keyword_model(calls)), src


async def say(r, i, text, *, completed=True):
    seg = {"speaker": "A", "text": text, "start": float(i), "end": float(i) + 1, "abs_start_ms": i * 1000,
           "completed": completed, "language": "en", "segment_id": f"seg-{i}"}
    await r.xadd(feed(ROW), {"payload": json.dumps({"type": "transcription", "session_uid": "n", "meeting_id": "n",
                                                    "segments": [seg]})})


async def end(r):
    await r.xadd(feed(ROW), {"payload": json.dumps({"type": "session_end", "session_uid": "n"})})


def auth(user_id):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return enc({"alg": "HS256"}) + "." + enc({"meeting_id": int(ROW), "user_id": user_id})


async def hint(r, src, user_id=int(OWNER)):
    await src._ensure_hint_group()
    await r.xadd(HINTS, {"payload": json.dumps({"meeting_id": ROW}), "auth": auth(user_id), "sig": "0" * 64})


async def annotations(r):
    return [json.loads(f["payload"]) for _id, f in await r.xrange(f"sig:meeting:{ROW}")]


async def until(pred, timeout=8.0):
    for _ in range(int(timeout / 0.05)):
        if await pred():
            return True
        await asyncio.sleep(0.05)
    return False


def test_a_meeting_is_discovered_scored_woken_and_ended():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        runner, src = make(r, "A", calls, sent)
        await hint(r, src)
        await say(r, 1, "good morning everyone")
        await say(r, 2, "draft line", completed=False)
        await say(r, 3, "Vexa, please write that down")
        await runner.discover_once()
        await runner.supervise_once()
        assert list(runner.tasks) == [f"meeting:{ROW}"]
        assert await until(lambda: _len(sent, 1))
        await say(r, 4, "we decide to go with option b")
        await end(r)
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 10)
        return r, calls, sent

    r, calls, sent = asyncio.run(go())
    # the final that addresses Vexa is the rule tier's (no model call); the others are scored
    assert calls == ["good morning everyone", "we decide to go with option b"]
    reasons = [s["data"]["reason"] for s in sent]
    assert reasons == ["addressed", "end"]
    first = sent[0]
    assert first["name"] == "meeting.signal" and first["subject"] == OWNER
    d = first["data"]
    assert d["source"] == {"kind": "meeting", "id": ROW} and d["meeting_row"] == ROW and d["brief_version"] == 3
    assert [b["segment_id"] for b in d["buffer"]] == ["seg-1", "seg-3"]
    anns = asyncio.run(_anns_after(r))
    assert sorted(a["segment_id"] for a in anns) == ["seg-1", "seg-3", "seg-4"]


async def _anns_after(r):
    return await annotations(r)


async def _len(lst, n):
    return len(lst) >= n


def test_off_policy_starts_nothing():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)

        calls = []
        runner, src = make(r, "A", calls, [], policy={"transcript_signals": "off", "model_signals": MODEL})
        await hint(r, src)
        await say(r, 1, "vexa hello")
        await runner.discover_once()
        await runner.supervise_once()
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 5)       # read the policy, let go
        await runner.supervise_once()
        return runner.tasks, calls, await r.get(f"sig:lease:meeting:{ROW}")
    tasks, calls, lease = asyncio.run(go())
    assert tasks == {} and calls == [] and lease is None


def test_a_hint_naming_someone_else_as_owner_listens_to_nothing():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent, asked = [], [], []
        runner, src = make(r, "A", calls, sent, asked=asked)
        await hint(r, src, user_id=99)
        await say(r, 1, "vexa hello")
        await runner.discover_once()
        await runner.supervise_once()
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 5)
        return calls, sent, asked
    calls, sent, asked = asyncio.run(go())
    assert calls == [] and sent == [] and asked == [{"subject": "99"}]


def test_a_second_instance_takes_over_without_scoring_any_line_twice(monkeypatch):
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        a, src_a = make(r, "A", calls, sent)
        # A commits its scores but dies before acknowledging anything, and never lets go of its lease
        async def no_ack(sid, ids):
            return None
        monkeypatch.setattr(src_a, "ack", no_ack)
        async def no_release(kind, sid, me):
            return True
        monkeypatch.setattr(a.store, "release", no_release)
        dead = {"now": False}
        real_read = src_a.read

        async def read(sid, consumer, after):
            if dead["now"]:
                raise RuntimeError("instance A died")
            return await real_read(sid, consumer, after)
        monkeypatch.setattr(src_a, "read", read)
        await hint(r, src_a)
        for i in range(1, 4):
            await say(r, i, f"line {i}")
        await a.discover_once()
        await a.supervise_once()
        assert await until(lambda: _len(calls, 3))
        assert await until(lambda: _state_upto(r))
        dead["now"] = True
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(a.tasks[f"meeting:{ROW}"], 5)
        # the lease would lapse after 30 s; B cannot take it while it is held
        b, _ = make(r, "B", calls, sent)
        await b.supervise_once()
        assert b.tasks == {}
        await r.delete(f"sig:lease:meeting:{ROW}")
        await say(r, 4, "line 4")
        await b.supervise_once()
        assert list(b.tasks) == [f"meeting:{ROW}"]
        assert await until(lambda: _len(calls, 4))
        await end(r)
        await asyncio.wait_for(b.tasks[f"meeting:{ROW}"], 10)
        pending = await r.xpending(feed(ROW), "signals")
        return calls, sent, await annotations(r), pending

    calls, sent, anns, pending = asyncio.run(go())
    assert calls == ["line 1", "line 2", "line 3", "line 4"]              # each line scored once
    assert sorted(a["segment_id"] for a in anns) == ["seg-1", "seg-2", "seg-3", "seg-4"]
    assert [s["data"]["reason"] for s in sent] == ["end"]
    assert len(sent[0]["data"]["buffer"]) == 4                             # A's buffer survived the takeover
    assert pending["pending"] == 0                                         # B acknowledged A's leftovers


async def _state_upto(r):
    raw = await r.get(f"sig:state:meeting:{ROW}")
    return bool(raw) and json.loads(raw)["scored_upto"] != ""


def test_a_writer_that_lost_its_lease_cannot_commit():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        s = Store(r)
        assert await s.acquire("meeting", ROW, "A")
        assert not await s.acquire("meeting", ROW, "B")
        await r.set(f"sig:lease:meeting:{ROW}", "B")
        from meeting_signals.core import State
        with pytest.raises(LeaseLost):
            await s.commit("meeting", ROW, "A", State(score=9), [{"x": 1}], ["seg-1"])
        return await r.get(f"sig:state:meeting:{ROW}"), await r.xlen(f"sig:meeting:{ROW}")
    assert asyncio.run(go()) == (None, 0)


def test_a_saved_but_unconfirmed_wake_is_resent_with_the_same_id():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        outcomes = iter([httpx.Response(503), httpx.Response(202, json={})])

        def handler(req):
            sent.append(json.loads(req.content))
            return next(outcomes)
        runner, src = make(r, "A", calls, sent)
        runner.emitter = FlowsEmitter("http://flows", "s", transport=httpx.MockTransport(handler))
        await hint(r, src)
        await say(r, 1, "vexa are you there")
        await runner.discover_once()
        await runner.supervise_once()
        assert await until(lambda: _len(sent, 2))
        await end(r)
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 15)
        return sent
    sent = asyncio.run(go())
    assert len(sent) == 2 and sent[0] == sent[1]                           # the same wake, resent



# -- the rule tier hears an address on the UNFINISHED transcript (app.dev 2026-10-16) --------------
# Meeting 146: the interim "Vexa, will you react if I..." reached the feed 23.5 s before its final
# ("react Vexa will you react if I call you ..."), which is the only line the sensor read. The rule
# tier now wakes on the first interim that addresses Vexa; the final of the same speech (another
# segment id, overlapping in time) does not wake again; unaddressed talk is still scored on finals.

async def speak(r, seg_id, text, start, end, *, completed, speaker="dmitry"):
    seg = {"speaker": speaker, "text": text, "start": start, "end": end, "completed": completed,
           "language": "en", "segment_id": seg_id}
    await r.xadd(feed(ROW), {"payload": json.dumps({"type": "transcription", "session_uid": "n",
                                                    "meeting_id": "n", "segments": [seg]})})


def test_an_interim_addressing_vexa_wakes_at_once_and_its_final_does_not_wake_again():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        runner, src = make(r, "A", calls, sent)
        await hint(r, src)
        await speak(r, "ch-0:1:200", "so the plan for next", 100.0, 101.0, completed=False)
        await speak(r, "ch-0:1:120", "Vexa, will you react if I", 112.4, 114.5, completed=False)
        await runner.discover_once()
        await runner.supervise_once()
        assert await until(lambda: _len(sent, 1))
        assert calls == []                                   # no model call for an interim
        await speak(r, "ch-0:1:120", "Vexa, will you react if I call", 112.4, 116.0, completed=False)
        await speak(r, "ch-0:1:107", "react Vexa will you react if I call you this is some", 107.9, 137.5,
                    completed=True)
        await speak(r, "ch-0:1:140", "we decide to go with option b", 140.0, 143.0, completed=True)
        await end(r)
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 10)
        return calls, sent

    calls, sent = asyncio.run(go())
    first = sent[0]["data"]
    assert first["reason"] == "addressed"
    assert [b["text"] for b in first["buffer"]] == ["Vexa, will you react if I"]
    assert [s["data"]["reason"] for s in sent] == ["addressed", "end"]          # the final woke nothing
    assert calls == ["we decide to go with option b"]                          # finals only, deduped


def test_an_unaddressed_interim_is_neither_scored_nor_buffered():
    from meeting_signals.meeting_feed import parse
    item = parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [
        {"speaker": "A", "text": "we will decide", "start": 1, "end": 2, "completed": False, "segment_id": "x"}]})})
    assert item.kind == "other"
    for text in ("Vexa, note that", "ok vexa what did we decide", "To Vexa: summarise", "so. Vexa can you"):
        item = parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [
            {"speaker": "A", "text": text, "start": 1, "end": 2, "completed": False, "segment_id": "x"}]})})
        assert item.kind == "line" and item.mode == "addressed" and item.interim, text



# -- BEING ADDRESSED NEVER WAITS FOR THE MODEL (app.dev 2026-10-17, meeting 153) -------------------
# A room call in Russian: "Векса, привет" arrived only on a FINAL (its interims read "Яксаааа.",
# "Привет."), the rule tier knew only the Latin name and only interims, and every model call answered
# 402 Payment Required — so the line was buffered unscored and the room heard nothing until the end.

def test_a_final_addressing_vexa_by_its_russian_name_is_addressed_without_the_model():
    from meeting_signals.meeting_feed import parse

    def final(text):
        return parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [
            {"speaker": "A", "text": text, "start": 1, "end": 2, "completed": True, "segment_id": "x"}]})})

    for text in ("Ты меня слышишь, ты не будешь на меня реагировать сейчас. Векса, привет. Вексе, вексе.",
                 "Векса, что мы решили?", "привет вэкса", "Vexa, note that", "ok vexa what did we decide"):
        item = final(text)
        assert item.kind == "line" and item.mode == "addressed" and not item.interim, text
    for text in ("и чат, и рум, и звонок, и векс.", "у Вексы есть комнаты", "Vexa's rooms are new",
                 "we decide to go with option b"):
        assert final(text).mode == "score", text


def refusing_model(calls):
    def handler(req):
        calls.append(json.loads(req.content)["messages"][1]["content"])
        return httpx.Response(402, json={"error": {"message": "Payment Required"}})
    return httpx.MockTransport(handler)


def test_an_address_on_a_final_wakes_the_room_while_the_model_refuses_every_call():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        src = MeetingFeed(r, BriefReader("http://agent", "s", transport=agent(ON)), consumer="A")
        runner = Runner(r, [src], KeySource(env={}), FlowsEmitter("http://flows", "s", transport=flows(sent)),
                        me="A", usd_per_mtok=2.0, model_transport=refusing_model(calls))
        await hint(r, src)
        await speak(r, "s-1", "Нашел звонок. У нас есть", 0.0, 8.0, completed=True)
        await speak(r, "s-2", "Ты меня слышишь, ты не будешь на меня реагировать сейчас. Векса, привет.",
                    30.0, 41.0, completed=True)
        await runner.discover_once()
        await runner.supervise_once()
        assert await until(lambda: _len(sent, 1))              # woken DURING the call, not at its end
        await end(r)
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 10)
        return calls, sent

    calls, sent = asyncio.run(go())
    assert sent[0]["data"]["reason"] == "addressed"
    assert "Векса, привет." in sent[0]["data"]["buffer"][-1]["text"]
    assert len(calls) == 1                                       # only the unaddressed line went to the model


# -- COALESCE: while the chat's signal turn is still running, new lines wait in ONE pending wake ---
# app.dev 2026-10-16: each wake spent the cap and queued another turn behind the running one.

def test_wakes_coalesce_while_the_chats_signal_turn_runs():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls, sent = [], []
        runner, src = make(r, "A", calls, sent)
        await hint(r, src)
        await r.set("session:meet-42:lease", "agent-17-chat-meet-42-signal")      # its turn is running
        await speak(r, "c-1", "Vexa, first question", 10.0, 12.0, completed=False)
        await runner.discover_once()
        await runner.supervise_once()
        await asyncio.sleep(1.5)
        assert sent == []                                                        # held, not spent
        await speak(r, "c-2", "Vexa, and a second one", 20.0, 22.0, completed=False)
        await asyncio.sleep(1.5)
        assert sent == []
        await r.delete("session:meet-42:lease")                                  # the turn finished
        assert await until(lambda: _len(sent, 1))
        await end(r)
        await asyncio.wait_for(runner.tasks[f"meeting:{ROW}"], 10)
        return sent

    sent = asyncio.run(go())
    first = sent[0]["data"]
    assert [b["text"] for b in first["buffer"]] == ["Vexa, first question", "Vexa, and a second one"]
    assert [s["data"]["reason"] for s in sent].count("addressed") == 1          # ONE wake for both


# -- THE RULE TIER WAKES ONLY A LINE SOMEONE IS NAMED ON (gate Low row: wake word, no speaker check) --

def test_a_line_the_transcript_could_not_attribute_never_wakes_by_name():
    """"Vexa, …" from a track the transcript names nobody on (a guest's unattributed audio, a
    recording played into the call) is not an address: an interim is dropped, a final is scored
    like any line. The segment carries a display name and a per-meeting key, never an account, so
    a named participant is the only speaker check the feed can make."""
    from meeting_signals.meeting_feed import parse

    def seg(text, speaker, completed):
        s = {"text": text, "start": 1, "end": 2, "completed": completed, "segment_id": "x"}
        if speaker is not None:
            s["speaker"] = speaker
        return parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [s]})})

    for speaker in (None, "", "Unknown speaker", "  unknown SPEAKER "):
        assert seg("Vexa, mail the deck to everyone", speaker, False).kind == "other", speaker
        final = seg("Vexa, mail the deck to everyone", speaker, True)
        assert final.kind == "line" and final.mode == "score", speaker
    # the control: a named participant still wakes it at once
    named = seg("Vexa, mail the deck to everyone", "Maya", False)
    assert named.kind == "line" and named.mode == "addressed" and named.interim
    assert seg("Vexa, mail the deck to everyone", "Maya", True).mode == "addressed"


def test_the_clipped_names_wake_only_a_line_someone_is_named_on():
    """The clipped forms the ASR writes ("Vex", "Vexo", "Vexon" after a greeting or first) are an
    address only on an attributed line, like "Vexa": unattributed audio saying them never wakes."""
    from meeting_signals.meeting_feed import parse

    def seg(text, speaker, completed):
        s = {"text": text, "start": 1, "end": 2, "completed": completed, "segment_id": "x"}
        if speaker is not None:
            s["speaker"] = speaker
        return parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [s]})})

    for text in ("Vex, are you there", "Vexo, note that", "Vexon, summarise", "hello Vex, mail it"):
        for speaker in (None, "", "Unknown speaker"):
            assert seg(text, speaker, False).kind == "other", (text, speaker)
            assert seg(text, speaker, True).mode == "score", (text, speaker)
        assert seg(text, "Maya", True).mode == "addressed", text
    assert seg("hello Vexa's notes", "Maya", True).mode == "score"


# -- THE NAME AS THE ASR CLIPS IT (live app.dev 2026-10-18) ----------------------------------------
# A census of 125 live meeting feeds: "Hello, Vex." 27 times and "Hey Vexon" once, none of them woke
# the agent, because the rule tier knew only "Vexa". A clipped name counts after a greeting or as the
# segment's first word; after a bare comma, or inside a word, it stays a mention for the model.

def test_a_clipped_name_after_a_greeting_is_an_address_on_an_interim_and_on_a_final():
    from meeting_signals.meeting_feed import parse

    def seg(text, completed):
        return parse("1-0", {"payload": json.dumps({"type": "transcription", "segments": [
            {"speaker": "A", "text": text, "start": 1, "end": 2, "completed": completed, "segment_id": "x"}]})})

    for text in ("Hello, Vex.", "Hey Vexon, please react on that.", "Vex, can you check the date?",
                 "hello vexa we need you"):
        for completed in (False, True):
            item = seg(text, completed)
            assert item.kind == "line" and item.mode == "addressed", (text, completed)
    for text in ("Didn't you said something about the Vexer channel?", "that would vex them, sadly",
                 "the vex of it", "Vexa's rooms are new"):
        assert seg(text, True).mode == "score", text
        assert seg(text, False).kind == "other", text
