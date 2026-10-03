"""Settings → Models: the extra-header field's normalization and masking (Vexa-ai/vexa#1667).

Pure functions, no database — the DB-backed round trip lives in test_model_settings.py behind
testcontainers. The rule these lock: the stored form is ALWAYS ``Name: Value`` lines, because that
is the form the claude CLI's ``ANTHROPIC_CUSTOM_HEADERS`` parses and agent-api passes it through
verbatim to both call shapes. A header that silently does not arrive is the failure this field
exists to end, so anything unconvertible is a 422 naming the expected shape rather than a stored
string that turns into no header at all.
"""
import pytest
from fastapi import HTTPException

from admin_api.app.main import _mask_headers, _normalize_headers, _validate_config_fields


def test_line_form_round_trips():
    assert _normalize_headers("x-provider-session: abc\nx-route: eu") == \
        "x-provider-session: abc\nx-route: eu"
    assert _normalize_headers("x-a: 1\r\n\r\nx-b: 2") == "x-a: 1\nx-b: 2"  # blank lines dropped


def test_json_object_is_converted_at_the_edge():
    assert _normalize_headers('{"x-provider-session": "abc"}') == "x-provider-session: abc"


def test_a_value_may_contain_a_colon():
    assert _normalize_headers("x-callback: https://host:8080/cb") == \
        "x-callback: https://host:8080/cb"


@pytest.mark.parametrize("bad, why", [
    ("garbage", "not 'Name: Value'"),
    ("{not json}", "JSON object"),
    ('["a"]', "not 'Name: Value'"),
    ("x-a:", "non-empty"),
    (": 1", "non-empty"),
    ("x a: 1", "valid HTTP header name"),
    ("x-a: héader", "ASCII"),
])
def test_unconvertible_values_are_422_not_silently_stored(bad, why):
    with pytest.raises(HTTPException) as exc:
        _normalize_headers(bad)
    assert exc.value.status_code == 422 and why in exc.value.detail


def test_validate_normalizes_through_the_shared_rulebook():
    cleaned = _validate_config_fields(
        {"headers": '{"x-a": "1"}', "harness_base_url": "https://messages.example.com"},
        kind="models")
    assert cleaned == {"headers": "x-a: 1", "harness_base_url": "https://messages.example.com"}


def test_harness_base_url_is_url_validated_like_base_url():
    with pytest.raises(HTTPException) as exc:
        _validate_config_fields({"harness_base_url": "not-a-url"}, kind="models")
    assert exc.value.status_code == 422


def test_masking_keeps_names_and_hides_values():
    assert _mask_headers("x-provider-session: supersecretvalue\nx-route: eu") == \
        "x-provider-session: ********alue\nx-route: ********"
    assert _mask_headers("") is None
