"""The service door's listen address (Lite binds it to loopback; elsewhere it follows HOST)."""
from gateway.service_door import door_host


def test_unset_follows_the_edge_host():
    assert door_host("0.0.0.0", {}) == "0.0.0.0"


def test_set_wins():
    assert door_host("0.0.0.0", {"VEXA_GATEWAY_SERVICE_DOOR_HOST": "127.0.0.1"}) == "127.0.0.1"
