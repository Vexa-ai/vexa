import pytest

from meeting_signals import brief as briefs
from meeting_signals.core import Caps, Listener, State
from meeting_signals.model import Line, ModelRefused, Scored
from conftest import run

SRC = {"kind": "meeting", "id": "42"}


def lines(*texts):
    return [Line(f"s{i}", "A", t, float(i), float(i) + 1) for i, t in enumerate(texts)]


def scorer(calls, table=None):
    async def score(asks, line, ctx):
        calls.append(line.segment_id)
        return Scored({k: (table or {}).get(line.text, {}).get(k, 0.0) for k in asks}, 1000, "fake")
    return score


def test_lines_are_scored_buffered_and_annotated_with_the_source():
    calls = []
    lst = Listener(SRC, scorer=scorer(calls, {"we decide": {"decision": 0.9}}), caps=Caps(30, 1.0), now=lambda: 100.0)
    st = State()
    ann, batch = run(lst.take(st, lines("hello", "we decide"), briefs.DEFAULT))
    assert calls == ["s0", "s1"] and len(ann) == 2 and ann[0]["source"] == SRC
    assert abs(st.score - 0.9) < 1e-9 and len(st.buffer) == 2 and st.since == 100.0
    assert lst.due(st, briefs.DEFAULT, batch) is None                  # 0.9 < wake_at 1.0
    run(lst.take(st, lines("we decide"), briefs.DEFAULT))
    assert lst.due(st, briefs.DEFAULT, []) == "threshold"
    data = lst.wake_data(st, briefs.DEFAULT, "threshold", {"meeting_row": "42"})
    assert data["source"] == SRC and data["meeting_row"] == "42" and len(data["buffer"]) == 3
    Listener.woke(st)
    assert (st.buffer, st.score, st.wakes, st.seq) == ([], 0.0, 1, 1)


def test_the_wake_cap_stops_model_calls_before_they_are_made():
    calls = []
    lst = Listener(SRC, scorer=scorer(calls), caps=Caps(2, 10.0))
    st = State(wakes=2)
    ann, _ = run(lst.take(st, lines("a", "b"), briefs.DEFAULT))
    assert calls == [] and ann == [] and len(st.buffer) == 2 and st.refusals == {"wakes_cap": 2}
    st.score = 99.0
    assert lst.due(st, briefs.DEFAULT, []) is None                       # no threshold wake past the cap
    assert lst.due(st, briefs.DEFAULT, [{"addressed_to_vexa": 1.0}]) == "addressed"   # being asked still wakes


def test_the_cost_cap_is_checked_on_the_estimate_before_each_call():
    calls = []
    # ~each call estimates a few hundred tokens; 2 USD per Mtok; cap leaves room for about one call
    lst = Listener(SRC, scorer=scorer(calls), caps=Caps(30, 0.0012, 2.0))
    st = State()
    run(lst.take(st, lines("one", "two", "three"), briefs.DEFAULT))
    assert len(calls) == 1 and st.refusals.get("cost_cap") == 2
    assert st.cost_usd <= 0.0012 + 0.002


def test_a_refused_answer_leaves_the_line_unscored_but_buffered():
    async def bad(asks, line, ctx):
        raise ModelRefused("garbage")
    st = State()
    ann, batch = run(Listener(SRC, scorer=bad, caps=Caps(30, 1.0)).take(st, lines("x"), briefs.DEFAULT))
    assert ann == [] and batch == [] and st.buffer[0]["scores"] == {} and st.refusals == {"model_refused": 1}


def test_context_is_the_preceding_lines_capped_at_24():
    seen = []

    async def score(asks, line, ctx):
        seen.append([c.segment_id for c in ctx])
        return Scored({k: 0.0 for k in asks}, 1, "f")
    lst = Listener(SRC, scorer=score, caps=Caps(30, 100.0))
    lst.seed_context(lines(*[f"old{i}" for i in range(30)]))
    run(lst.take(State(), [Line("n1", "A", "new", 99, 100), Line("n2", "A", "new2", 100, 101)], briefs.DEFAULT))
    assert len(seen[0]) == 24 and seen[1][-1] == "n1" and len(seen[1]) == 24



# app.dev 2026-10-16: meeting 146 spent its 12 wakes in four minutes and the sensor went silent for
# the rest of the call. The cap is a cost guard per HOUR of meeting, not a lifetime budget.

def test_the_wake_cap_is_per_hour_of_meeting():
    calls = []
    clock = [1000.0]
    lst = Listener(SRC, scorer=scorer(calls), caps=Caps(2, 10.0), now=lambda: clock[0])
    st = State(wakes=2, opened=1000.0)
    run(lst.take(st, lines("a"), briefs.DEFAULT))
    assert calls == []                                                   # hour one: capped
    clock[0] = 1000.0 + 3601
    run(lst.take(st, lines("b"), briefs.DEFAULT))
    assert calls == ["s0"]                                               # hour two: two more


def test_past_the_cap_one_quiet_wake_still_goes_so_the_room_can_be_told():
    lst = Listener(SRC, scorer=None, caps=Caps(2, 10.0), now=lambda: 2000.0)
    st = State(wakes=2, opened=1000.0, buffer=[{"segment_id": "x", "speaker": "A", "text": "t",
                                               "start": 0.0, "scores": {}}], since=1000.0)
    assert lst.due(st, briefs.DEFAULT, []) == "max_wait"                 # the wake agent-api refuses and says so
    st.wakes = 3
    assert lst.due(st, briefs.DEFAULT, []) is None                       # and only that one


def test_being_addressed_past_the_cap_wakes_a_bounded_number_of_times():
    """Every addressed wake is an agent turn. Past the hour's cap a line naming Vexa still wakes —
    up to the cap again — and then nothing, however often it is said (it used to be unbounded)."""
    lst = Listener(SRC, scorer=scorer([]), caps=Caps(2, 10.0), now=lambda: 0.0)
    st = State(wakes=0, opened=0.0, buffer=[{"segment_id": "s", "speaker": "A", "text": "Vexa?",
                                              "start": 0.0, "scores": {}}])
    asked = [{"addressed_to_vexa": 1.0}]
    woke = 0
    for _ in range(20):
        st.buffer = st.buffer or [{"segment_id": "s", "speaker": "A", "text": "Vexa?", "start": 0.0, "scores": {}}]
        if lst.due(st, briefs.DEFAULT, asked) == "addressed":
            Listener.woke(st)
            woke += 1
    assert woke == 4 and st.wakes == 4                        # the cap (2), then the cap again (2)
    st.buffer = [{"segment_id": "s", "speaker": "A", "text": "Vexa?", "start": 0.0, "scores": {}}]
    assert lst.due(st, briefs.DEFAULT, asked) is None
