"""
BDD-style helpers for E2E tests with clear Given-When-Then namespacing.

Usage:
    from tests.e2e.bdd import Given, When, Then
    
    async with Given.di_container_is_initialized():
        meeting = Given.meeting_data_is_valid(...)
        webhook = Given.webhooks_are_mocked_for_meeting(...)
        
        result = await some_action()
        When.tasks_are_executed(result, logger)
        
        Then.webhook_should_have_been_called(webhook, logger)
        Then.all_tasks_should_have_completed_successfully(result)
"""

# Import individual helper modules
from . import given, when, then


class Given:
    """GIVEN step helpers organized by domain - clearly visible in test code."""
    
    # Infrastructure helpers
    from .given.infrastructure import (
        di_container_is_initialized,
        a_test_logger, 
        a_python_logger
    )
    
    # Meeting helpers
    from .given.meeting import (
        meeting_exists_for_session,
        meeting_data_is_valid
    )
    
    # Webhook helpers  
    from .given.webhook import webhooks_are_mocked_for_meeting


class When:
    """WHEN step helpers organized by domain - clearly visible in test code."""
    
    # Meeting domain actions
    from .when.meeting import (
        a_bot_exits_the_meeting,
        meeting_is_requested_to_stop,
        a_bot_is_requested_for_meeting,
        meeting_data_is_refreshed_from_database
    )
    
    # Task domain actions  
    from .when.tasks import (
        end_of_meeting_tasks_are_executed,
        task_results_are_logged
    )


class Then:
    """THEN step helpers organized by domain - clearly visible in test code."""
    
    # Meeting assertions
    from .then.meeting import (
        meeting_should_be_marked_as_completed,
        meeting_should_have_status
    )
    
    # Webhook assertions
    from .then.webhook import (
        webhook_should_have_been_called,
        webhook_task_should_have_succeeded
    )
    
    # Task assertions
    from .then.tasks import (
        all_tasks_should_have_completed_successfully,
        tasks_should_have_succeeded
    )