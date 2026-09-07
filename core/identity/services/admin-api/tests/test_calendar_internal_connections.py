"""The internal calendar-configs projection — what meeting-api's sweep is told about a user.

Pure over ``admin_api.app.calendars`` (no database, no docker): the flattening decides which
connection syncs, which one retires rows, and which single connection may claim the meetings the
pre-plural singular feed imported. Each of those is load-bearing for the sweep on the other side
of the hop, so they are pinned here rather than only through the HTTP edge.
"""
from admin_api.app.calendars import _legacy_id, internal_connections

ICS = "https://calendar.google.com/calendar/ical/private-abc123/basic.ics"
ICS_2 = "https://outlook.office365.com/owa/calendar/private-fedcba/calendar.ics"
USER = 42


def _connection(cid, **overrides):
    return {"id": cid, "name": "Calendar", "ics_url": ICS, "auto_join": True,
            "bot_name": "Vexa", "enabled": True, **overrides}


def test_live_connection_carries_its_feed_and_policy():
    (cfg,) = internal_connections({"calendar_connections": [_connection("work")]}, USER)
    assert cfg["user_id"] == USER
    assert cfg["calendar_id"] == "work"
    assert cfg["ics_url"] == ICS
    assert cfg["auto_join"] is True
    assert "paused" not in cfg and "deleted" not in cfg


def test_disabled_connection_crosses_as_a_paused_tombstone():
    """A paused calendar must leave no meeting armed. It reaches the sweep with no feed URL and
    a `paused` marker, which the sweep reads as an empty feed — its managed rows retire, and
    re-enabling re-imports them on the next pass."""
    data = {"calendar_connections": [
        _connection("work"),
        _connection("personal", ics_url=ICS_2, enabled=False),
    ]}

    configs = {cfg["calendar_id"]: cfg for cfg in internal_connections(data, USER)}

    assert configs["personal"]["paused"] is True
    assert configs["personal"]["deleted"] is False
    assert "ics_url" not in configs["personal"]
    assert configs["work"]["ics_url"] == ICS  # the live one is untouched


def test_deleted_connection_still_crosses_as_a_tombstone():
    data = {"calendar_connections": [_connection("work", enabled=False, deleted=True)]}
    (cfg,) = internal_connections(data, USER)
    assert cfg["deleted"] is True
    assert "ics_url" not in cfg


def test_exactly_one_connection_claims_the_pre_plural_rows():
    """Meetings imported before plural calendars carry a bare `calendar_uid` and name no
    connection. Marking more than one connection `legacy` would let a second calendar's sweep
    read those rows as its own and cancel them, so the marker sits on the connection the
    singular feed migrated into — here the synthesized one — and on no other."""
    data = {"calendar_connections": [
        _connection("personal", ics_url=ICS_2),
        _connection(_legacy_id(USER)),
    ]}

    configs = internal_connections(data, USER)

    assert [cfg["calendar_id"] for cfg in configs if cfg.get("legacy")] == [_legacy_id(USER)]


def test_first_connection_claims_them_when_nothing_was_synthesized():
    data = {"calendar_connections": [_connection("work"), _connection("personal", ics_url=ICS_2)]}
    configs = internal_connections(data, USER)
    assert [cfg["calendar_id"] for cfg in configs if cfg.get("legacy")] == ["work"]


def test_unmigrated_singular_feed_is_its_own_legacy_connection():
    (cfg,) = internal_connections({"calendar_ics_url": ICS, "calendar_auto_join": False}, USER)
    assert cfg["calendar_id"] == _legacy_id(USER)
    assert cfg["legacy"] is True
    assert cfg["auto_join"] is False


def test_no_connections_yields_nothing():
    assert internal_connections({}, USER) == []
