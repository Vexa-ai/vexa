"""S03-2 — WHAT ATTENDEES ARE TOLD ABOUT WHERE THE WORDS LIVE IS WHAT THE OPERATOR CAN PROMISE.

The disclosure goes into the first mail a stranger gets from a deployment. Its default is composed
by the software, so every clause of it must hold on every supported install. "Recordings and
transcripts stay here" does not: hosted transcription receives the audio, helm with an external
object store keeps the recordings there, and a hosted model reads the transcript. So no default
says where they are kept — not the derived sentence, not the baked attendee head, not the files
that tell an administrator what their people will read, and not the provenance line's fallback.

An operator who can promise it says so in `data_statement`, and that promise reaches the mail
verbatim (the positive control), with the clause about a colleague's workspace still after it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import flows_defs.production as production
import flows_steps.mailtext as mailtext
from flows_steps import policies

from test_no_agents_degrade import (DONE_REFS, NO_AGENT, _forbid_the_agent_door, _production,
                                    _run_flow)

REPO = Path(policies.__file__).resolve().parents[4]

#: Every way of saying where recordings and transcripts are kept that a default has used.
LOCATION_CLAIMS = ("stay here", "stays here", "stay there", "stays there")

PROMISE = "Recordings and transcripts stay in our Frankfurt rack"

#: Every answer an admin can give that is not `data_statement`: the defaults, each profile, the
#: rules nothing enforces, and the four shapes the policies wizard recommends.
_ANSWERS = [
    "",
    "profile: default",
    "profile: bank",
    "profile: studio",
    "agent_reads_desk: off",
    "transcript_retention_days: 30\nrecording_retention_days: 0",
    "recording_retention_days: forever",
    "external_participants: off\nbot_joins_mixed_meetings: off\nreport_to_participants: on",
    "profile: bank\nreport_to_participants: off\norganizer_confirms_join: on\nopen_web: off",
    "external_participants: on\nreport_to_participants: on\nrecording_retention_days: forever",
    "report_to_participants: on\norganizer_confirms_join: on\nbot_joins_mixed_meetings: on",
]


def _says_where(text: str) -> list[str]:
    low = " ".join(text.split()).lower()
    return [c for c in LOCATION_CLAIMS if c in low]


@pytest.mark.parametrize("rows", _ANSWERS)
def test_no_default_sentence_says_where_recordings_and_transcripts_are_kept(rows):
    said = policies.visibility_sentence(policies.resolve(f"---\n{rows}\n---\n" if rows else None))
    assert _says_where(said) == [], f"{rows!r}: the default sentence claims {said!r}"
    assert "recordings" not in said.lower() and "transcripts" not in said.lower()
    assert said == mailtext.VISIBILITY_SENTENCE


def test_the_baked_sentence_is_the_two_clauses_the_software_backs():
    assert mailtext.VISIBILITY_SENTENCE == (
        "Vexa runs on this organisation's own servers; what you and your colleagues keep in your "
        "workspaces is visible to the company's agents.")


@pytest.mark.parametrize("rel", [
    "behavior/mail/attendee-head.md",
    "behavior/mail/README.md",
    "behavior/asks/setup-global.md",
    "behavior/global/STRUCTURE.md",
])
def test_no_copy_of_the_default_sentence_says_it(rel):
    """The sites that spell the default out: the attendee head (literal, never a token), the mail
    README, and the two places an administrator is told what their people will read."""
    text = (REPO / rel).read_text(encoding="utf-8")
    flat = " ".join(line.lstrip("> ") for line in text.splitlines())
    assert "recordings and transcripts stay here" not in " ".join(flat.split()), rel
    assert " ".join(mailtext.VISIBILITY_SENTENCE.split()) in " ".join(flat.split()), \
        f"{rel} no longer carries the default sentence at all"


def test_the_baked_attendee_head_does_not_say_it():
    head = mailtext.DEFAULTS["attendee-head"]
    assert _says_where(head) == []
    assert mailtext.VISIBILITY_SENTENCE in head


def _post_meeting_mail(monkeypatch, policy_file):
    reg, sent = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: policy_file)
    monkeypatch.delenv("VEXA_FLOWS_DATA_STATEMENT", raising=False)
    st, _ = _run_flow(reg, "meeting.completed", dict(DONE_REFS), NO_AGENT)
    assert st["status"] == "done", f"{st['step']}: {st['reason']}"
    assert len(sent) == 1, list(sent)
    return sent[0]["body"]


def test_the_provenance_fallback_does_not_say_it(monkeypatch):
    """`_provenance`'s last resort, when nothing names a `data_statement`: the mail every person
    gets after a meeting still says where Vexa runs, and nothing about where the words are kept."""
    body = _post_meeting_mail(monkeypatch, None)
    assert "Vexa runs on this organisation's own servers. These notes are visible" in body
    assert _says_where(body) == [], body


# ── the positive control: the operator's own promise still reaches the mail ──────────────────────

def test_an_operators_data_statement_reaches_the_rendered_mail_verbatim(monkeypatch):
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(policies, "ws_file",
                        lambda *_a, **_k: f"---\ndata_statement: {PROMISE}.\n---\n")
    _subject, body = mailtext.render("minutes-head", "57", {"title": "T", "when": "now"})
    assert f"{PROMISE}; {policies.DESK_VISIBLE_CLAUSE}." in body, body
    assert f"{PROMISE}.;" not in body and ".." not in body


def test_without_a_data_statement_the_locality_and_desk_clauses_remain(monkeypatch):
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: None)
    _subject, body = mailtext.render("minutes-head", "57", {"title": "T", "when": "now"})
    assert f"{policies.LOCALITY_CLAUSE}; {policies.DESK_VISIBLE_CLAUSE}." in body


@pytest.mark.parametrize("written", [PROMISE, PROMISE + "."])
def test_an_operators_data_statement_reaches_the_provenance_line_as_one_sentence(monkeypatch,
                                                                                 written):
    """Written with or without its full stop, the promise is one sentence in the mail."""
    body = _post_meeting_mail(monkeypatch, f"---\ndata_statement: {written}\n---\n")
    assert f"{PROMISE}. These notes are visible to the people who were in the meeting." in body, body
