from meeting_signals import brief as briefs
from meeting_signals.scoring import addressed, batch_score, wake_reason

B = briefs.normalize({"labels": [{"name": "decision", "wake": True}, {"name": "action_item", "wake": True},
                                 {"name": "new_name", "wake": False}], "wake_at": 1.0, "max_wait_s": 60})


def test_batch_score_is_the_sum_of_per_label_maxima_over_wake_labels():
    rows = [{"decision": 0.9, "action_item": 0.1, "new_name": 1.0, "addressed_to_vexa": 0.0},
            {"decision": 0.3, "action_item": 0.6, "new_name": 1.0, "addressed_to_vexa": 0.1}]
    # decision max .9 + action max .6 + addressed max .1; new_name does not wake so it does not count
    assert abs(batch_score(rows, B.wake_labels) - 1.6) < 1e-9


def test_many_weak_lines_do_not_add_up_like_one_strong_one():
    weak = [{"decision": 0.1}] * 50
    assert batch_score(weak, ["decision"]) == 0.1


def test_unscored_lines_add_nothing():
    assert batch_score([{}, {}], B.wake_labels) == 0.0


def test_addressed_wakes_at_once_whatever_the_total():
    rows = [{"addressed_to_vexa": 0.5}]
    assert addressed(rows)
    assert wake_reason(scores=rows, running=0.0, brief=B, buffered=1, since=0.0, now=1.0) == "addressed"
    assert not addressed([{"addressed_to_vexa": 0.49}])


def test_threshold_then_max_wait_then_nothing():
    assert wake_reason(scores=[], running=1.0, brief=B, buffered=3, since=0.0, now=1.0) == "threshold"
    assert wake_reason(scores=[], running=0.2, brief=B, buffered=3, since=0.0, now=60.0) == "max_wait"
    assert wake_reason(scores=[], running=0.2, brief=B, buffered=3, since=0.0, now=59.0) is None


def test_an_empty_buffer_never_wakes():
    assert wake_reason(scores=[{"addressed_to_vexa": 1}], running=5, brief=B, buffered=0, since=None, now=1e9) is None
