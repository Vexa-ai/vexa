# recreate_db.py
import asyncio
import logging
import sys
import os

# --- Configuration ---
# Attempt to configure path if running standalone. Assumes script is in services/admin-api/app/scripts
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..', '..', '..'))
libs_dir = os.path.join(project_root, 'libs')
if libs_dir not in sys.path:
    sys.path.insert(0, libs_dir)
# --------------------

from shared_models.database import recreate_db, logger as db_logger, Base 


# Configure logging to see the warnings/errors from recreate_db
# Set level to INFO to capture all messages from the function
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout # Ensure logs go to stdout for docker exec visibility
)
# Ensure the database logger also outputs INFO level messages
db_logger.setLevel(logging.INFO)

async def main():
    print("\n--- Starting Database Recreation ---\n", flush=True)
    print("Attempting to drop and recreate all database tables.", flush=True)
    print("Check logs below for details, warnings, and confirmation.", flush=True)
    print("-" * 40, flush=True)
    await recreate_db()
    print("-" * 40, flush=True)
    print("--- Database Recreation Process Finished ---\n", flush=True)

if __name__ == "__main__":
    print("+" * 60)
    print("!!! DANGER !!!")
    print("This script will permanently delete ALL data from the database")
    print("by dropping and recreating all tables based on shared_models.")
    print("Make sure this is absolutely intended, especially in production!")
    print("+" * 60)
    
    # Simple confirmation prompt
    confirm = input("Type 'recreate' to proceed: ")
    
    if confirm == 'recreate':
        print("\nConfirmation received. Proceeding with database recreation...\n", flush=True)
        try:
            asyncio.run(main())
        except Exception as e:
            # Use logger for exceptions too
            logging.exception(f"An unexpected error occurred during execution: {e}")
            print("\n--- Database Recreation FAILED ---\n", flush=True)
            sys.exit(1)
    else:
        print("\nConfirmation not received or incorrect. Database recreation cancelled.\n", flush=True)
        sys.exit(0) 