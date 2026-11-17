"""P20 policy `guest_prelobby_failure` (founder, 17 Nov 2026): person > organisation > default."""
import pytest
from fastapi import HTTPException

from admin_api.app import person_settings as ps
from admin_api.app import platform_settings as pls


def test_default_is_retry_signed_in_with_nothing_set():
    assert pls.guest_prelobby_failure(None, {}) == "retry_signed_in"
    assert pls.guest_prelobby_failure("", None) == "retry_signed_in"


def test_the_organisation_decides_when_the_person_has_not():
    assert pls.guest_prelobby_failure("", {"guest_prelobby_failure": "fail"}) == "fail"


def test_the_person_wins_over_the_organisation():
    assert pls.guest_prelobby_failure("guest_retry", {"guest_prelobby_failure": "fail"}) == "guest_retry"


def test_a_stored_value_outside_the_vocabulary_reads_the_next_tier():
    assert pls.guest_prelobby_failure("bogus", {"guest_prelobby_failure": "fail"}) == "fail"


def test_person_door_accepts_the_three_values_and_empty_and_refuses_others():
    for v in ("retry_signed_in", "guest_retry", "fail", ""):
        out = ps.apply({}, {"guest_prelobby_failure": v})
        assert out["person_settings"]["guest_prelobby_failure"] == v
    with pytest.raises(ps.Refused):
        ps.apply({}, {"guest_prelobby_failure": "always"})
    assert ps.read({})["guest_prelobby_failure"] == ""


def test_organisation_door_cleans_its_writes():
    assert pls.SETTINGS["meet_join"].clean({"guest_prelobby_failure": "FAIL"}) == {"guest_prelobby_failure": "fail"}
    with pytest.raises(HTTPException):
        pls.SETTINGS["meet_join"].clean({"guest_prelobby_failure": "always"})
    with pytest.raises(HTTPException):
        pls.SETTINGS["meet_join"].clean({"other": "x"})
