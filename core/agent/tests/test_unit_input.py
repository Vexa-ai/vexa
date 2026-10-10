"""Only agent-api can put a message on a live worker's input stream.

The worker takes what arrives on ``unit:<id>:in`` as its owner's next message, and Redis cannot say
who wrote an entry. agent-api signs every entry with the unit's own key (derived from the internal
secret) and hands the worker that key; the worker runs only the entries that verify. Unsigned,
forged, tampered and other-unit entries are passed over on every read path — the between-turns loop,
the mid-turn steering drain, and the boot drain — and the key never reaches the model's harness.
"""
from __future__ import annotations

import json

from control_plane import dispatch
from llm.ports import harness_subprocess_env
from shared import unit_input
from shared.config import load_settings
from tests.conftest import TEST_UNIT_IN_KEY, signed_turn
from tests.test_unit_foundation import VALID_INV, _FakeIdentity, _FakeRuntime, _WarmFake
from tests.test_boot_drain import FakeStream as BootStream
from tests.test_worker import CursorStream, FakeStream
from worker.worker import serve

SECRET = "internal-secret-for-tests-0123456789abcdef"
OTHER_UNIT_KEY = unit_input.unit_key(SECRET, "agent-8-chat-main")


def _turn(prompt):
    yield {"type": "message-delta", "text": f"re:{prompt}"}


def _ran(stream) -> list[str]:
    return [e["text"] for e in stream.events() if e["type"] == "message-delta"]


def _forgeries(prompt: str) -> list[dict]:
    good = signed_turn({"prompt": prompt})
    return [
        {"turn": json.dumps({"prompt": prompt})},                                   # unsigned
        unit_input.signed_entry(OTHER_UNIT_KEY, {"prompt": prompt}),               # another unit's key
        {"turn": json.dumps({"prompt": prompt + "!"}), "sig": good["sig"]},       # tampered
        {"turn": json.dumps({"prompt": prompt}), "sig": "00" * 32},                # made up
    ]


def test_keys_are_per_unit_and_per_secret():
    a = unit_input.unit_key(SECRET, "agent-7-chat-main")
    assert a and a != unit_input.unit_key(SECRET, "agent-8-chat-main")
    assert a != unit_input.unit_key(SECRET + "x", "agent-7-chat-main")
    assert unit_input.unit_key("", "agent-7-chat-main") == ""


def test_only_signed_entries_verify():
    assert unit_input.verified_turn(TEST_UNIT_IN_KEY, signed_turn({"prompt": "hi"})) == {"prompt": "hi"}
    for forged in _forgeries("hi"):
        assert unit_input.verified_turn(TEST_UNIT_IN_KEY, forged) is None
    assert unit_input.verified_turn("", signed_turn({"prompt": "hi"})) is None


def _malformed(prompt: str) -> list:
    """Entries anyone holding the service connection could write, none of them a signature."""
    good = signed_turn({"prompt": prompt})
    deep = "[" * 100000 + "]" * 100000
    return [
        {"turn": good["turn"], "sig": "é" * 64},                       # non-ASCII text
        {"turn": good["turn"], "sig": b"\xff" * 64},                   # non-ASCII bytes
        {"turn": good["turn"], "sig": good["sig"].upper()},            # not the encoded form
        {"turn": good["turn"], "sig": good["sig"][:-1]},               # short
        {"turn": good["turn"], "sig": good["sig"] + "0"},              # long
        {"turn": good["turn"], "sig": 7},                               # not text
        {"turn": b"\xff\xfe", "sig": good["sig"]},                      # not UTF-8
        {"turn": deep, "sig": unit_input._sig(TEST_UNIT_IN_KEY, deep)},  # signed, too deep to parse
        ["turn", "sig"],                                                # not a mapping
    ]


def test_a_malformed_entry_is_a_refusal_never_an_exception():
    for fields in _malformed("hi"):
        assert unit_input.verified_turn(TEST_UNIT_IN_KEY, fields) is None
    assert unit_input.verified_turn("not-hex", signed_turn({"prompt": "hi"})) is None


def test_the_serve_loop_passes_over_a_malformed_entry_and_runs_the_next():
    inbox = [(f"{i}-0", f) for i, f in enumerate(_malformed("injected"), start=1)]
    inbox.append(("99-0", signed_turn({"prompt": "mine"})))
    s = FakeStream(inbox=inbox)
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={"session": {"ref": "x"}}, idle_ms=10)
    assert _ran(s) == ["re:mine"]


def test_the_boot_drain_passes_over_a_malformed_entry():
    stream = BootStream([("5-0", _malformed("boot")[0]), ("6-0", signed_turn({"prompt": "queued"}))])
    ran = []

    def turn(prompt):
        ran.append(prompt)
        return iter(())

    serve(stream, out_topic="o", in_topic="i", turn=turn,
          start={"entrypoint": {"inline": "hello", "nonce": "n0"}}, idle_ms=10)
    assert ran == ["hello", "queued"]


def test_the_serve_loop_runs_only_its_own_signed_messages():
    inbox = [(f"{i}-0", f) for i, f in enumerate(_forgeries("injected"), start=1)]
    inbox.append(("9-0", signed_turn({"prompt": "mine"})))
    s = FakeStream(inbox=inbox)
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={"session": {"ref": "x"}}, idle_ms=10)
    assert _ran(s) == ["re:mine"]


def test_a_forged_stop_does_not_end_the_worker():
    s = FakeStream(inbox=[("1-0", {"turn": json.dumps({"type": "stop"})}), ("2-0", signed_turn({"prompt": "go on"}))])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={"session": {"ref": "x"}}, idle_ms=10)
    assert _ran(s) == ["re:go on"]


def test_a_worker_without_a_key_runs_no_stream_entry(monkeypatch):
    monkeypatch.delenv(unit_input.KEY_ENV)
    s = FakeStream(inbox=[("1-0", signed_turn({"prompt": "queued"}))])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={"entrypoint": {"inline": "first"}}, idle_ms=10)
    assert _ran(s) == ["re:first"]


def test_the_boot_drain_skips_forgeries():
    """At boot the worker runs everything already waiting — the signed entries only."""
    stream = BootStream([("5-0", _forgeries("boot")[0]), ("6-0", signed_turn({"prompt": "queued"}))])
    ran = []

    def turn(prompt):
        ran.append(prompt)
        return iter(())

    serve(stream, out_topic="o", in_topic="i", turn=turn,
          start={"entrypoint": {"inline": "hello", "nonce": "n0"}}, idle_ms=10)
    assert ran == ["hello", "queued"]


def test_a_forgery_is_never_steered_into_a_running_turn():
    s = CursorStream(preloaded=[("5-0", signed_turn({"prompt": "hello"}))])

    class Steering:
        injected: list = []

        def midturn_enabled(self):
            return True

        def inject_user_message(self, text):
            self.injected.append(text)
            return True

    harness = Steering()

    def active_turn(prompt):
        if prompt == "hello":
            s.entries.append(("6-0", _forgeries("steer")[0]))
            s.entries.append(("7-0", signed_turn({"prompt": "real steer", "nonce": "n7"})))
        yield {"type": "message-delta", "text": f"re:{prompt}"}

    serve(s, out_topic="o", in_topic="i", turn=active_turn,
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10, harness=harness)
    assert "steer" not in harness.injected
    assert "re:steer" not in _ran(s)


def test_agent_api_signs_what_it_delivers_with_the_key_it_hands_the_worker():
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(internal_api_secret=SECRET), rt, _FakeIdentity(),
                            warm_stream=_WarmFake())
    wid = d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    key = env[unit_input.KEY_ENV]
    assert key == unit_input.unit_key(SECRET, wid)
    (entry,) = d._warm_stream.streams[f"unit:{wid}:in"]
    assert unit_input.verified_turn(key, entry[1])["prompt"] == "hi"
    assert unit_input.verified_turn(OTHER_UNIT_KEY, entry[1]) is None


def test_the_key_never_reaches_the_model_harness(monkeypatch):
    monkeypatch.setenv(unit_input.KEY_ENV, "aa" * 32)
    assert unit_input.KEY_ENV not in harness_subprocess_env()


def test_the_contract_vector_is_this_module():
    """unit.v1's InputVector, minted here and re-derived in Node by the contract's validate.mjs."""
    from pathlib import Path

    golden = (Path(__file__).resolve().parents[1] / "contracts" / "unit.v1" / "golden"
              / "InputVector.chat-follow-up.json")
    v = json.loads(golden.read_text())
    assert unit_input.unit_key(v["secret"], v["unit_id"]) == v["key"]
    entry = {"turn": v["turn"], "sig": v["sig"]}
    assert unit_input.verified_turn(v["key"], entry) == json.loads(v["turn"])
    assert unit_input.signed_entry(v["key"], json.loads(v["turn"])) == entry
