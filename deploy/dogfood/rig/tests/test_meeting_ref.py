"""Agent meeting URL parsing preserves provider identity before dispatch."""
import pytest
import vexa_control_mcp as rig

@pytest.mark.parametrize("host", ["teams.microsoft.com", "teams.live.com", "gov.teams.microsoft.us", "teams.cloud.microsoft"])
def test_short_teams_links_include_16_digit_ids(host):
    assert rig._meeting_ref(f"https://{host}/meet/1234567890123456?p=fixture") == ("teams", "1234567890123456")

def test_enterprise_fragment():
    assert rig._meeting_ref("https://teams.microsoft.com/v2/?meetingjoin=true#/meet/1234567890123456?p=fixture") == ("teams", "1234567890123456")

@pytest.mark.parametrize("url", ["https://teams.microsoft.com.evil.example/meet/1234567890123456", "https://evil.example/?next=https://teams.live.com/meet/1234567890123456", "https://notteams.microsoft.com/meet/1234567890123456", "https://user@teams.microsoft.com/meet/1234567890123456", "http://teams.microsoft.com/meet/1234567890123456"])
def test_rejects_lookalikes_and_unsafe_urls(url):
    assert rig._meeting_ref(url)[0] is None
