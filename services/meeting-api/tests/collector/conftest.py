"""conftest.py -- pytest path setup for collector unit tests."""
import os

# Set required env vars BEFORE importing anything that touches database
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pass")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
