"""A MODEL SWITCH KEEPS THE CONVERSATION (2026-10-13, app.dev, Company mode): after the person picked
another model mid-chat, "go ahead" was answered with "This chat has no earlier message". A pick of a
model on another harness starts a new worker whose harness cannot resume the previous harness's
session; it must be seeded from the chat's own turn records instead. The same harness resumes."""
from __future__ import annotations

from worker import engine


def _world(tmp_path, monkeypatch):
    seen = []

    def fake_run(work, prompt, harness, session=None, **kw):
        seen.append({"harness": harness.name, "resume": session, "prompt": prompt})
        reply = "Noted: ORCHID." if "ORCHID" in prompt.split("<!--")[-1] else "ok"
        yield {"type": "message-delta", "text": reply}
        yield {"type": "done", "ok": True, "reply": reply, "sessionId": f"{harness.name}-sid"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts",
                        lambda: [{"slug": "desk-1", "path": str(tmp_path), "write": True, "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "grant_tools_access", lambda paths: None)
    monkeypatch.setattr(engine, "show_tools", lambda paths: None)
    return seen


def _h(name):
    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0
    H.name = name
    return H()


def _turn(tmp_path, text, harness):
    return list(engine.run_turn_over_workspace(tmp_path, text, harness=harness, commit=False, session="main"))


def test_another_harness_is_seeded_with_the_conversation_and_never_resumes_a_foreign_session(tmp_path, monkeypatch):
    seen = _world(tmp_path, monkeypatch)
    _turn(tmp_path, "The code word is ORCHID.", _h("claude-code"))
    _turn(tmp_path, "What was the code word?", _h("openai-agent"))
    second = seen[-1]
    assert second["resume"] is None, "a session id another harness minted is never resumed"
    assert "The code word is ORCHID." in second["prompt"] and "Noted: ORCHID." in second["prompt"]
    # …and back: the first harness's own session is the older one, so it is seeded too, not resumed
    _turn(tmp_path, "Again?", _h("claude-code"))
    assert seen[-1]["resume"] is None
    assert "What was the code word?" in seen[-1]["prompt"]


def test_the_same_harness_with_another_model_resumes_and_is_not_seeded(tmp_path, monkeypatch):
    seen = _world(tmp_path, monkeypatch)
    _turn(tmp_path, "The code word is ORCHID.", _h("claude-code"))
    _turn(tmp_path, "What was the code word?", _h("claude-code"))
    assert seen[-1]["resume"] == "claude-code-sid"
    assert "Noted: ORCHID." not in seen[-1]["prompt"]
