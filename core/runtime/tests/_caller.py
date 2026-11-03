"""The runtime caller credential the tests drive the API with, and a client that presents it."""
from fastapi.testclient import TestClient

TOKEN = "runtime-caller-token-for-tests-0123456789abcdef"


def caller_client(app) -> TestClient:
    """A TestClient that presents the runtime caller credential on every request."""
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
