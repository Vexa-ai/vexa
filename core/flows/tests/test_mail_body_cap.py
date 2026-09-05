"""THE MAIL BODY CAP IS THE DECLARED KEY — `VEXA_FLOWS_MAIL_BODY_MAX`, read where the body is cut.

R-B12. The key was declared in `flows_config.DECLARED` (class `defaulted`, default `4000`, *"how
much of an inbound body may enter an agent prompt, inside the untrusted block"*), promised to
operators in `core/flows/README.md` (*"length-capped (`VEXA_FLOWS_MAIL_BODY_MAX`)"*) — and read by
nothing. The only cap in the intake was a literal `[:2000]` inside `strip_quotes`.

That is the exact shape `test_config_declaration`'s declared⊆read direction exists to catch, and
what makes it worth a file of its own is what it costs on both sides: an operator who SETS the key
gets no error and no effect, and an operator who READS the documentation is told a number that is
not the number. A configuration surface that answers neither way is worse than one that does not
exist.

Three properties, and the third is the one a silent `[:n]` cannot have:

  1. the cap is the declared value, resolved PER CALL (the poller is long-lived);
  2. the quote strip runs BEFORE the cut, so a reply does not spend its budget on our own text;
  3. a body that was cut SAYS SO — an elided message that just stops reads as a message that just
     stopped, which is a different fact about the sender.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flows_config as cfg  # noqa: E402
from flows_integrations import mailbox  # noqa: E402


def test_the_key_is_declared_defaulted_at_four_thousand():
    klass, default, why = cfg.DECLARED["VEXA_FLOWS_MAIL_BODY_MAX"]
    assert (klass, default) == ("defaulted", "4000")
    assert "body" in why


def test_an_unset_key_is_the_declared_default_and_not_the_old_literal(monkeypatch):
    """The literal was 2000 and the declaration says 4000. Whichever number is right, an operator
    must not be able to read one and get the other."""
    monkeypatch.delenv("VEXA_FLOWS_MAIL_BODY_MAX", raising=False)
    assert mailbox.body_max() == 4000


def test_the_operator_value_is_what_actually_cuts(monkeypatch):
    """THE REGRESSION. Green on this tree only because the key reaches the cut; on the tree before
    it, `strip_quotes` returns 2000 characters whatever this is set to."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "50")
    out = mailbox.strip_quotes("x" * 500)
    assert out.startswith("x" * 50)
    assert "x" * 51 not in out


def test_a_value_that_is_not_a_number_is_the_default_never_a_crash(monkeypatch):
    """A poller mid-flight must not die of a typo in an env file."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "four thousand")
    assert mailbox.body_max() == 4000
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "0")
    assert mailbox.body_max() == 1, "a cap of zero would throw the whole message away"


def test_the_cut_is_read_per_call_not_bound_at_import(monkeypatch):
    """The door-at-import defect, one key along: this process has already imported the module."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "10")
    assert len(mailbox.strip_quotes("y" * 100).split("\n")[0]) == 10
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "20")
    assert len(mailbox.strip_quotes("y" * 100).split("\n")[0]) == 20


def test_a_body_under_the_cap_is_untouched_and_carries_no_marker(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "4000")
    assert mailbox.strip_quotes("  Just a short reply.\n") == "Just a short reply."


def test_an_elided_body_says_so_and_says_by_how_much(monkeypatch):
    """A silent cut is a message that ends mid-sentence, and a reader — a person or the agent
    reading the untrusted block — cannot tell that from a sender who stopped writing."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "100")
    out = mailbox.strip_quotes("z" * 350)
    assert "VEXA_FLOWS_MAIL_BODY_MAX" in out, "the marker names the control that did the cutting"
    assert "250" in out, "…and how many characters it dropped"
    assert out.startswith("z" * 100) and "z" * 101 not in out


def test_the_quotes_come_off_before_the_cut(monkeypatch):
    """ORDER, and it is the whole value of the cap on a real thread. A reply whose first screen is
    the mail it answers would otherwise spend the entire budget on text we sent ourselves."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "40")
    quoted = "\n".join([f"> {'q' * 80}"] * 20)
    body = f"Yes, Tuesday works.\n\n{quoted}"
    out = mailbox.strip_quotes(body)
    assert out.startswith("Yes, Tuesday works.")
    assert "q" not in out


def test_the_admitted_reply_carries_the_capped_text(monkeypatch):
    """END TO END on the field that actually travels: `mail.reply`'s `text` ref is the value a
    prompt is built from, and `strip_quotes` is the only thing between an inbox and it."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_BODY_MAX", "64")
    text = mailbox.strip_quotes("a" * 4000)
    assert len(text.split("\n")[0]) == 64
    assert "3936" in text
