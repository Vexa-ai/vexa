"""
Pytest configuration and shared fixtures for bot-manager tests.
"""
import pytest
import asyncio
import os
import sys
from pathlib import Path
import logging
from app.adapters.logging import StandardLogger

# Add the parent directory to sys.path so we can import the app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--show-deselected",
        action="store_true",
        default=False,
        help="Show deselected tests in output"
    )
    parser.addoption(
        "--log-http",
        action="store_true",
        default=False,
        help="Enable detailed HTTP request/response logging"
    )
    

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def setup_test_environment():
    
    """Set up test environment variables."""
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"  # Use test DB

    # Only set LOG_LEVEL if not already set (preserves command line override)
    if "LOG_LEVEL" not in os.environ:
        os.environ["LOG_LEVEL"] = "DEBUG"

    # Only set database env vars if they're not already set (preserves E2E test config)
    if "DB_HOST" not in os.environ:
        os.environ["DB_HOST"] = "postgres"  # Default to "postgres" to enable testcontainers fallback
    if "DB_NAME" not in os.environ:
        os.environ["DB_NAME"] = "test_vexa"
    if "DB_USER" not in os.environ:
        os.environ["DB_USER"] = "postgres"
    if "DB_PASSWORD" not in os.environ:
        os.environ["DB_PASSWORD"] = "postgres"

def pytest_deselected(items):
    """Show deselected tests if --show-deselected flag is used."""
    if not items:
        return
    
    config = items[0].session.config
    
    # Only show if flag is enabled
    if not config.getoption("--show-deselected"):
        return
    
    reporter = config.pluginmanager.getplugin("terminalreporter")
    reporter.ensure_newline()
    reporter.line("=" * 20 + " DESELECTED TESTS " + "=" * 20, yellow=True)
    for item in items:
        reporter.line(f"DESELECTED: {item.nodeid}", yellow=True)
    reporter.line("=" * 57, yellow=True)

@pytest.fixture(scope="session", autouse=True)
def configure_http_logging(request):
    """Configure HTTP request/response logging based on command line flag."""
    
    log_http = request.config.getoption("--log-http")
    
    if log_http :
        # Set logging levels for all HTTP-related loggers based on LOG_LEVEL env var
        import os
        log_level_str = os.environ.get('LOG_LEVEL', 'INFO')
        try:
            numeric_level = getattr(logging, log_level_str.upper())
        except AttributeError:
            numeric_level = logging.INFO
            
        # Create logger with the correct level from environment
        logger = StandardLogger("httpx", level=numeric_level)
        
        # Monkey patch httpx for detailed logging
        import httpx
        import json
        
        original_send = httpx.Client.send
        original_async_send = httpx.AsyncClient.send
        
        def logged_send(self, request, **kwargs):
            logger.info(f"🔍 ➡️  {request.method} {request.url}")
            if hasattr(request, 'content') and request.content:
                try:
                    content = request.content.decode('utf-8')
                    if request.headers.get('content-type', '').startswith('application/json'):
                        parsed = json.loads(content)
                        logger.debug(f"🔍 Request Body: {json.dumps(parsed, indent=2)}")
                    else:
                        logger.debug(f"🔍 Request Body: {content}")
                except:
                    logger.debug(f"🔍 Request Body: <{len(request.content)} bytes>")
            
            response = original_send(self, request, **kwargs)
            
            logger.info(f"🔍 ⬅️  {response.status_code} {response.url}")
            if response.content:
                try:
                    if response.headers.get('content-type', '').startswith('application/json'):
                        parsed = response.json()
                        logger.debug(f"🔍 Response Body: {json.dumps(parsed, indent=2)}")
                    else:
                        logger.debug(f"🔍 Response Body: {response.text}")
                except:
                    logger.debug(f"🔍 Response Body: <{len(response.content)} bytes>")
            logger.debug("-" * 50)
            return response
        
        async def logged_async_send(self, request, **kwargs):
            logger.info(f"🔍 ➡️  {request.method} {request.url}")
            if hasattr(request, 'content') and request.content:
                try:
                    content = request.content.decode('utf-8')
                    if request.headers.get('content-type', '').startswith('application/json'):
                        parsed = json.loads(content)
                        logger.debug(f"🔍 Request Body: {json.dumps(parsed, indent=2)}")
                    else:
                        logger.debug(f"🔍 Request Body: {content}")
                except:
                    logger.debug(f"🔍 Request Body: <{len(request.content)} bytes>")
            
            response = await original_async_send(self, request, **kwargs)
            
            logger.info(f"🔍 ⬅️  {response.status_code} {response.url}")
            if response.content:
                try:
                    if response.headers.get('content-type', '').startswith('application/json'):
                        parsed = response.json()
                        logger.debug(f"🔍 Response Body: {json.dumps(parsed, indent=2)}")
                    else:
                        logger.debug(f"🔍 Response Body: {response.text}")
                except:
                    logger.debug(f"🔍 Response Body: <{len(response.content)} bytes>")
            logger.debug("-" * 50)
            return response
        
        httpx.Client.send = logged_send
        httpx.AsyncClient.send = logged_async_send
        
        logger.info("🔍 HTTP request/response body logging enabled")