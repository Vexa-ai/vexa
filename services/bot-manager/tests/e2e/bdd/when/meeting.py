"""
WHEN helpers for meeting domain actions - these wrap the system under test.
"""
import asyncio


async def a_bot_exits_the_meeting(client, session_id: str, exit_code: int = 0, reason: str = "meeting_ended"):
    """WHEN a bot exits the meeting and reports its status."""
    response = await asyncio.to_thread(
        client.post,
        "/bots/internal/callback/exited",
        json={
            "connection_id": session_id,
            "exit_code": exit_code,
            "reason": reason
        }
    )
    return response


async def meeting_is_requested_to_stop(client, platform: str, native_meeting_id: str, auth_headers: dict):
    """WHEN a meeting is requested to be stopped by the user."""
    response = await asyncio.to_thread(
        client.delete,
        f"/bots/{platform}/{native_meeting_id}",
        headers=auth_headers
    )
    return response


async def a_bot_is_requested_for_meeting(client, meeting_data: dict, auth_headers: dict):
    """WHEN a bot is requested to join a meeting."""
    response = await asyncio.to_thread(
        client.post,
        "/bots",
        json=meeting_data,
        headers=auth_headers
    )
    return response


async def meeting_data_is_refreshed_from_database(db_session, meeting):
    """WHEN meeting data is refreshed from the database."""
    await db_session.refresh(meeting)
    return meeting