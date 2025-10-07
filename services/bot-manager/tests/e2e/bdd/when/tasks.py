"""
WHEN helpers for task execution actions - these wrap the system under test.
"""
import logging
from app.tasks.bot_exit_tasks import run_all_tasks


async def end_of_meeting_tasks_are_executed(meeting_id: int, db_session, logger=None):
    """WHEN end-of-meeting tasks are executed for a meeting."""
    result = await run_all_tasks(meeting_id, db_session, logger=logger)
    return result


def task_results_are_logged(result: dict, logger: logging.Logger):
    """WHEN task results are logged (helper for debugging)."""
    succeeded = result.get('succeeded', [])
    failed = result.get('failed', [])
    
    logger.info(f"Task execution completed - Succeeded: {len(succeeded)}, Failed: {len(failed)}")
    
    if succeeded:
        logger.info(f"Successful tasks: {', '.join(succeeded)}")
    
    if failed:
        logger.warning(f"Failed tasks detected: {len(failed)}")
        for failed_task in failed:
            if isinstance(failed_task, dict):
                logger.error(f"Task '{failed_task['task']}' failed: {failed_task['error']}")
            else:
                logger.error(f"Failed task: {failed_task}")