"""The Python outbound URL guard against the shared vector table — the same rows the TypeScript
guard (`@vexa/transcribe-whisper` url-guard.ts) is held to, from a byte-identical copy
(`scripts/parity.json`, fact `outbound-url-vectors`). The guard is vendored verbatim into every
Python image, so holding this copy holds them all."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_api.webhooks import ssrf

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "outbound-url-vectors.json").read_text())


@pytest.mark.parametrize("row", VECTORS["addresses"], ids=lambda r: r["addr"] or "<empty>")
def test_address(row):
    assert ssrf.is_blocked_ip(row["addr"]) is row["blocked"]


@pytest.mark.parametrize("row", VECTORS["hostnames"], ids=lambda r: r["host"] or "<empty>")
def test_hostname(row):
    assert ssrf.is_blocked_hostname(row["host"]) is row["blocked"]


@pytest.mark.parametrize("row", VECTORS["urls"], ids=lambda r: r["url"])
def test_url(row):
    def resolver(host):
        assert row["resolved"] is not None, f"{row['url']} is a literal address and needs no lookup"
        return list(row["resolved"])

    try:
        ssrf.validate_url(row["url"], resolver=resolver)
        refused = False
    except ssrf.SSRFError:
        refused = True
    assert refused is row["refused"]
