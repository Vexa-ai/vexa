"""The per-speaker silence-gap segmenter (ported PcmBuffer shape) — pure, offline, clock-injected."""

from discord_bot.segmenter import PcmBuffer


def test_write_then_drain_ready_after_silence():
    buf = PcmBuffer()
    buf.write(1, b"aaaa", now=0.0)
    buf.write(1, b"bbbb", now=0.1)
    # not silent yet at t=0.2 (only 0.1s since last write, threshold 0.8s)
    assert buf.drain_ready(0.8, now=0.2) == []
    # silent enough at t=1.0 (0.9s since last write)
    segs = buf.drain_ready(0.8, now=1.0)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.user_id == 1
    assert seg.pcm == b"aaaabbbb"
    assert seg.start == 0.0
    assert seg.end == 0.1


def test_drain_ready_leaves_actively_talking_users_buffered():
    buf = PcmBuffer()
    buf.write(1, b"x", now=0.0)
    buf.write(2, b"y", now=0.9)
    # user 1 has been silent 1.0s, user 2 only 0.1s
    segs = buf.drain_ready(0.8, now=1.0)
    assert [s.user_id for s in segs] == [1]
    # user 2 still buffered
    assert buf.drain_ready(0.8, now=1.0) == []
    assert buf.drain_ready(0.8, now=2.0) != []


def test_multiple_users_segmented_independently():
    buf = PcmBuffer()
    buf.write(10, b"A", now=0.0)
    buf.write(20, b"B", now=0.0)
    segs = buf.drain_ready(0.5, now=1.0)
    assert {s.user_id for s in segs} == {10, 20}


def test_drain_all_ignores_silence_window():
    buf = PcmBuffer()
    buf.write(1, b"live-audio", now=5.0)
    segs = buf.drain_all()
    assert len(segs) == 1
    assert segs[0].pcm == b"live-audio"


def test_drained_buffer_resets_for_next_utterance():
    buf = PcmBuffer()
    buf.write(1, b"first", now=0.0)
    buf.drain_ready(0.5, now=1.0)
    buf.write(1, b"second", now=2.0)
    segs = buf.drain_ready(0.5, now=3.0)
    assert len(segs) == 1
    assert segs[0].pcm == b"second"
    assert segs[0].start == 2.0


def test_empty_buffer_drains_nothing():
    buf = PcmBuffer()
    assert buf.drain_ready(0.8, now=0.0) == []
    assert buf.drain_all() == []
