import os
import importlib
import inspect
from dependency_injector.wiring import inject, Provide
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from shared_models.models import Meeting
from shared_models.database import async_session_local
from app.core.protocols.logger_protocol import LoggerProtocol

@inject
async def run_all_tasks(
    meeting_id: int, 
    db_session: AsyncSession = None,
    logger: LoggerProtocol = Provide['logging.bot_manager_logger']
):
    """
    Dynamically discovers and runs all bot exit tasks for a given meeting_id.
    
    Args:
        meeting_id: The ID of the meeting to run tasks for
        db_session: Optional database session. If not provided, creates its own.
    
    This function fetches the meeting object (eager-loading the associated user),
    and then scans the current directory for Python modules. It imports them and 
    looks for an async function named 'run' that accepts 'meeting' and 'db' arguments. 
    It then executes each found task and commits any changes at the end.
    
    Returns:
        dict: Status of task execution with 'succeeded' and 'failed' lists
    """
    logger.info(f"Starting to run all post-meeting tasks for meeting_id: {meeting_id}")
    
    succeeded_tasks = []
    failed_tasks = []
    
    # Use provided session or create new one
    if db_session:
        return await _run_tasks_with_session(meeting_id, db_session, succeeded_tasks, failed_tasks, logger)
    else:
        async with async_session_local() as db:
            return await _run_tasks_with_session(meeting_id, db, succeeded_tasks, failed_tasks, logger)


async def _run_tasks_with_session(meeting_id: int, db: AsyncSession, succeeded_tasks: list, failed_tasks: list, logger: LoggerProtocol):
    """Helper function to run tasks with a given database session."""
    try:
        # Eager load the User object to avoid separate queries in tasks
        meeting = await db.get(Meeting, meeting_id, options=[selectinload(Meeting.user)])
        if not meeting:
            logger.error(f"Could not find meeting with ID {meeting_id} to run post-meeting tasks.")
            return {"succeeded": [], "failed": [], "error": "Meeting not found"}

        current_dir = os.path.dirname(__file__)
        current_package = 'app.tasks.bot_exit_tasks'

        files = os.listdir(current_dir)
        files.sort()

        for filename in files:
            if filename.endswith('.py') and filename != '__init__.py':
                module_name = filename[:-3]
                try:
                    full_module_path = f"{current_package}.{module_name}"
                    module = importlib.import_module(full_module_path)
                    
                    if hasattr(module, 'run') and inspect.iscoroutinefunction(module.run):
                        logger.info(f"Found task in '{module_name}'. Executing for meeting {meeting_id}...")
                        try:
                            await module.run(meeting, db)
                            logger.info(f"Successfully executed task in '{module_name}' for meeting {meeting_id}.")
                            succeeded_tasks.append(module_name)
                        except Exception as e:
                            logger.error(f"Error executing task in '{module_name}' for meeting {meeting_id}: {e}", exc_info=True)
                            failed_tasks.append({"task": module_name, "error": str(e)})
                    else:
                        logger.debug(f"Module '{module_name}' does not have a valid async 'run' function.")
                
                except ImportError as e:
                    logger.error(f"Failed to import task module '{module_name}': {e}", exc_info=True)
                    failed_tasks.append({"task": module_name, "error": f"Import error: {str(e)}"})
        
        await db.commit()
        logger.info(f"All post-meeting tasks run and changes committed for meeting_id: {meeting_id}")

    except Exception as e:
        logger.error(f"An error occurred in the task runner for meeting_id {meeting_id}: {e}", exc_info=True)
        await db.rollback()
        return {"succeeded": succeeded_tasks, "failed": failed_tasks, "error": f"Runner error: {str(e)}"}

    return {"succeeded": succeeded_tasks, "failed": failed_tasks} 