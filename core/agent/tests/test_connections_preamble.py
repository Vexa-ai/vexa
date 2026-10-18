"""Every dispatch names the connect verb and the meaning of each connection failure reason."""
from control_plane.routers.connections import INSTRUCTIONS
from worker.engine import connections_preamble


def test_the_preamble_names_connection_request_and_every_reason_agent_api_answers():
    text = connections_preamble()
    assert '`connection_request`' in text
    for reason in INSTRUCTIONS:
        assert f'`{reason}`' in text, reason
    assert 'Never answer that request by retrying the tool that failed' in text


def test_the_preamble_says_whose_error_it_is_and_how_links_are_made():
    """2026-10-13: the agent wrote `/mail/u/<address>/` links, Gmail answered them with its own
    "Temporary Error (404) … account temporarily unavailable" page, and the agent blamed Google; a
    later gateway 503 ("Authentication temporarily unavailable") it blamed on Google too, and it ran
    Bash against mail.google.com to "test" a link it cannot be signed in to."""
    text = connections_preamble()
    assert '`web_url`' in text and 'never build a Gmail or Calendar address' in text
    assert 'suspect the link first' in text
    assert '`vexa_sign_in_unavailable`' in text and "Vexa's own sign-in service" in text
    assert 'Google or Microsoft only when the failure says the provider returned it' in text
    assert 'never fetch' in text and 'mail.google.com' in text
