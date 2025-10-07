"""
End-of-Meeting Replay Tests

This module allows replaying the end-of-meeting scenario for any existing meeting
by providing a session_uid (connection_id). Useful for:

- Debugging production issues
- Testing bot exit logic against real data  
- Validating webhook delivery
- Reproducing specific meeting scenarios

Usage:
    # Replay with mocked webhook
    pytest tests/e2e/test_meeting_replay.py --session-id="session-abc123"
    
    # Replay with real webhook delivery
    REPLAY_USE_REAL_WEBHOOKS=true pytest tests/e2e/test_meeting_replay.py --session-id="session-abc123"
"""
import os
import pytest
import pytest_asyncio
import respx
import httpx
from fastapi.testclient import TestClient
from main import app
from .bdd import Given, When, Then



@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sync_client():
    """Create a sync TestClient that can be used in async tests."""
    with TestClient(app) as client:
        yield client


@pytest.mark.describe('End of Meeting Processing')
class TestEndOfMeeting:
    """
    Test end-of-meeting processing using real meeting data from database.
    
    These tests verify the complete end-of-meeting workflow including task execution,
    webhook delivery, and database updates by replaying scenarios with a session_uid.
    """


    @pytest.mark.e2e
    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.slow
    @respx.mock
    @pytest.mark.it('replays bot exit tasks for given session ID with detailed results')
    async def test_replay_meeting_end_by_session_id(self, db_session, session_id):
        """
        Replay the complete end-of-meeting workflow for a specific session ID.
        
        Set SESSION_ID environment variable or use --session-id pytest option.
        """
        if not session_id:
            pytest.skip("No session ID provided. Use --session-id or SESSION_ID")
        
        from .bdd import Given, When, Then
        
        async with Given.di_container_is_initialized(testing=True):
            
            meeting, meeting_session = await Given.meeting_exists_for_session(db_session, session_id)
            logger = Given.a_python_logger(__name__)
            logger.info(f"Replaying end-of-meeting for session {session_id}")
            
            Given.meeting_data_is_valid(meeting, meeting_session, session_id, logger)
            
            webhook_request, webhook_url = Given.webhooks_are_mocked_for_meeting(meeting, respx, httpx)
            if webhook_url:
                logger.info(f"Webhook configured: {webhook_url} (mocked)")
            else:
                logger.info("No webhook configured")
            
            test_logger = Given.a_test_logger("run_all_tasks")
            result = await When.end_of_meeting_tasks_are_executed(meeting.id, db_session, logger=test_logger)
            
            When.task_results_are_logged(result, logger)
            
            Then.webhook_should_have_been_called(webhook_request, logger)
            Then.webhook_task_should_have_succeeded(result)
            
            logger.info("✅ End-of-meeting replay completed successfully")

    @pytest.mark.e2e
    @pytest.mark.webhook
    @pytest.mark.integration 
    @pytest.mark.slow
    @pytest.mark.it('processes end of meeting by session ID with configurable webhook delivery')
    async def test_end_of_meeting_by_session_id(self, sync_client, db_session, session_id):
        """
        Test the complete HTTP callback workflow that triggers task execution.
        
        This verifies the FastAPI endpoint, dependency injection, and background task execution
        by making real HTTP requests and checking database side effects.
        
        Set DRY_RUN=true to mock webhooks and display payload. Default uses real webhooks.
        """
        logger = Given.a_test_logger(__name__)

        if not session_id:
            error = "No session ID provided. Use --session-id or SESSION_ID"
            logger.error(error)
            pytest.skip(error)
        
        if not os.getenv('DB_HOST', ''):
            error = "no DB_HOST defined. Use DB_HOST env variable"
            logger.error(error)
            pytest.skip(error)

        if not os.getenv('DB_NAME', ''):
            error = "no DB_NAME defined. Use DB_NAME env variable"
            logger.error(error)
            pytest.skip(error)

        if not os.getenv('DB_PASSWORD', ''):
            error = "no DB_PASSWORD defined. Use DB_PASSWORD env variable"
            logger.error(error)
            pytest.skip(error)

        dry_run = os.getenv('DRY_RUN', '').lower() == 'true'
        
        # Apply mocking only in dry-run mode
        if dry_run:
            import respx
            respx_mock = respx.mock
            respx_mock.start()
        else:
            respx_mock = None
        
        try:
            meeting, meeting_session = await Given.meeting_exists_for_session(db_session, session_id)
            
            Given.meeting_data_is_valid(meeting, meeting_session, session_id, logger)
            
            initial_status = meeting.status
            initial_end_time = meeting.end_time
            
            webhook_request = None
            webhook_url = None
            
            if dry_run:
                webhook_request, webhook_url = Given.webhooks_are_mocked_for_meeting(meeting, respx, httpx)
                if webhook_url:
                    logger.info(f"Webhook mocked for dry-run: {webhook_url}")
            else:
                if not meeting.user or not meeting.user.data or not meeting.user.data.get('webhook_url'):
                    pytest.skip(f"No webhook URL configured for meeting {meeting.id} - cannot test real webhook delivery")
                
                webhook_url = meeting.user.data['webhook_url']
                logger.warning("⚠️  REAL WEBHOOK DELIVERY ENABLED - will send actual HTTP requests")
                logger.info(f"Webhook URL: {webhook_url}")
            
            logger.info(f"Testing HTTP callback for session {session_id}, meeting {meeting.id}")
            
            response = await When.a_bot_exits_the_meeting(sync_client, session_id, reason="e2e_http_test")
            
            assert response.status_code == 200, f"HTTP callback failed: {response.text}"
            response_data = response.json()
            
            logger.info(f"HTTP callback response: {response_data}")
            assert "meeting_id" in response_data
            assert response_data["meeting_id"] == meeting.id
            assert response_data["final_status"] == "completed"
            
            await When.meeting_data_is_refreshed_from_database(db_session, meeting)
            
            Then.meeting_should_be_marked_as_completed(meeting, initial_status, initial_end_time)
            
            if dry_run:
                Then.webhook_should_have_been_called(webhook_request, logger)
                
                # # Display webhook payload in dry-run mode
                # if webhook_request and webhook_request.calls:
                #     last_call = webhook_request.calls[-1]
                #     logger.info("=" * 60)
                #     logger.info("DRY RUN - CAPTURED WEBHOOK PAYLOAD")
                #     logger.info("=" * 60)
                #     logger.info(f"Webhook URL: {last_call.request.url}")
                #     logger.info("Payload that would be sent:")
                #     logger.info("-" * 40)
                    
                #     if hasattr(last_call.request, 'content') and last_call.request.content:
                #         try:
                #             import json
                #             payload = json.loads(last_call.request.content.decode('utf-8'))
                #             print(json.dumps(payload, indent=2, ensure_ascii=False))
                #         except (json.JSONDecodeError, UnicodeDecodeError):
                #             print(last_call.request.content.decode('utf-8', errors='replace'))
                    
                #     logger.info("-" * 40)
                #     logger.info("=" * 60)
            
            webhook_mode = "dry-run" if dry_run else "real"
            logger.info(f"✅ HTTP callback E2E test completed with {webhook_mode} webhook delivery - Meeting {meeting.id} processed successfully")
        
        finally:
            # Clean up respx mock if it was started
            if respx_mock:
                respx_mock.stop()



