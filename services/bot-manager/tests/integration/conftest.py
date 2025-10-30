"""
Integration Test Configuration and Fixtures

Shared fixtures for integration tests that need dependency injection
but don't require full E2E server startup.
"""
import pytest

# Initialize DI container immediately for integration testing
# This needs to happen before test modules import the task functions
from app.startup import initialize_application
_test_container = initialize_application(testing=True)

@pytest.fixture(scope="session", autouse=True)
def initialize_di_container():
    """Provide the already-initialized DI container for integration testing."""
    yield _test_container
    from app.startup import shutdown_application
    shutdown_application()