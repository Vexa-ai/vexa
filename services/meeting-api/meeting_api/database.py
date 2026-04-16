import os
import logging
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import create_engine
from sqlalchemy.sql import text

from .models import Base

logger = logging.getLogger("meeting_api.database")

# --- Database Configuration ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
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

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
ssl_params = f"?sslmode={DB_SSL_MODE}" if DB_SSL_MODE else ""
DATABASE_URL_SYNC = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}{ssl_params}"

import ssl

asyncpg_ssl = None
if DB_SSL_MODE and DB_SSL_MODE.lower() in ("require", "prefer"):
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    asyncpg_ssl = ssl_context
elif DB_SSL_MODE and DB_SSL_MODE.lower() in ("verify-ca", "verify-full"):
    asyncpg_ssl = True
elif DB_SSL_MODE and DB_SSL_MODE.lower() == "disable":
    asyncpg_ssl = False

connect_args = {}
if asyncpg_ssl is not None:
    connect_args["ssl"] = asyncpg_ssl
connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG",
    pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
    pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", "30")),
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_reset_on_return="rollback",
)
async_session_local = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

sync_engine = create_engine(DATABASE_URL_SYNC)


async def get_db() -> AsyncSession:
    """FastAPI dependency to get an async database session."""
    async with async_session_local() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Converge database schema to match meeting-api models (idempotent)."""
    from schema_sync import ensure_schema
    from admin_models.models import Base as AdminBase
    logger.info(f"Initializing database tables at {DB_HOST}:{DB_PORT}/{DB_NAME}")
    try:
        await ensure_schema(engine, Base, prerequisites=AdminBase)
        logger.info("Database tables checked/created successfully.")
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}", exc_info=True)
        raise


async def recreate_db():
    """DANGEROUS: Drops all tables and recreates them."""
    if os.getenv("ALLOW_DROP_SCHEMA") != "true":
        raise RuntimeError("recreate_db disabled in production. Set ALLOW_DROP_SCHEMA=true to enable.")
    logger.warning(f"!!! DANGEROUS: Dropping and recreating all tables in {DB_NAME} at {DB_HOST}:{DB_PORT} !!!")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            await conn.run_sync(Base.metadata.create_all)
        logger.warning(f"!!! DANGEROUS OPERATION COMPLETE for {DB_NAME} at {DB_HOST}:{DB_PORT} !!!")
    except Exception as e:
        logger.error(f"Error recreating database tables: {e}", exc_info=True)
        raise
