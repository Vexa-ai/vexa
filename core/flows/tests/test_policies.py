"""THE RULES ARE A FILE, AND THE FILE AND THE CODE CANNOT DRIFT.

Four claims, in the order they would fail:

  P1  The seeded `behavior/global/POLICIES.md` declares exactly the keys `policies.DEFAULTS` knows,
      with exactly those values. A key in one and not the other is either a rule nobody can answer
      or a control that silently does nothing — the failure `production.py`'s own docstrings call
      out for inert params.
  P2  Resolution order: flow param, then the file, then env, then the baked default. An absent,
      empty or unreadable file changes nothing.
  P3  The disclosure is DERIVED. At the defaults it composes, byte for byte, to the sentence the
      founder wrote (decision 21) and `mailtext.VISIBILITY_SENTENCE` still carries — and it CHANGES
      when a rule that makes it untrue changes, which is the whole reason it is not a constant.
  P4  The rules reach the steps: who the follow-up is mailed to, and whether it is sent at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flows import Registry
from flows_defs import production
from flows_steps import mailtext, policies

# <repo>/core/flows/src/flows_steps/policies.py -> parents[4] is the repo root, the same anchor
# `test_attendee_mail_shape`'s drift gate uses for `behavior/mail`.
SEEDED = Path(policies.__file__).resolve().parents[4] / "behavior" / "global" / "POLICIES.md"


# ── P1 · the file and the table are the same set ─────────────────────────────────────────────────

def test_the_seeded_policy_file_declares_every_rule_the_code_knows():
    if not SEEDED.is_file():
        pytest.skip(f"no seed at {SEEDED}")
    declared = policies.front_matter(SEEDED.read_text(encoding="utf-8"))
    missing = [k for k in policies.DEFAULTS if k not in declared]
    assert not missing, (f"{SEEDED} does not carry {missing} — a rule the code applies that the "
                         f"admin cannot see or answer")


def test_the_policy_file_declares_no_rule_the_code_does_not_read():
    if not SEEDED.is_file():
        pytest.skip(f"no seed at {SEEDED}")
    declared = set(policies.front_matter(SEEDED.read_text(encoding="utf-8")))
    extra = declared - set(policies.DEFAULTS) - {"kind", "profile"}
    assert not extra, (f"{SEEDED} declares {sorted(extra)}, which nothing reads — an inert row a "
                       f"deployment can still set is a control that silently does nothing")


def test_the_seeded_values_are_the_defaults():
    """The file a person reads and the defaults the code applies say the same thing."""
    if not SEEDED.is_file():
        pytest.skip(f"no seed at {SEEDED}")
    seeded = policies.resolve(SEEDED.read_text(encoding="utf-8"))
    for key, default in policies.DEFAULTS.items():
        assert seeded[key] == default, f"{key}: the seed says {seeded[key]!r}, the code {default!r}"
    assert seeded["profile"] == "default"
    assert seeded["problems"] == []


def test_the_seeded_file_names_the_two_profiles_the_founder_named():
    if not SEEDED.is_file():
        pytest.skip(f"no seed at {SEEDED}")
    body = SEEDED.read_text(encoding="utf-8")
    for name in ("bank", "studio"):
        assert f"`{name}`" in body, f"the {name} profile is in the code and not on the page"
    assert set(policies.PROFILES) == {"default", "bank", "studio"}


# ── P2 · resolution ──────────────────────────────────────────────────────────────────────────────

def test_no_file_is_the_defaults():
    assert policies.resolve(None)["external_participants"] is True
    assert policies.resolve("")["transcript_retention_days"] == policies.FOREVER


def test_a_file_with_no_front_matter_declares_nothing():
    assert policies.resolve("# Policies\n\nsome prose")["open_web"] is True


def test_an_unclosed_fence_is_not_front_matter():
    assert policies.resolve("---\nopen_web: off\n\nprose")["open_web"] is True


def test_an_explicit_answer_wins():
    assert policies.resolve("---\nopen_web: off\n---\n")["open_web"] is False
    assert policies.resolve("---\nnewcomer_reads_history: yes\n---\n")["newcomer_reads_history"]


def test_an_empty_row_is_not_an_answer():
    """`attendee_domains:` with nothing after it is the shape an unanswered row has."""
    out = policies.resolve("---\nattendee_domains:\nopen_web: off\n---\n")
    assert out["attendee_domains"] == ()
    assert out["open_web"] is False


def test_the_bank_profile_is_the_founders_bank():
    out = policies.resolve("---\nprofile: bank\n---\n")
    assert out["external_participants"] is False
    assert out["bot_joins_mixed_meetings"] is False
    assert out["recording_retention_days"] == 0
    assert out["open_web"] is False
    assert out["report_to_participants"] is True, "the loop levers stay on in a bank"
    assert out["prep_and_invite_mail"] is True


def test_the_studio_profile_keeps_recordings():
    assert policies.resolve("---\nprofile: studio\n---\n")["recording_retention_days"] == \
        policies.FOREVER


def test_an_explicit_row_wins_over_the_profile():
    out = policies.resolve("---\nprofile: bank\nopen_web: on\n---\n")
    assert out["open_web"] is True
    assert out["external_participants"] is False


def test_an_unknown_profile_falls_back_and_says_so():
    out = policies.resolve("---\nprofile: casino\n---\n")
    assert out["profile"] == "default"
    assert any("casino" in p for p in out["problems"])


def test_a_value_this_module_cannot_read_falls_back_and_says_so():
    out = policies.resolve("---\nopen_web: maybe\n---\n")
    assert out["open_web"] is True
    assert any("open_web" in p for p in out["problems"])


def test_domains_are_normalised():
    out = policies.resolve("---\nattendee_domains: @Acme.com, partner.example , \n---\n")
    assert out["attendee_domains"] == ("acme.com", "partner.example")


def test_retention_reads_days_and_forever():
    out = policies.resolve("---\ntranscript_retention_days: 30\nrecording_retention_days: ∞\n---\n")
    assert out["transcript_retention_days"] == 30
    assert out["recording_retention_days"] == policies.FOREVER


def test_read_never_raises_when_global_cannot_be_reached(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("the agent domain is not deployed here")
    monkeypatch.setattr(policies, "ws_file", boom)
    assert policies.read("57")["report_to_participants"] is True


def test_read_with_no_uid_asks_nobody(monkeypatch):
    monkeypatch.setattr(policies, "ws_file",
                        lambda *_a, **_k: pytest.fail("read the store with no subject"))
    assert policies.read("")["open_web"] is True


def test_read_takes_the_file_the_admin_wrote(monkeypatch):
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: "---\nprofile: bank\n---\n")
    assert policies.read("57")["external_participants"] is False


def test_param_prefers_the_flow_over_the_file(monkeypatch):
    monkeypatch.setattr(policies, "ws_file",
                        lambda *_a, **_k: "---\nopen_web: off\nexternal_participants: off\n---\n")

    class _Flow:
        def param(self, key, default=None):
            return {"open_web": True}.get(key, default)

    class _Ctx:
        flow = _Flow()
        refs = {"uid": "57"}

    assert policies.param(_Ctx(), "open_web") is True
    assert policies.param(_Ctx(), "external_participants") is False


# ── P3 · the derived disclosure ──────────────────────────────────────────────────────────────────

def test_at_the_defaults_it_is_the_founders_sentence():
    """The composition and `mailtext.VISIBILITY_SENTENCE` are the same bytes.

    That constant stays where it is: `scripts/parity.json`'s `visibility-sentence` fact anchors one
    of its four sites on that assignment, and this change touches no site."""
    assert policies.visibility_sentence(policies.resolve(None)) == mailtext.VISIBILITY_SENTENCE


def test_turning_the_desk_rule_off_changes_what_attendees_are_told():
    said = policies.visibility_sentence(policies.resolve("---\nagent_reads_desk: off\n---\n"))
    assert "visible to the company's agents" not in said
    assert said.startswith(policies.LOCALITY_CLAUSE)


def test_retention_shows_up_in_the_sentence():
    said = policies.visibility_sentence(policies.resolve(
        "---\ntranscript_retention_days: 30\nrecording_retention_days: 7\n---\n"))
    assert "30 days" in said and "7 days" in said


def test_a_studio_that_keeps_recordings_says_so():
    said = policies.visibility_sentence(policies.resolve("---\nprofile: studio\n---\n"))
    assert said == mailtext.VISIBILITY_SENTENCE, \
        "keeping recordings on the same servers does not change where they are"


def test_a_written_data_statement_replaces_the_locality_clause():
    said = policies.visibility_sentence(policies.resolve(
        "---\ndata_statement: Everything stays in our Frankfurt rack.\n---\n"))
    assert said.startswith("Everything stays in our Frankfurt rack;")
    assert "visible to the company's agents" in said


def test_the_mail_renderer_fills_visibility_from_the_rules(monkeypatch):
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: "---\nagent_reads_desk: off\n---\n")
    _subject, body = mailtext.render("minutes-head", "57", {"title": "T", "when": "now"})
    assert "read only by an agent working for its own person" in body


def test_with_no_policy_file_the_mail_says_what_it_always_said(monkeypatch):
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: None)
    _subject, body = mailtext.render("minutes-head", "57", {"title": "T", "when": "now"})
    assert mailtext.VISIBILITY_SENTENCE in body


# ── P4 · the rules reach the steps ───────────────────────────────────────────────────────────────
#
# `_attendees` and `_followup_on` are closures inside `production.build`, so these go through the
# step itself, on the rig `test_attendee_mail_shape` already owns — one meeting, four addresses,
# three of them inside `bank.test` and `out@other.test` outside it.

from test_attendee_mail_shape import PRIOR, REFS, _ctx, _rig, teardown_function  # noqa: E402,F401
from test_link_loop import FakeScaffolds  # noqa: E402


@pytest.fixture(autouse=True)
def _scaffolds(monkeypatch):
    """Every send mints a scaffold first; this stands in for agent-api. Autouse fixtures do not
    travel with an imported rig, so this module declares its own."""
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())


def _who(monkeypatch, policy_file):
    monkeypatch.setattr(policies, "ws_file", lambda *_a, **_k: policy_file)
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx(dict(REFS), PRIOR))
    return sorted(m["to"] for m in ch.sent), out


def test_outside_the_domain_is_still_never(monkeypatch):
    """PRD §16.2 is not a rule on the page and did not become one: `out@other.test` is never
    mailed while the allow-list is the organiser's own domain, whatever `external_participants`
    says. That rule is the OTHER axis — a participant with no desk here."""
    to, out = _who(monkeypatch, "---\nexternal_participants: on\n---\n")
    assert to == ["ben@bank.test", "cara@bank.test"]
    assert out.result["sent"] == 2


def test_externals_off_mails_only_people_who_already_have_an_account(monkeypatch):
    monkeypatch.setattr(production, "platform_user_id",
                        lambda a: "12" if a == "ben@bank.test" else "")
    to, _ = _who(monkeypatch, "---\nexternal_participants: off\n---\n")
    assert to == ["ben@bank.test"]


def test_externals_on_asks_identity_nothing(monkeypatch):
    """The default path must not pay for a question whose answer it would ignore."""
    monkeypatch.setattr(production, "platform_user_id",
                        lambda a: pytest.fail("asked identity with the rule ON"))
    to, _ = _who(monkeypatch, "---\nexternal_participants: on\n---\n")
    assert to == ["ben@bank.test", "cara@bank.test"]


def test_the_bank_profile_writes_to_nobody_without_an_account(monkeypatch):
    monkeypatch.setattr(production, "platform_user_id", lambda a: "")
    to, _ = _who(monkeypatch, "---\nprofile: bank\n---\n")
    assert to == []


def test_identity_being_down_does_not_mail_wider(monkeypatch):
    def boom(_a):
        raise RuntimeError("admin-api is unreachable")
    monkeypatch.setattr(production, "platform_user_id", boom)
    to, _ = _who(monkeypatch, "---\nexternal_participants: off\n---\n")
    assert to == []


def test_the_allow_list_on_the_page_widens_what_inside_means(monkeypatch):
    to, _ = _who(monkeypatch, "---\nattendee_domains: bank.test, other.test\n---\n")
    assert to == ["ben@bank.test", "cara@bank.test", "out@other.test"]


def test_the_page_wins_over_the_env(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_ATTENDEE_DOMAINS", "other.test")
    to, _ = _who(monkeypatch, "---\nattendee_domains: bank.test\n---\n")
    assert to == ["ben@bank.test", "cara@bank.test"]


def test_the_env_still_holds_when_the_page_answers_nothing(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_ATTENDEE_DOMAINS", "other.test")
    to, _ = _who(monkeypatch, "---\nopen_web: on\n---\n")
    assert to == ["out@other.test"], "the env fallback is still read when the page answers nothing"


def test_report_to_participants_off_stops_the_fan_out(monkeypatch):
    to, out = _who(monkeypatch, "---\nreport_to_participants: off\n---\n")
    assert to == []
    assert out.result["followup"] == "off"


def test_the_per_meeting_opt_out_still_wins(monkeypatch):
    monkeypatch.setattr(policies, "ws_file",
                        lambda *_a, **_k: "---\nreport_to_participants: on\n---\n")
    reg, ch = _rig(monkeypatch)
    out = reg.steps["email_attendees"](_ctx({**REFS, "share_opt_out": True}, PRIOR))
    assert ch.sent == [] and out.result["followup"] == "off"
