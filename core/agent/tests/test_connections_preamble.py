"""Every dispatch names the connect verb and the meaning of each connection failure reason."""
from control_plane.routers.connections import INSTRUCTIONS
from worker.engine import connections_preamble


def test_the_preamble_names_connection_request_and_every_reason_agent_api_answers():
    text = connections_preamble()
    assert '`connection_request`' in text
    for reason in INSTRUCTIONS:
        assert f'`{reason}`' in text, reason
    assert 'Never answer that request by retrying the tool that failed' in text
