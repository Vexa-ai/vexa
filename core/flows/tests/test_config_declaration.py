"""THE CONFIG CONTRACT, both directions — nothing read that is not declared, nothing declared that
is not read.

The rate limits this branch adds are configuration, and configuration that lives only in the code
that reads it is how `gate:config-contract` came to be green over less than half the seam (B7):
81 literal env reads outside every scan list, no declared→read direction, and one key plumbed
through four surfaces and consumed by nothing (R-E18). `core/flows` is not one of the five adopted
`config.v1` services and adopting it is that structural item, not this branch — so this file is
the same assertion at this brick's own scale, which is what makes the adoption a transcription
when it happens.

Both directions, and only one of them is the usual one:

  read ⊆ declared   catches the key somebody added in a hurry — the 18 that R-A11 found;
  declared ⊆ read   catches the key whose READER was deleted, which is the shape that leaves an
                    operator setting a value that reaches nothing and never says so.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flows_config as cfg  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
# The declaration itself names every key, so scanning it would make both directions vacuous.
DECLARATION = SRC / "flows_config.py"
KEY = re.compile(r"\bVEXA_[A-Z0-9_]*[A-Z0-9]\b")
# An env name read through a MODULE CONSTANT is invisible to a literal scan, and R-A11 found two of
# exactly those in the agent tier — "read through a variable so no literal scanner can see them".
# `common.py` has the pattern here too since F95 (`INTERNAL_SECRET_ENV = "INTERNAL_API_SECRET"`), so
# the scan reads the convention rather than pretending the keys are not there.
ENV_CONST = re.compile(r"^[A-Z][A-Z0-9_]*_ENV(?:_DEPRECATED)?\s*=\s*(.+)$", re.M)
NAME_IN = re.compile(r"[\"\']([A-Z][A-Z0-9_]{3,})[\"\']")


def _sources():
    return [f for f in sorted(SRC.rglob("*.py")) if f != DECLARATION]


def _read_keys() -> dict:
    """Every env key the brick's source names — `VEXA_*` literals plus the names held by its
    `*_ENV` / `*_ENV_DEPRECATED` constants — with the files each appears in."""
    found: dict = {}
    for f in _sources():
        text = f.read_text()
        names = set(KEY.findall(text))
        for rhs in ENV_CONST.findall(text):
            names.update(NAME_IN.findall(rhs))
        for name in names:
            found.setdefault(name, set()).add(f.relative_to(SRC).as_posix())
    return found


def test_the_scan_sees_a_name_held_in_a_constant_not_only_a_literal():
    """The guard on the guard: if this stops working, the reverse assertions above go quiet
    instead of going red, which is the failure mode the whole file exists to prevent."""
    read = _read_keys()
    assert "INTERNAL_API_SECRET" in read, "an env name read through a *_ENV constant went unseen"
    assert "flows_steps/common.py" in read["INTERNAL_API_SECRET"]


def test_every_key_the_code_reads_is_declared():
    undeclared = {k: sorted(v) for k, v in _read_keys().items() if k not in cfg.DECLARED}
    assert not undeclared, f"env keys read but not declared in flows_config.DECLARED: {undeclared}"


def test_every_declared_key_is_actually_read_somewhere():
    read = set(_read_keys())
    orphans = sorted(k for k in cfg.DECLARED if k not in read)
    assert not orphans, ("declared but read by nothing — either wire it or delete it, because an "
                         f"operator who sets it gets no error and no effect: {orphans}")


def test_every_declaration_says_its_class_its_default_and_why():
    for name, decl in cfg.DECLARED.items():
        assert len(decl) == 3, name
        klass, default, why = decl
        assert klass in ("required-explicit", "defaulted", "capability"), (name, klass)
        assert isinstance(why, str) and len(why) > 20, f"{name} has no usable why"
        if klass == "required-explicit":
            assert default is None, f"{name} is required-explicit and must not carry a default"
        if klass == "defaulted":
            assert default is not None, f"{name} is defaulted and must say what the default is"


def test_the_accessors_refuse_a_name_nobody_declared():
    """A typo must be a crash at the read, not a silent empty string — which is the failure mode
    every one of these keys had before there was a table."""
    import pytest
    for fn in (cfg.get, cfg.get_bool, cfg.get_int):
        with pytest.raises(KeyError) as e:
            fn("VEXA_FLOWS_MAIL_DOMAIN")           # the singular typo of a real key
        assert "not declared" in str(e.value)


def test_the_declared_default_is_what_an_unset_key_returns(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_MAIL_RATE_PER_SENDER", raising=False)
    assert cfg.get_int("VEXA_FLOWS_MAIL_RATE_PER_SENDER") == 12
    monkeypatch.setenv("VEXA_FLOWS_MAIL_RATE_PER_SENDER", "not a number")
    assert cfg.get_int("VEXA_FLOWS_MAIL_RATE_PER_SENDER") == 12, "a typo is the default, never a crash"
    monkeypatch.setenv("VEXA_FLOWS_MAIL_RATE_PER_SENDER", " 4 ")
    assert cfg.get_int("VEXA_FLOWS_MAIL_RATE_PER_SENDER") == 4


def test_the_two_keys_that_open_the_machine_are_required_explicit():
    """A weak default makes an unconfigured deployment look configured (R-B11, and R-A20 one lane
    over). These two are the ones that mint accounts, tokens and flows."""
    for name in ("VEXA_FLOWS_ADMIN_KEY", "VEXA_FLOWS_API_KEY", "INTERNAL_API_SECRET"):
        assert cfg.DECLARED[name][0] == "required-explicit", name
    # The two deprecated spellings of the internal secret (F95) are capability-classed on purpose:
    # a deployment that sets NEITHER them nor the canonical name is refused by the canonical key's
    # own required-explicit row, and a deployment that sets one of them is warned and honoured for
    # one release. Declaring them makes their removal a deletion here rather than an archaeology.
    for legacy in ("VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET"):
        assert cfg.DECLARED[legacy][0] == "capability", legacy
        assert "DEPRECATED" in cfg.DECLARED[legacy][2]


def test_the_mail_policy_keys_are_declared_together():
    """The control this branch adds is five keys, and a half-declared control is the one an
    operator turns on wrong."""
    for name in ("VEXA_FLOWS_MAIL_DOMAINS", "VEXA_FLOWS_MAIL_QUARANTINE_REPLY",
                 "VEXA_FLOWS_MAIL_RATE_PER_SENDER", "VEXA_FLOWS_MAIL_RATE_GLOBAL",
                 "VEXA_FLOWS_MAIL_RATE_WINDOW_S", "VEXA_FLOWS_MAIL_BODY_MAX"):
        assert name in cfg.DECLARED
