#!/usr/bin/env python3
"""
Utility script to check database state for migration purposes.
Outputs one of: 'alembic', 'legacy', or 'fresh'
"""
import asyncio
import sys
from sqlalchemy import text

# Add the parent directory to sys.path to import shared_models
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared_models.database import engine

async def check_db_state():
    """
    Check database state and return:
    - 'alembic': Has alembic_version table (Alembic-managed)
    - 'legacy': Has meetings table but no alembic_version (legacy database)
    - 'fresh': Empty database with no tables
    """
    try:
        async with engine.connect() as conn:
            # Check for alembic_version table
            has_alembic = await conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'")
            )
            if has_alembic.scalar():
                return 'alembic'
            
            # Check for meetings table
            has_meetings = await conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = 'meetings'")
            )
            if has_meetings.scalar():
                return 'legacy'
            
            # Empty database
            return 'fresh'
    except Exception:
        # On any error, assume fresh database
        return 'fresh'

if __name__ == "__main__":
    state = asyncio.run(check_db_state())
    print(state)
