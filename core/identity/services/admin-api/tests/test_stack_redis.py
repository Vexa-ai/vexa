"""O-STACK-2 — Valkey backing-stack eval (testcontainers).

Runs against an ephemeral **Valkey** (the engine every surface ships, #653), asserting the REAL
usage patterns work end-to-end on the store we deploy:

  Stream     XADD → XREADGROUP → XACK   — transcription_segments (consumer group collector_group)
  Reclaim    XAUTOCLAIM                 — orphan-drain on the same stream (the #636 command; absent on Redis < 6.2)
  Pub/Sub    PUBLISH → SUBSCRIBE        — tc:meeting:*, bot_commands:meeting:*, meeting:*:status
  List queue LPUSH → BRPOP              — webhook_retry_queue
  Sorted set ZADD → ZRANGEBYSCORE       — speaker_events:{session_uid} (scheduler/score-ordered)
"""
import json
import time

from conftest import requires_docker

pytestmark = requires_docker

STREAM = "transcription_segments"
GROUP = "collector_group"


def test_stream_xadd_xreadgroup_xack(redis_client):
    """The transcript-durability path: bot XADDs a segment payload, the collector group reads
    it via XREADGROUP, acks via XACK — and the pending-entries list drains to empty."""
    r = redis_client
    r.delete(STREAM)
    # collector creates the group at the stream head (mkstream).
    r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)

    payload = json.dumps({
        "type": "transcription", "token": "<JWT>", "uid": "sess-1",
        "platform": "google_meet", "meeting_id": "8725",
        "segments": [{"start": 19.0, "end": 34.0, "text": "Hello, this is the transcript",
                      "language": "en", "completed": False, "speaker": "Alice"}],
    })
    msg_id = r.xadd(STREAM, {"payload": payload})

    # XREADGROUP — one consumer in the group reads the new message.
    read = r.xreadgroup(GROUP, "consumer-1", {STREAM: ">"}, count=10)
    assert read, "XREADGROUP returned no messages"
    _stream, entries = read[0]
    assert len(entries) == 1
    got_id, fields = entries[0]
    assert got_id == msg_id
    got = json.loads(fields["payload"])
    assert got["meeting_id"] == "8725"
    assert got["segments"][0]["speaker"] == "Alice"
    assert got["segments"][0]["completed"] is False     # draft segment

    # Before XACK: the message is pending for this consumer.
    pending = r.xpending(STREAM, GROUP)
    assert pending["pending"] == 1

    # XACK — message acknowledged, pending drains.
    acked = r.xack(STREAM, GROUP, got_id)
    assert acked == 1
    assert r.xpending(STREAM, GROUP)["pending"] == 0


def test_stream_xautoclaim_reclaims_orphans(redis_client):
    """The orphan-reclaim path (#636): XAUTOCLAIM must exist and drain a pending entry from a dead
    consumer to a survivor. This is the exact command jammy's Redis 6.0.16 lacked — asserting it
    against the shipped Valkey engine is what makes the eval witness the store users run (#653)."""
    r = redis_client
    r.delete(STREAM)
    r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    msg_id = r.xadd(STREAM, {"payload": json.dumps({"uid": "orphan"})})

    # "dead" consumer reads the message but never acks — it stays pending, owned by the dead consumer.
    r.xreadgroup(GROUP, "consumer-dead", {STREAM: ">"}, count=10)
    assert r.xpending(STREAM, GROUP)["pending"] == 1

    # XAUTOCLAIM (min-idle 0) transfers the orphan to the survivor — the reclaim the collector runs.
    cursor, claimed, _deleted = r.xautoclaim(STREAM, GROUP, "consumer-survivor", min_idle_time=0, start_id="0")
    assert any(cid == msg_id for cid, _fields in claimed), "XAUTOCLAIM did not reclaim the orphaned entry"

    # The survivor now owns it; ack drains the group to empty.
    assert r.xack(STREAM, GROUP, msg_id) == 1
    assert r.xpending(STREAM, GROUP)["pending"] == 0


def test_pubsub_publish_subscribe_tc_meeting(redis_client):
    """tc:meeting:{id}:mutable — collector PUBLISHes change-only updates; the gateway SUBSCRIBEs
    and fans out to the dashboard WebSocket."""
    r = redis_client
    channel = "tc:meeting:8725:mutable"
    sub = r.pubsub()
    sub.subscribe(channel)
    # Drain the subscribe-confirmation message.
    assert sub.get_message(timeout=2)["type"] == "subscribe"

    body = json.dumps({"start_time": 19.0, "text": "Hello", "speaker": "Alice"})
    # Retry a few times — SUBSCRIBE registration can race the first PUBLISH.
    received = None
    for _ in range(20):
        r.publish(channel, body)
        m = sub.get_message(timeout=1)
        if m and m["type"] == "message":
            received = m
            break
        time.sleep(0.05)
    assert received is not None, "no message delivered on tc:meeting channel"
    assert json.loads(received["data"])["speaker"] == "Alice"
    sub.close()


def test_pubsub_bot_commands_and_status(redis_client):
    """bot_commands:meeting:{id} (meeting-api → vexa-bot) and meeting:{id}:status
    (meeting-api → gateway → WS). Both are pub/sub fire-and-forget."""
    r = redis_client
    cmd_ch = "bot_commands:meeting:8725"
    status_ch = "meeting:8725:status"
    sub = r.pubsub()
    sub.subscribe(cmd_ch, status_ch)
    # Drain the two subscribe confirmations.
    confirms = 0
    for _ in range(4):
        m = sub.get_message(timeout=2)
        if m and m["type"] == "subscribe":
            confirms += 1
        if confirms == 2:
            break
    assert confirms == 2

    status_msg = json.dumps({"meeting_id": 8725, "status": "active",
                             "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                             "user_id": 42})
    seen = {"cmd": None, "status": None}
    for _ in range(40):
        r.publish(cmd_ch, json.dumps({"action": "leave"}))
        r.publish(status_ch, status_msg)
        m = sub.get_message(timeout=1)
        if m and m["type"] == "message":
            if m["channel"] == cmd_ch:
                seen["cmd"] = json.loads(m["data"])
            elif m["channel"] == status_ch:
                seen["status"] = json.loads(m["data"])
        if seen["cmd"] and seen["status"]:
            break
        time.sleep(0.02)
    assert seen["cmd"] == {"action": "leave"}
    assert seen["status"]["status"] == "active"
    sub.close()


def test_list_queue_lpush_brpop(redis_client):
    """webhook_retry_queue — webhook_delivery LPUSHes a failed delivery; retry_worker BRPOPs it
    (FIFO: LPUSH head, BRPOP tail). This is why webhooks eventually arrive."""
    r = redis_client
    q = "webhook_retry_queue"
    r.delete(q)

    j1 = json.dumps({"meeting_id": 1, "event": "meeting.completed", "attempt": 1})
    j2 = json.dumps({"meeting_id": 2, "event": "meeting.completed", "attempt": 1})
    r.lpush(q, j1)
    r.lpush(q, j2)
    assert r.llen(q) == 2

    # BRPOP pops the oldest (tail) first → FIFO ordering preserved.
    _q, first = r.brpop(q, timeout=2)
    assert json.loads(first)["meeting_id"] == 1
    _q, second = r.brpop(q, timeout=2)
    assert json.loads(second)["meeting_id"] == 2
    assert r.llen(q) == 0


def test_sorted_set_zadd_zrangebyscore(redis_client):
    """speaker_events:{session_uid} — collector ZADDs speaker events scored by relative
    timestamp (ms); the score-ordered ZRANGEBYSCORE read is the speaker-mapping fallback. Same
    sorted-set mechanism scheduler jobs use (score = run-at)."""
    r = redis_client
    key = "speaker_events:sess-1"
    r.delete(key)

    r.zadd(key, {"SPEAKER_START:Alice": 5000})
    r.zadd(key, {"SPEAKER_STOP:Alice": 8000})
    r.zadd(key, {"SPEAKER_START:Bob": 12000})

    # Window query — events between 4s and 9s, score-ordered.
    window = r.zrangebyscore(key, 4000, 9000)
    assert window == ["SPEAKER_START:Alice", "SPEAKER_STOP:Alice"]
    # Full ordered range with scores.
    full = r.zrange(key, 0, -1, withscores=True)
    assert [m for m, _ in full] == ["SPEAKER_START:Alice", "SPEAKER_STOP:Alice", "SPEAKER_START:Bob"]
    assert full[-1][1] == 12000.0
    # "Due jobs" pattern — pop everything up to a cutoff score (scheduler semantics).
    due = r.zrangebyscore(key, "-inf", 8000)
    assert due == ["SPEAKER_START:Alice", "SPEAKER_STOP:Alice"]
