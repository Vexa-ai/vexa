"""
Integration Tests for Send Webhook Functionality

This module contains comprehensive integration tests for the webhook delivery system
that triggers when a bot exits a meeting. The tests cover:

- Happy path webhook delivery with payload validation
- Error handling scenarios (HTTP errors, network failures, timeouts)
- Edge cases (missing data, configuration issues)  
- Complete integration flow with run_all_tasks
- Database interactions using real PostgreSQL via testcontainers

Test Categories:
- unit: Individual webhook function tests
- integration: Full workflow tests including database
- error_handling: Error scenarios and resilience
"""
import asyncio
import json
import pytest
import pytest_asyncio
import respx
import httpx
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

# Import the modules we're testing
from app.tasks.bot_exit_tasks.send_webhook import run as send_webhook_task
from app.tasks.bot_exit_tasks import run_all_tasks
from shared_models.models import Base, User, Meeting, MeetingSession
from shared_models.database import async_session_local

@pytest.mark.describe('Webhook integration')
class TestSendWebhookIntegration:
    """
    Integration Tests for Send Webhook on Bot Exit
    
    This test suite validates the complete webhook delivery pipeline when a bot
    exits a meeting. Tests are organized into the following categories:
    
    1. Happy Path Tests - Successful webhook delivery scenarios
    2. Error Handling Tests - HTTP errors, network failures, timeouts  
    3. Edge Case Tests - Missing data, configuration issues
    4. Integration Tests - Full workflow with run_all_tasks
    5. Data Validation Tests - Payload structure and content verification
    
    All tests use testcontainers for real PostgreSQL database interactions
    and respx for HTTP request mocking to ensure realistic test conditions.
    """
    
    @pytest_asyncio.fixture(scope="class")
    async def postgres_container(self):
        """Start a PostgreSQL test container."""
        # Try to explicitly specify Docker client configuration
        import os
        docker_host = os.environ.get('DOCKER_HOST')
        if not docker_host:
            # Try common Docker socket paths
            if os.path.exists('/var/run/docker.sock'):
                os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
            elif os.path.exists(f'{Path.home()}/.docker/run/docker.sock'):
                os.environ['DOCKER_HOST'] = f'unix:///{Path.home()}/.docker/run/docker.sock'
        
        with PostgresContainer("postgres:15-alpine") as postgres:
            yield postgres

    @pytest_asyncio.fixture
    async def db_session(self, postgres_container):
        """Create a database session using testcontainers PostgreSQL."""
        # Get connection URL from the container
        db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
        
        engine = create_async_engine(db_url, echo=False)
        
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        async with async_session() as session:
            yield session
        
        await engine.dispose()

    @pytest_asyncio.fixture
    async def test_user(self, db_session):
        """Create a test user with webhook configuration."""
        # Use unique email for each test
        import uuid
        unique_email = f"test-{uuid.uuid4().hex[:8]}@example.com"
        
        user = User(
            email=unique_email,
            name="Test User",
            data={"webhook_url": "https://webhook.example.com/meetings"}
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def test_meeting(self, db_session, test_user):
        """Create a test meeting associated with the test user."""
        meeting = Meeting(
            user_id=test_user.id,
            user=test_user,  # Eager load user to simulate bot exit task behavior
            platform="google_meet",
            platform_specific_id="test-meeting-123",
            status="completed",
            bot_container_id="container-abc123",
            start_time=datetime.now().replace(tzinfo=None),
            end_time=datetime.now().replace(tzinfo=None),
            data={
                "participants": [
                    {"name": "John Doe", "email": "john@example.com"},
                    {"name": "Jane Smith", "email": "jane@example.com"}
                ]
            }
        )
        db_session.add(meeting)
        await db_session.commit()
        await db_session.refresh(meeting)
        return meeting

    @pytest_asyncio.fixture
    async def test_meeting_session(self, db_session, test_meeting):
        """Create a test meeting session for connection_id."""
        session = MeetingSession(
            meeting_id=test_meeting.id,
            session_uid="session-uid-123",
            session_start_time=datetime.now().replace(tzinfo=None)
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        return session

    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.http
    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.it('webhook delivery succeeds with meeting payload')
    async def test_webhook_delivery_succeeds_with_complete_payload(self, db_session, test_meeting, test_meeting_session):
        """Test successful webhook delivery on bot exit."""
        # Mock the webhook endpoint
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(200, json={"status": "received"})
        )
        
        # Execute the webhook task
        await send_webhook_task(test_meeting, db_session)
        
        # Verify webhook was called
        assert webhook_request.called
        assert len(webhook_request.calls) == 1
        
        # Verify webhook payload
        request_call = webhook_request.calls[0]
        payload = json.loads(request_call.request.content.decode())

        print(payload)
        
        assert payload["id"] == test_meeting.id
        assert payload["user_id"] == test_meeting.user_id
        assert payload["platform"] == "google_meet"
        assert payload["native_meeting_id"] == "test-meeting-123"
        assert payload["status"] == "completed"
        assert payload["bot_container_id"] == "container-abc123"
        assert payload["connection_id"] == "session-uid-123"
        assert payload["participants"] == [
            {"name": "John Doe", "email": "john@example.com"},
            {"name": "Jane Smith", "email": "jane@example.com"}
        ]
        assert "start_time" in payload
        assert "end_time" in payload
        assert "created_at" in payload
        assert "updated_at" in payload

    @pytest.mark.webhook
    @pytest.mark.error_handling
    @pytest.mark.http
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_handles_http_error_responses_gracefully(self, db_session, test_meeting, test_meeting_session):
        """Test webhook delivery with HTTP error response."""
        # Mock webhook endpoint to return error
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        
        # Execute the webhook task (should not raise exception)
        await send_webhook_task(test_meeting, db_session)
        
        # Verify webhook was attempted
        assert webhook_request.called

    @pytest.mark.webhook
    @pytest.mark.error_handling
    @pytest.mark.http
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_handles_network_failures_gracefully(self, db_session, test_meeting, test_meeting_session):
        """Test webhook delivery with network error."""
        # Mock network failure
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        
        # Execute the webhook task (should not raise exception)
        await send_webhook_task(test_meeting, db_session)
        
        # Verify webhook was attempted
        assert webhook_request.called

    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.database
    @pytest.mark.asyncio
    async def test_webhook_skips_when_no_webhook_url_configured(self, db_session):
        """Test webhook task when user has no webhook URL configured."""
        # Create user without webhook URL
        import uuid
        unique_email = f"no-webhook-{uuid.uuid4().hex[:8]}@example.com"
        
        user = User(
            email=unique_email,
            name="No Webhook User",
            data={}  # No webhook_url
        )
        db_session.add(user)
        
        meeting = Meeting(
            user_id=user.id,
            user=user,
            platform="google_meet",
            platform_specific_id="no-webhook-meeting",
            status="completed"
        )
        db_session.add(meeting)
        await db_session.commit()
        await db_session.refresh(meeting)
        
        # Execute webhook task (should complete without error)
        await send_webhook_task(meeting, db_session)

    @pytest.mark.webhook
    @pytest.mark.error_handling
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_handles_missing_user_gracefully(self, db_session):
        """Test webhook task when meeting has no associated user."""
        # Create meeting without user relationship
        meeting = Meeting(
            user_id=999,  # Non-existent user
            platform="google_meet",
            platform_specific_id="orphan-meeting",
            status="completed"
        )
        meeting.user = None  # Simulate missing user
        
        # Execute webhook task - should raise ValueError
        with pytest.raises(ValueError, match="Could not find user on meeting object"):
            await send_webhook_task(meeting, db_session)

    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.http
    @pytest.mark.database
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_succeeds_when_no_connection_id_available(self, db_session, test_meeting):
        """Test webhook delivery when no meeting session exists (no connection_id)."""
        # Mock webhook endpoint
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(200)
        )
        
        # Execute webhook task (no meeting session created, so connection_id should be None)
        await send_webhook_task(test_meeting, db_session)
        
        # Verify webhook was called and connection_id is None
        assert webhook_request.called
        payload = json.loads(webhook_request.calls[0].request.content.decode())
        assert payload["connection_id"] is None

    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.http
    @pytest.mark.database
    @patch('app.tasks.bot_exit_tasks.async_session_local')
    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_bot_exit_workflow_triggers_webhook(self, mock_session_local, db_session, test_meeting, test_meeting_session):
        """Test the complete bot exit flow including run_all_tasks."""
        # Create a mock session context manager that returns our test db session
        mock_db_context = AsyncMock()
        mock_db_context.__aenter__.return_value = db_session
        mock_db_context.__aexit__.return_value = None
        
        # Configure the mock to return our context manager
        mock_session_local.return_value = mock_db_context
        
        # Mock webhook endpoint
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(200)
        )
        
        # Mock the transcription collector endpoint (used by aggregate_transcription task)
        respx.get(f"http://transcription-collector:8000/internal/transcripts/{test_meeting.id}").mock(
            return_value=httpx.Response(200, json={"transcripts": []})
        )
        
        # Execute run_all_tasks (this will use our mocked database session)
        await run_all_tasks(test_meeting.id)
        
        # Verify the session factory was called
        mock_session_local.assert_called()
        
        # Verify webhook was triggered
        assert webhook_request.called

    @pytest.mark.webhook
    @pytest.mark.integration
    @pytest.mark.http
    @pytest.mark.database
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_payload_contains_all_required_fields(self, db_session, test_meeting, test_meeting_session):
        """Test that webhook payload contains all required fields with correct data types."""
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(200)
        )
        
        await send_webhook_task(test_meeting, db_session)
        
        payload = json.loads(webhook_request.calls[0].request.content.decode())
        
        # Verify payload structure and types
        required_fields = [
            'id', 'user_id', 'platform', 'native_meeting_id', 'constructed_meeting_url',
            'status', 'bot_container_id', 'connection_id', 'start_time', 'end_time',
            'data', 'created_at', 'updated_at', 'participants'
        ]
        
        for field in required_fields:
            assert field in payload, f"Required field '{field}' missing from webhook payload"
        
        # Verify specific types and values
        assert isinstance(payload['id'], int)
        assert isinstance(payload['user_id'], int)
        assert isinstance(payload['platform'], str)
        assert isinstance(payload['participants'], list)
        assert payload['platform'] == "google_meet"
        assert payload['connection_id'] == "session-uid-123"

    @pytest.mark.webhook
    @pytest.mark.error_handling
    @pytest.mark.http
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_handles_request_timeouts_gracefully(self, db_session, test_meeting, test_meeting_session):
        """Test webhook delivery with timeout."""
        # Mock webhook to timeout
        webhook_route = respx.post("https://webhook.example.com/meetings")
        webhook_route.mock(side_effect=httpx.TimeoutException("Request timed out"))
        
        # Execute webhook task (should handle timeout gracefully)
        await send_webhook_task(test_meeting, db_session)
        
        # Verify the request was attempted and check call status
        assert webhook_route.called
        call = webhook_route.calls.last
        # The call should exist but have failed with the timeout exception
        assert call.request is not None
        # Check that no response was set (indicates the exception occurred)
        assert not hasattr(call, '_response') or call._response is None

    @pytest.mark.webhook
    @pytest.mark.error_handling
    @pytest.mark.http
    @pytest.mark.integration
    @pytest.mark.database
    @respx.mock
    @pytest.mark.asyncio
    async def test_webhook_handles_missing_participants_data_gracefully(self, db_session, test_user):
        """Test webhook delivery when meeting data doesn't have 'participants' key - should handle gracefully."""
        # Create a meeting with data that has NO 'participants' key
        meeting = Meeting(
            user_id=test_user.id,
            user=test_user,
            platform="google_meet",
            platform_specific_id="missing-participants-meeting",
            status="completed",
            bot_container_id="container-xyz789",
            start_time=datetime.now().replace(tzinfo=None),
            end_time=datetime.now().replace(tzinfo=None),
            data={
                "other_field": "some_value",
                "meeting_info": "test data"
                # NOTE: No 'participants' key here - should default to empty array
            }
        )
        db_session.add(meeting)
        await db_session.commit()
        await db_session.refresh(meeting)
        
        # Mock webhook endpoint
        webhook_request = respx.post("https://webhook.example.com/meetings").mock(
            return_value=httpx.Response(200)
        )
        
        # Execute webhook task - should succeed now with the fix
        await send_webhook_task(meeting, db_session)
        
        # Verify webhook was called successfully
        assert webhook_request.called
        
        # Verify the payload has empty participants array
        request_call = webhook_request.calls[0]
        payload = json.loads(request_call.request.content.decode())
        assert payload["participants"] == []  # Should be empty array, not throw KeyError


# Run the tests if this file is executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])