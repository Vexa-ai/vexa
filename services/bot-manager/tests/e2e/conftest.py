"""
E2E Test Configuration and Fixtures

Shared fixtures for end-to-end tests that may need real database connections,
external services, or production-like environments.
"""
import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer
from shared_models.models import Base


def pytest_addoption(parser):
    """Add custom command line options for replay tests."""
    parser.addoption(
        "--session-id",
        action="store",
        default=None,
        help="Session ID (connection_id) to replay end-of-meeting scenario"
    )


def pytest_configure(config):
    pass

@pytest.fixture(scope="session")
def session_id(request):
    """Fixture to provide session ID from command line or environment."""
    # First try command line argument
    session_id = request.config.getoption("--session-id")
    
    # Fall back to environment variable
    if not session_id:
        session_id = os.getenv('SESSION_ID')
    
    return session_id


@pytest_asyncio.fixture(scope="session")
async def e2e_postgres_container():
    """
    PostgreSQL container for E2E tests.
    
    Uses a longer-lived container that can be shared across multiple tests.
    Falls back gracefully when Docker is not available.
    
    Environment variables:
    - DB_HOST: If set to non-default value, skip testcontainer
    - DOCKER_HOST: Docker socket location (if not auto-detected)
    """
    # Check if we should use external database via DB_HOST
    db_host = os.getenv('DB_HOST')
    
    if db_host and db_host != 'postgres':
        # Use external database - no container needed
        yield None
        return
    
    try:
        # Use testcontainer for isolated testing
        with PostgresContainer("postgres:15-alpine") as postgres:
            yield postgres
    except Exception as e:
        # Docker not available - provide helpful error message
        error_msg = f"Docker not available for testcontainers: {e}"
        if "No such file or directory" in str(e):
            error_msg += "\n\nTo fix this, either:"
            error_msg += "\n  1. Set DOCKER_HOST environment variable to your Docker socket"
            error_msg += "\n  2. Or set DB_HOST=localhost to use external database"
            
        pytest.skip(error_msg)


@pytest_asyncio.fixture
async def e2e_db_session(e2e_postgres_container):
    """
    Database session for E2E tests.
    
    Uses the same database configuration as the main application:
    - If DB_HOST is set, connects to external database  
    - Otherwise uses testcontainer
    """
    
    # Check if using external database via standard DB environment variables
    db_host = os.getenv('DB_HOST')
    
    if db_host and db_host != 'postgres':
        # Use external database with same config as main app
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'vexa')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', 'postgres')
        
        db_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        create_tables = False  # Assume tables already exist
    else:
        # Use testcontainer
        db_url = e2e_postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
        create_tables = True
    

    # Enable SQL logging when sqlalchemy is in DEBUG
    # enable_sql_logging = 'sqlalchemy' in os.getenv('DEBUG', '')
    engine = create_async_engine(db_url, echo=False, echo_pool=False)
    
    # Create tables if using testcontainer
    if create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


    async with async_session() as session:
        yield session
    
    
    await engine.dispose()


# Re-export the main db_session fixture from integration tests for consistency
@pytest.fixture
async def db_session(e2e_db_session):
    """Alias for e2e_db_session to maintain compatibility with existing test patterns."""
    return e2e_db_session