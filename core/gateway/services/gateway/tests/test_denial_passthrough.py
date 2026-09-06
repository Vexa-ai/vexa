"""The edge must not edit a refusal it did not author.

`POST /bots` can come back from meeting-api as a 403 whose `detail` carries, besides the machine
fields (`code`, `reason`, `decision_id`), the deciding service's own plain words for the caller
(`message`) and one link they can open (`action_url`). None of that vocabulary exists in this
package and none of it should: the gateway's whole contribution to a refusal is to not touch it.

That is already how `_forward` behaves — body and status go back VERBATIM — so these tests add no
behaviour. They exist because the property is invisible: nothing in the proxy mentions denials, so
a future normaliser, error-envelope or "helpful" 4xx rewrite at this seam would silently strip the
one part of the answer a caller could have acted on, and every test here would still pass without
them. A field-shaped assertion at the edge is the tripwire.

Every string below is fixture-local — no plan, price, currency or URL of ours appears in this repo.
"""
import json

import pytest
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}

SPAWN = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}

DENIAL = {
    "code": "service_not_allowed",
    "reason": "a_reason_this_build_has_never_heard_of",
    "decision_id": "decision-fixture-77",
    "message": "This account cannot start bots right now. Open the account page to fix it.",
    "action_url": "https://example.invalid/account",
}


def _client(status_code, body):
    downstream = FakeDownstream(status_code=status_code, body=body)
    app = create_app(FakeAuthorizer(), downstream, FakeRedis())
    return TestClient(app), downstream


@pytest.mark.parametrize("status_code", [403, 429])
def test_refusal_detail_reaches_the_caller_whole(status_code):
    """Status and every field of `detail`, byte for byte — including the two the edge cannot read."""
    client, _ = _client(status_code, {"detail": DENIAL})
    r = client.post("/bots", headers=AUTH, json=SPAWN)
    assert r.status_code == status_code
    assert r.json()["detail"] == DENIAL


def test_the_words_survive_specifically():
    """Spelled out separately from the equality above so a partial-copy regression names itself."""
    client, _ = _client(403, {"detail": DENIAL})
    detail = client.post("/bots", headers=AUTH, json=SPAWN).json()["detail"]
    assert detail["message"] == DENIAL["message"]
    assert detail["action_url"] == DENIAL["action_url"]
    # And the reason is passed through as-is: there is no vocabulary at this seam to fall out of.
    assert detail["reason"] == "a_reason_this_build_has_never_heard_of"


def test_refusal_without_words_is_not_padded():
    """The edge adds nothing when the deciding service said nothing — no null keys, no defaults."""
    bare = {k: DENIAL[k] for k in ("code", "reason", "decision_id")}
    client, _ = _client(403, {"detail": bare})
    detail = client.post("/bots", headers=AUTH, json=SPAWN).json()["detail"]
    assert detail == bare
    assert "message" not in detail
    assert "action_url" not in detail


def test_refusal_body_is_byte_identical():
    """Strongest form of the same claim: the response body is the upstream body, unreserialised."""
    upstream = {"detail": DENIAL}
    client, _ = _client(403, upstream)
    r = client.post("/bots", headers=AUTH, json=SPAWN)
    assert r.content == json.dumps(upstream).encode()
