"""subject-ticket.v1 key wiring in compose (offline — no stack, no docker).

Each ticket producer signs with its own Ed25519 key and the gateway's service door verifies under
the public halves. The property that makes a ticket mean anything is WHO HOLDS WHICH FILE: a
producer's private key mounted anywhere else lets that service speak for the producer, and a
private key reaching flows-api (which answers people's requests) or the gateway would let them mint
the tickets the door is there to demand. These pin the mounts and the one-shot that writes them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"

PRIVATE = {
    "subject-ticket-meetings-key": {"meeting-api"},
    "subject-ticket-agent-key": {"agent-api"},
    "subject-ticket-mail-key": {"flows-mailbox", "flows-worker"},
}
PUBLIC = "subject-ticket-verify-keys"
ONE_SHOT = "subject-ticket-keys"


def _services() -> dict:
    """Every service block, as text — the same line-wise read `gate:config-contract` uses."""
    text = COMPOSE.read_text().split("\n")
    start = text.index("services:") + 1
    out, name, buf = {}, None, []
    for line in text[start:]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        m = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if m:
            if name:
                out[name] = "\n".join(buf)
            name, buf = m.group(1), []
            continue
        buf.append(line)
    if name:
        out[name] = "\n".join(buf)
    return out


def _mounts(block: str) -> dict:
    """{volume: (target, read_only)} of a service block's named-volume mounts."""
    out = {}
    for m in re.finditer(r"^\s+-\s+([a-z][a-z0-9-]*):(/[^:\s]+)(:ro)?\s*$", block, re.M):
        out[m.group(1)] = (m.group(2), bool(m.group(3)))
    return out


def _env(block: str) -> dict:
    return dict(re.findall(r"^\s+-\s+([A-Z][A-Z0-9_]*)=(\S*)\s*$", block, re.M))


SERVICES = _services()


@pytest.mark.parametrize("volume", sorted(PRIVATE))
def test_each_private_key_is_mounted_by_its_producer_alone(volume):
    holders = {s for s, b in SERVICES.items() if volume in _mounts(b) and s != ONE_SHOT}
    assert holders == PRIVATE[volume], holders
    for s in holders:
        assert _mounts(SERVICES[s])[volume] == ("/run/vexa-subject-ticket", True)


def test_each_producer_mounts_no_other_producers_key():
    for volume, producers in PRIVATE.items():
        for s in producers:
            others = set(PRIVATE) - {volume}
            assert not others & set(_mounts(SERVICES[s])), (s, others & set(_mounts(SERVICES[s])))


def test_the_gateway_mounts_the_public_halves_only_and_read_only():
    gw = _mounts(SERVICES["gateway"])
    assert gw[PUBLIC] == ("/run/vexa-subject-ticket-verify", True)
    assert not set(PRIVATE) & set(gw)
    assert _env(SERVICES["gateway"])["VEXA_SUBJECT_TICKET_KEYS_DIR"] == "/run/vexa-subject-ticket-verify"
    holders = {s for s, b in SERVICES.items() if PUBLIC in _mounts(b) and s != ONE_SHOT}
    assert holders == {"gateway"}, holders


def test_flows_api_mounts_no_ticket_key():
    """flows-api answers requests people make; it carries tickets, it never mints one. The mail key
    setting is the same on every lane (one configuration) but its FILE is not mounted here."""
    mounts = set(_mounts(SERVICES["flows-api"]))
    assert not (set(PRIVATE) | {PUBLIC}) & mounts, mounts


def test_the_producers_read_the_key_where_it_is_mounted():
    for s in ("meeting-api", "agent-api"):
        assert _env(SERVICES[s])["VEXA_SUBJECT_TICKET_KEY_FILE"] == "/run/vexa-subject-ticket/key.pem"
    for s in ("flows-api", "flows-mailbox", "flows-worker"):
        assert _env(SERVICES[s])["VEXA_FLOWS_MAIL_TICKET_KEY_FILE"] == "/run/vexa-subject-ticket/key.pem"


def test_the_one_shot_writes_every_pair_offline_and_hands_the_keys_to_their_uid():
    block = SERVICES[ONE_SHOT]
    assert "network_mode: none" in block and 'user: "0:0"' in block
    assert set(_mounts(block)) == set(PRIVATE) | {PUBLIC}
    for issuer in ("meetings", "agent", "mail"):
        assert f"subject-ticket-{issuer}-key:/keys/{issuer}" in block
    assert "for i in meetings agent mail" in block
    assert "identity_token.py keygen /keys/$$i/key.pem /keys/public/$$i.pem" in block
    # meeting-api and the flows lanes run as uid 10001; agent-api as root
    assert "chown 10001:10001 /keys/meetings/key.pem /keys/mail/key.pem" in block


@pytest.mark.parametrize("service", ["meeting-api", "agent-api", "gateway"])
def test_the_key_holders_start_after_the_one_shot(service):
    block = SERVICES[service]
    m = re.search(rf"^      {ONE_SHOT}:\n\s+condition: (\S+)", block, re.M)
    assert m and m.group(1) == "service_completed_successfully", service
