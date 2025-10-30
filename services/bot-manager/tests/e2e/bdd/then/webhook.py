"""
THEN helpers for webhook assertions.
"""
import logging


def webhook_should_have_been_called(webhook_request, logger: logging.Logger):
    """THEN webhook should have been called if configured."""
    if webhook_request:
        assert webhook_request.called, "Webhook should have been called"
        logger.info("Webhook called successfully")


def webhook_task_should_have_succeeded(result: dict):
    """THEN the webhook task should have succeeded."""
    if 'send_webhook' not in result.get('succeeded', []):
        failed_webhook = next((task for task in result.get('failed', []) 
                             if isinstance(task, dict) and task.get('task') == 'send_webhook'), None)
        if failed_webhook:
            raise AssertionError(f"Webhook task failed: {failed_webhook['error']}")