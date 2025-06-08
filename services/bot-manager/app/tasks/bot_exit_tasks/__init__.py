import os
import importlib
import inspect
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting
from shared_models.database import async_session_local

logger = logging.getLogger(__name__)

async def run_all_tasks(meeting_id: int):
    """
    Dynamically discovers and runs all bot exit tasks for a given meeting_id.
    
    This function creates its own database session, fetches the meeting object,
    and then scans the current directory for Python modules. It imports them
    and looks for an async function named 'run' that accepts 'meeting' and 'db'
    arguments. It then executes each found task and commits any changes at the end.
    """
    logger.info(f"Starting to run all post-meeting tasks for meeting_id: {meeting_id}")
    
    async with async_session_local() as db:
        try:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Could not find meeting with ID {meeting_id} to run post-meeting tasks.")
                return

            current_dir = os.path.dirname(__file__)
            # Adjust the package path to be relative to the 'app' directory
            # Assuming 'tasks' is a sub-package of 'app'
            current_package = 'app.tasks.bot_exit_tasks'

            for filename in os.listdir(current_dir):
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
                            except Exception as e:
                                logger.error(f"Error executing task in '{module_name}' for meeting {meeting_id}: {e}", exc_info=True)
                        else:
                            logger.debug(f"Module '{module_name}' does not have an async 'run' function.")
                    
                    except ImportError as e:
                        logger.error(f"Failed to import task module '{module_name}': {e}", exc_info=True)
            
            await db.commit()
            logger.info(f"All post-meeting tasks run and changes committed for meeting_id: {meeting_id}")

        except Exception as e:
            logger.error(f"An error occurred in the task runner for meeting_id {meeting_id}: {e}", exc_info=True)
            await db.rollback() 