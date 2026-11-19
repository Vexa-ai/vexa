"""The write-back pre-pass, against every junk name the app.dev friction reports of 2026-10-13 quote.

After a turn, the pre-pass (`engine.writeback_candidates`) lists names from the turn that no desk
has a page for, and the phase asks the agent to write a page for each. On 2026-10-13 it proposed,
verbatim: a routine's own format placeholder ("YYYY-MM-DD HH"), fragments cut mid-word ("OpenAI
Realt", "Agent Chrom", "Discord Developer Port", "URL Gener"), UI labels in bold ("Reset Token",
"Client Secret", "Message Content", "New Application"), a token-format example ("Bot MTI"), box
labels from a fenced diagram ("OT CONTAINER", "AGENT ADAP"), sentence starts ("Next I'm"), an HTTP
reason phrase ("Service Unavailable"), a name split at a non-ASCII letter ("Escola Jo" out of
"Jardim-Escola João de Deus de Belas"), components ("Second Chromium", "Realtime API"), and an
instruction's emphasis ("TWO LINES"). It also offered a name again on the next turn after the agent
had skipped it.

Fewer, correct proposals: zero is a fine answer. The positive controls at the bottom are the names
the same turns DID need pages for, and they must survive every filter.
"""
from __future__ import annotations

import json

from shared.marks import flow_mark
from worker import engine
from worker.worker import serve
from workspaces.shared import entities as E


def _mounts(tmp_path):
    return [{"slug": "d", "path": str(tmp_path), "write": True}]


def _cands(tmp_path, *texts, **kw):
    return engine.writeback_candidates(list(texts), _mounts(tmp_path), **kw)


# ── the corpus: every quoted junk name, inside the kind of text it was cut from ──────────────────

ROUTINE_PROMPT = (flow_mark("agent_run", "user-2-weather-lisbon")
                  + "Get the Lisbon weather and report the time as `YYYY-MM-DD HH:MM`. Say it in TWO "
                    "LINES.")

CORPUS = {
    "YYYY-MM-DD HH": "Logged at YYYY-MM-DD HH:MM as the routine asks, and `YYYY-MM-DD HH:MM` again.",
    "TWO LINES": "Here it is in TWO LINES, as asked.",
    "Service Unavailable": "Open-Meteo answered 503 Service Unavailable, so there is no reading.",
    "Bad Gateway": "The proxy said 502 Bad Gateway twice.",
    "Next I'm": "Next I'm updating the queue's ages and footer.",
    "Nothing I'm": "Nothing I'm saying is new here.",
    "And I'd": "And I'd say the same again.",
    "New Application": "In the portal click **New Application** and give it a name.",
    "Reset Token": "Then press **Reset Token** and copy what it shows.",
    "Client Secret": "Keep the **Client Secret** out of the chat.",
    "Message Content": "Turn on **Message Content** under Privileged Gateway Intents.",
    "URL Generator": "Open the __URL Generator__ and tick the bot scope.",
    "View Channels": "Grant View Channels and Message History to the bot role.",
    "Channel ID": "Right-click the channel and copy the Channel ID.",
    "Application ID": "The Application ID sits on the General Information tab.",
    "Bot MTI": "Your header reads `Bot MTIxMzQ1Njc4OTAxMjM0NTY3OA.GhIjKl` with your own token.",
    "Bot MTIxMzQ1Njc4OTAxMjM0NTY3OA": "It should start Bot MTIxMzQ1Njc4OTAxMjM0NTY3OA and go on.",
    "OT CONTAINER": ("```\n┌──────────────────┐    ┌───────────────┐\n│ OpenAI Realtime  │ -> │ BOT CONTAINER │\n"
                     "│ AGENT ADAPTER    │    └───────────────┘\n└──────────────────┘\n```"),
    "BOT CONTAINER": "+-----------------+\n| BOT CONTAINER   |\n+-----------------+",
    "AGENT ADAPTER": "┌───────────────┐\n│ AGENT ADAPTER │\n└───────────────┘",
    "Second Chromium": "A Second Chromium runs beside the first one for the agent.",
    "Realtime API": "It talks to the Realtime API over a websocket.",
    "Agent Chromium": "| Part | What |\n|---|---|\n| Agent Chromium | the second browser |",
    "Discord Developer Portal": "Open the Discord Developer Portal first.",
    "Escola Jo": "A reply came from Jardim-Escola João de Deus de Belas this morning.",
    "Deus de Belas": "A reply came from Jardim-Escola João de Deus de Belas this morning.",
}


def test_no_junk_name_from_the_corpus_is_proposed(tmp_path):
    bad = {}
    for junk, text in CORPUS.items():
        out = _cands(tmp_path, text)
        if any(c == junk or c.startswith(junk) or junk.startswith(c) for c in out
               if c != "Jardim-Escola João de Deus de Belas"):
            bad[junk] = out
    assert bad == {}, bad


def test_a_routine_prompt_is_not_a_source_of_names(tmp_path):
    """The routine's own instruction text is machinery; its placeholder and its emphasis are not
    things the person or the agent named."""
    out = engine.writeback_candidates(engine.writeback_sources(ROUTINE_PROMPT, ["Sunny, 21C."]),
                                      _mounts(tmp_path))
    assert out == [], out


def test_the_person_s_own_words_are_still_a_source(tmp_path):
    composed = "folded context about Quill Harbour Partners\n<!--vexa:user-input-below-->who is Robin Vale?"
    sources = engine.writeback_sources(composed, ["Nobody I know yet."])
    assert "Robin Vale" in engine.writeback_candidates(sources, _mounts(tmp_path))
    # the folded context before the sentinel is not the person's words
    assert "Quill Harbour Partners" not in engine.writeback_candidates(sources, _mounts(tmp_path))


# ── streamed chunks are one reply, not many ──────────────────────────────────────────────────────

def test_a_name_split_across_two_stream_chunks_is_read_whole(tmp_path):
    sources = engine.writeback_sources("hi", ["It runs on Northwind Realt", "ime Labs and on Discord Developer Port",
                                              "al pages."])
    out = engine.writeback_candidates(sources, _mounts(tmp_path))
    assert "Northwind Realt" not in out and "Discord Developer Port" not in out, out
    assert "Northwind Realtime Labs" in out, out


def test_a_fence_split_across_chunks_is_still_masked(tmp_path):
    chunks = ["Here is the shape:\n\n```\n│ OpenAI Realtime │ ", "-> │ BOT CONTAINER │\n```\n\nDone."]
    out = engine.writeback_candidates(engine.writeback_sources("draw it", chunks), _mounts(tmp_path))
    assert out == [], out


def test_an_unclosed_fence_masks_to_the_end(tmp_path):
    assert _cands(tmp_path, "Shape:\n```\n│ AGENT ADAPTER │ Second Box Label\n") == []


def test_the_serve_loop_hands_the_phase_whole_names(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    monkeypatch.setattr(engine, "active_mounts", lambda: _mounts(tmp_path))
    got = []

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "Robin Vale works with OpenAI Realt"}
        yield {"type": "message-delta", "text": "ime Partners now."}
        yield {"type": "done", "reply": "", "sessionId": "s1"}

    def writeback(cands):
        got.append(list(cands))
        yield {"type": "done", "reply": "", "sessionId": "s1"}

    class S:
        out = []

        def xadd(self, name, fields):
            self.out.append(fields)
            return str(len(self.out))

        def xread(self, *_a, **_k):
            return []

    serve(S(), out_topic="out", in_topic="in", turn=turn,
          start={"entrypoint": {"inline": "who is Robin Vale?"}}, idle_ms=1, writeback=writeback)
    assert got, "the phase never ran"
    assert "OpenAI Realt" not in got[0], got
    assert "OpenAI Realtime Partners" in got[0], got


# ── Unicode ──────────────────────────────────────────────────────────────────────────────────────

def test_a_name_with_non_ascii_letters_is_one_name():
    out = E.candidate_names("A reply came from Jardim-Escola João de Deus de Belas this morning.",
                            mask_linked=False)
    assert "Jardim-Escola João de Deus de Belas" in out, out
    assert not any(c in ("Escola Jo", "Deus de Belas") for c in out), out


def test_accented_capitals_open_a_name():
    out = E.candidate_names("We met Élodie Durand and Øystein Ås at Zürich Örlikon Bank.", mask_linked=False)
    assert "Élodie Durand" in out and "Øystein Ås" in out, out


# ── the session remembers what it already offered ────────────────────────────────────────────────

def test_a_name_offered_once_is_not_offered_again_in_the_same_session(tmp_path):
    offered: set = set()
    first = _cands(tmp_path, "Robin Vale called.", offered=offered)
    assert first == ["Robin Vale"]
    assert _cands(tmp_path, "Robin Vale called again.", offered=offered) == []
    # a different name still comes through
    assert _cands(tmp_path, "Robin Vale and Nora Quill called.", offered=offered) == ["Nora Quill"]


# ── positive controls: the names those same turns needed ─────────────────────────────────────────

def test_real_people_and_companies_still_come_through(tmp_path):
    text = ("Alex Example built the demo. Sam Example sent the agreement from Northwind Labs. "
            "Robin Vale joined from Quill Harbour Partners.")
    out = _cands(tmp_path, text)
    for name in ("Alex Example", "Sam Example", "Northwind Labs", "Robin Vale", "Quill Harbour Partners"):
        assert name in out, (name, out)


def test_the_phase_prompt_says_what_gets_no_page():
    p = engine.writeback_prompt(["Robin Vale"])
    assert "UI label" in p and "no page" in p.lower()
    assert json.dumps(p)  # plain text
