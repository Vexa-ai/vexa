"""
GIVEN helpers for infrastructure setup (DI, logging, environment).
"""
from contextlib import asynccontextmanager
import logging
from app.startup import initialize_application, shutdown_application
from app.adapters.logging.standard_logger import StandardLogger


@asynccontextmanager
async def di_container_is_initialized(testing: bool = True):
    """GIVEN a DI container is properly initialized for testing."""
    initialize_application(testing=testing)
    try:
        yield
    finally:
        shutdown_application()


def a_test_logger(test_name: str) -> StandardLogger:
    """GIVEN a test logger for the specified test."""
    return StandardLogger(f"e2e.{test_name}")


def a_python_logger(name: str) -> logging.Logger:
    """GIVEN a standard Python logger for test output."""
    return logging.getLogger(name)