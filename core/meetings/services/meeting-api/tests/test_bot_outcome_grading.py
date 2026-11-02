"""The product's bot-outcome grading table (ADR 0052 §10): every rule pinned by one case."""

import datetime as dt

import pytest

from meeting_api.lifecycle.outcome import CASES, CLASSES, PENDING, classify

NOW = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize(("row", "want"), CASES)
def test_rule(row, want):
    assert tuple(classify(row, now=NOW)) == want


def test_every_terminal_class_is_one_of_five():
    graded = {classify(row, now=NOW).cls for row, _ in CASES} - {PENDING}
    assert graded <= set(CLASSES)
    assert "ours" in graded and "unknown" in graded
