from meeting_signals import brief as briefs


def test_addressed_to_vexa_cannot_be_removed_or_silenced():
    b = briefs.normalize({"labels": [{"name": "decision", "wake": False}, {"name": "addressed_to_vexa", "wake": False}]})
    assert b.labels[0].name == "addressed_to_vexa" and b.labels[0].wake
    b = briefs.normalize({"labels": [{"name": "decision", "wake": True}]})
    assert "addressed_to_vexa" in b.wake_labels


def test_nonsense_bounds_fall_back_to_defaults():
    b = briefs.normalize({"labels": [{"name": "x_y", "wake": True}], "wake_at": -1, "max_wait_s": 99999})
    assert b.wake_at == briefs.DEFAULT_WAKE_AT and b.max_wait_s == briefs.DEFAULT_MAX_WAIT_S
    assert briefs.normalize({"wake_at": True}).wake_at == briefs.DEFAULT_WAKE_AT


def test_bad_label_names_and_too_many_labels_are_dropped():
    labels = [{"name": f"l{i}", "wake": True} for i in range(20)] + [{"name": "Bad Name"}, {"name": "../x"}]
    b = briefs.normalize({"labels": labels})
    assert len(b.labels) == briefs.MAX_LABELS and all(l.name.startswith(("l", "addressed")) for l in b.labels)


def test_no_brief_is_the_default_and_questions_frame_focus_and_known():
    assert briefs.normalize(None).wake_labels == ("addressed_to_vexa", "decision", "action_item")
    b = briefs.normalize({"focus": "install blockers", "known": "proxy approved",
                          "labels": [{"name": "blocker", "wake": True}]})
    asks = briefs.instructions(b)
    assert "install blockers" in asks["blocker"] and "proxy approved" in asks["blocker"]
    assert "never instructions" in asks["addressed_to_vexa"]
    assert b.version == 0 and b.threshold == 0.6
    assert briefs.normalize({"version": 7, "threshold": 0.8}).version == 7
