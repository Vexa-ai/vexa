from meeting_signals import brief as briefs
from meeting_signals.core import Caps, Listener, State
from meeting_signals.model import Line, Scored
from meeting_signals.policy import resolve
from meeting_signals.rules import matches, validate
from conftest import run


def test_rules_are_validated_and_dangerous_regexes_refused():
    rules = validate([{"kind": "keyword", "value": "rollback"}, {"kind": "person", "value": "Ana"},
                      {"kind": "regex", "value": "INC-[0-9]{4}"}, {"kind": "regex", "value": "(a+)+$"},
                      {"kind": "regex", "value": "(unclosed"}, {"kind": "shell", "value": "x"},
                      {"kind": "keyword", "value": "x" * 300}, "junk"])
    assert [r.kind for r in rules] == ["keyword", "person", "regex"]


def test_rule_matching():
    rules = validate([{"kind": "keyword", "value": "roll back"}, {"kind": "person", "value": "ana"},
                      {"kind": "regex", "value": r"INC-\d{4}"}, {"kind": "keyword", "value": "quiet", "wake": False}])
    assert run(matches(rules, text="we must ROLL BACK now", speaker="Bo"))
    assert not run(matches(rules, text="rolling backwards", speaker="Bo"))
    assert run(matches(rules, text="hello", speaker="Ana"))
    assert run(matches(rules, text="see INC-1234", speaker="Bo"))
    assert not run(matches(rules, text="keep quiet", speaker="Bo"))


def test_the_brief_carries_rules_and_a_model_tier_but_cannot_drop_being_addressed():
    b = briefs.normalize({"labels": [{"name": "decision", "wake": True}], "rules": [{"kind": "keyword", "value": "go"}],
                          "model_tier": "small"})
    assert b.model_tier == "small" and len(b.rules) == 1 and "addressed_to_vexa" in b.wake_labels
    assert briefs.normalize({"model_tier": "gpt-5-huge"}).model_tier == "cheapest"


def test_the_tier_picks_only_a_model_the_policy_allows():
    tiers = {"cheapest": {"adapter": "openai-compatible", "route": "small-q", "base_url": "http://l/v1", "secret_ref": "none"},
             "small": {"adapter": "jev", "route": "typesafe/jev-1.13", "secret_ref": "broker:signals/x"}}
    p, problems = resolve({"transcript_signals": "on", "model_signals": tiers})
    assert p.active and not problems
    assert p.model_for("small")["route"] == "typesafe/jev-1.13" and p.model_for("cheapest")["route"] == "small-q"
    one, _ = resolve({"transcript_signals": "on", "model_signals": tiers["small"]})
    assert one.model_for("small") == one.model_for("cheapest") == one.model


def test_a_rule_hit_wakes_at_once_without_a_model_call_even_past_the_wake_cap():
    calls = []

    async def score(asks, line, ctx):
        calls.append(line.segment_id)
        return Scored({k: 0.0 for k in asks}, 10, "m")
    b = briefs.normalize({"labels": [{"name": "decision", "wake": True}], "rules": [{"kind": "keyword", "value": "rollback"}]})
    lst = Listener({"kind": "chat", "id": "r1"}, scorer=score, caps=Caps(1, 1.0))
    st = State(wakes=1)
    ann, batch = run(lst.take(st, [Line("m1", "Bo", "plan a rollback", 0, 0)], b))
    assert calls == [] and ann[0]["model"] == "rule" and ann[0]["scores"] == {"addressed_to_vexa": 1.0}
    assert lst.due(st, b, batch) == "addressed"


def test_context_lines_are_never_buffered_or_scored():
    calls = []

    async def score(asks, line, ctx):
        calls.append([c.segment_id for c in ctx])
        return Scored({k: 0.0 for k in asks}, 10, "m")
    lst = Listener({"kind": "chat", "id": "r1"}, scorer=score, caps=Caps(30, 1.0))
    st = State()
    run(lst.take(st, [(Line("a1", "Vexa", "Here is the plan.", 0, 0), "context"), Line("m2", "Bo", "ok", 1, 1)],
                 briefs.DEFAULT))
    assert [b["segment_id"] for b in st.buffer] == ["m2"] and calls == [["a1"]]
