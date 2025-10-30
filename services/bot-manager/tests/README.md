# Bot Manager Tests

This directory contains integration and unit tests for the bot-manager service.

## Running Tests

### Prerequisites

1. Install test dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Ensure you're in the bot-manager service directory:
   ```bash
   cd services/bot-manager
   ```

### Run All Tests

```bash
pytest
```

### Run Specific Test Files

```bash
# Run webhook integration tests only
pytest tests/test_send_webhook_integration.py

# Run with verbose output
pytest -v tests/test_send_webhook_integration.py
```

### Run Tests with Coverage

```bash
pip install pytest-cov
pytest --cov=app tests/
```

## Test Structure

- `conftest.py` - Shared pytest configuration and fixtures
- `test_send_webhook_integration.py` - Integration tests for webhook functionality on bot exit

## Test Categories

### Integration Tests
- Test the complete flow from bot exit to webhook delivery
- Use real HTTP mocking with `respx`
- Test database interactions with in-memory SQLite
- Cover error scenarios and edge cases

### Key Test Scenarios

The webhook integration tests cover:

1. **Successful webhook delivery** - Verifies complete payload structure and HTTP success
2. **HTTP error responses** - Tests handling of 4xx/5xx responses
3. **Network errors** - Tests connection failures and timeouts
4. **Missing configurations** - Tests behavior when webhook URL is not configured
5. **Database edge cases** - Tests missing users, sessions, etc.
6. **Complete task runner integration** - Tests the full `run_all_tasks` flow

## Database Setup

Tests use an in-memory SQLite database that's created fresh for each test. This ensures:
- Fast test execution
- No external dependencies
- Clean state between tests
- No impact on development/production databases

## HTTP Mocking

Tests use `respx` to mock HTTP requests to webhook endpoints, allowing us to:
- Test webhook payloads without external services
- Simulate various error conditions
- Verify request details (headers, body, etc.)
- Test timeout and connection scenarios