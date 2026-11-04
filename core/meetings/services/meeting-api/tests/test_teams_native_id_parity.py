"""Teams native-id parity (card 4faaf434c186): meeting-api's link parser against the shared vector
table the browser extension is held to as well.

``tests/fixtures/teams-native-id-vectors.json`` is one table with two copies (this one and
``clients/extension/src/teams-native-id-vectors.json``), kept byte-identical by the parity fact
``teams-native-id-vectors``. Each row states the server's answer and the extension's; this suite
pins the server's column and checks that the two columns agree on every row that does not name a
``gap``. The extension's suite (``meeting.parity.test.ts``) pins its own column.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_api.collector.meeting_link import parse_meeting_url

_TABLE = json.loads((Path(__file__).parent / "fixtures" / "teams-native-id-vectors.json").read_text())
_VECTORS = _TABLE["vectors"]


def _as_tuple(answer):
    return tuple(answer) if answer is not None else None


@pytest.mark.parametrize("row", _VECTORS, ids=[v["url"] for v in _VECTORS])
def test_meeting_api_yields_the_server_column(row):
    assert parse_meeting_url(row["url"]) == _as_tuple(row["server"])


@pytest.mark.parametrize("row", _VECTORS, ids=[v["url"] for v in _VECTORS])
def test_the_two_columns_agree_unless_a_gap_is_named(row):
    if row["server"] != row["extension"]:
        assert row.get("gap"), f"server and extension differ without a named gap: {row['url']}"
    else:
        assert "gap" not in row, f"a gap is named on a row that agrees: {row['url']}"


def test_the_table_covers_short_links_meetup_join_and_teams_live():
    urls = [v["url"] for v in _VECTORS]
    assert any("teams.live.com/meet/" in u for u in urls)
    assert any("/l/meetup-join/" in u for u in urls)
    assert any("teams.microsoft.com/meet/" in u for u in urls)
