"""The post-meeting grounding gate reads words in any script (app.dev 2026-10-18).

`_phrases` matched `[a-z0-9']` only, so a Russian or Portuguese transcript had no phrases at all and
every report on it — including ones quoting it verbatim — was refused as "not grounded in the
transcript", twice, on every non-English meeting."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows_steps.meeting import grounded_in  # noqa: E402

RU = ("Это я разговариваю.\nя разговариваю это никто не будет\n"
      "так это я значит транскрипирую а теперь я возьму и сделаю такую как вы нужны устал")


def test_a_report_quoting_a_russian_transcript_is_grounded():
    note = ('A test call. dmitry said: "так это я значит транскрипирую а теперь я возьму". '
            "Nothing was decided.")
    assert grounded_in(note, RU)


def test_a_report_that_never_read_a_russian_transcript_is_not():
    note = "The team agreed the Q3 roadmap and Anna will send the pricing deck by Friday."
    assert not grounded_in(note, RU)


def test_portuguese_with_accents_is_read_as_words():
    pt = "então nós decidimos que a reunião começa amanhã às nove horas da manhã"
    assert grounded_in(f'Ela disse "nós decidimos que a reunião começa amanhã".', pt)
    assert not grounded_in("Nothing in common with the call at all, honestly speaking.", pt)


def test_a_transcript_with_nothing_quotable_cannot_refuse_a_report():
    fillers = "uh\nyou\nso\num\nУх.\nMm-hmm\nYeah\nок"
    assert grounded_in("A short mic check; nothing was said.", fillers)


def test_english_still_works_and_short_windows_still_do_not_pass():
    en = "we should ship the onboarding redesign before the conference next month"
    assert grounded_in('He said "ship the onboarding redesign before the conference".', en)
    assert not grounded_in("do you want to talk about something else entirely today", en)
