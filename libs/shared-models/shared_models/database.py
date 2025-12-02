import os
import logging
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import create_engine # For sync engine if needed for migrations later
from sqlalchemy.sql import text

# Import Base from models within the same package
# Ensure models are imported somewhere before init_db is called so Base is populated.
from .models import Base 

logger = logging.getLogger("shared_models.database")

# --- Database Configuration ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
# SSL mode: disable, allow, prefer, require, verify-ca, verify-full
# For Supabase and most remote databases, use "require" or "prefer"
DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "prefer")

# --- Validation at startup ---
if not all([DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD]):
    missing_vars = [
        var_name
        for var_name, var_value in {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
        }.items()
        if not var_value
    ]
    raise ValueError(f"Missing required database environment variables: {', '.join(missing_vars)}")

# Build connection URLs with SSL support
# For asyncpg: SSL is handled via connect_args, not URL query parameters
# For psycopg2: SSL is handled via query parameters in the URL
DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
ssl_params = f"?sslmode={DB_SSL_MODE}" if DB_SSL_MODE else ""
DATABASE_URL_SYNC = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}{ssl_params}"

# Configure SSL for asyncpg
# asyncpg uses ssl parameter (True/False/ssl.SSLContext)
# For Supabase Session Pooler, we need SSL but may need to disable certificate verification
# Map sslmode values to asyncpg ssl parameter
import ssl

asyncpg_ssl = None
if DB_SSL_MODE and DB_SSL_MODE.lower() in ("require", "prefer"):
    # For require/prefer: Use SSL but don't verify certificate (for pooler compatibility)
    # Create an SSL context that doesn't verify certificates
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    asyncpg_ssl = ssl_context
elif DB_SSL_MODE and DB_SSL_MODE.lower() in ("verify-ca", "verify-full"):
    # For verify modes: Use SSL with certificate verification
    asyncpg_ssl = True
elif DB_SSL_MODE and DB_SSL_MODE.lower() == "disable":
    asyncpg_ssl = False
# If DB_SSL_MODE is not set or is "allow", asyncpg_ssl remains None (default behavior)

# --- SQLAlchemy Async Engine & Session --- 
# Use pool settings appropriate for async connections
connect_args = {}
if asyncpg_ssl is not None:
    connect_args["ssl"] = asyncpg_ssl

# Disable prepared statement caching for pgbouncer compatibility
# This is required when using pgbouncer in transaction or statement pooling mode
connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG",
    pool_size=10, # Example pool size
    max_overflow=20 # Example overflow
)
async_session_local = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# --- Sync Engine (For Alembic migrations) ---
sync_engine = create_engine(DATABASE_URL_SYNC)

# --- FastAPI Dependency --- 
async def get_db() -> AsyncSession:
    """FastAPI dependency to get an async database session."""
    async with async_session_local() as session:
        try:
            yield session
        finally:
            # Ensure session is closed, though context manager should handle it
            await session.close()

# --- Initialization Function --- 
async def init_db():
    """Creates database tables based on shared models' metadata."""
    logger.info(f"Initializing database tables at {DB_HOST}:{DB_PORT}/{DB_NAME}")
    try:
        async with engine.begin() as conn:
            # This relies on all SQLAlchemy models being imported 
            # somewhere before this runs, so Base.metadata is populated.
            # Add checkfirst=True to prevent errors if tables already exist
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        logger.info("Database tables checked/created successfully.")
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}", exc_info=True)
        raise 

# --- DANGEROUS: Recreate Function ---
async def recreate_db():
    """
    DANGEROUS: Drops all tables and recreates them based on shared models' metadata.
    THIS WILL RESULT IN COMPLETE DATA LOSS. USE WITH EXTREME CAUTION.
    """
    logger.warning(f"!!! DANGEROUS OPERATION: Dropping and recreating all tables in {DB_NAME} at {DB_HOST}:{DB_PORT} !!!")
    try:
        async with engine.begin() as conn:
            # Instead of drop_all, use raw SQL to drop the schema with cascade
            logger.warning("Dropping public schema with CASCADE...")
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            logger.warning("Public schema dropped.")
            logger.info("Recreating public schema...")
            await conn.execute(text("CREATE SCHEMA public;"))
            # Optional: Grant permissions if needed (often handled by default roles)
            # await conn.execute(text("GRANT ALL ON SCHEMA public TO public;")) 
            # await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres;")) 
            logger.info("Public schema recreated.")
            
            logger.info("Recreating all tables based on models...")
            await conn.run_sync(Base.metadata.create_all)
            logger.info("All tables recreated successfully.")
        logger.warning(f"!!! DANGEROUS OPERATION COMPLETE for {DB_NAME} at {DB_HOST}:{DB_PORT} !!!")
    except Exception as e:
        logger.error(f"Error recreating database tables: {e}", exc_info=True)
        raise 