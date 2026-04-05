"""conftest.py – pytest configuration for transcription-service tests."""


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that need a running service")
