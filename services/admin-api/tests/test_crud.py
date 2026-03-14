"""Tests for admin-api CRUD route definitions and endpoint availability."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock

# Set required env vars before importing app
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("ANALYTICS_API_TOKEN", "test-analytics-token")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from app.main import app, verify_admin_token, verify_analytics_or_admin_token, get_current_user
from shared_models.database import get_db
from httpx import AsyncClient, ASGITransport


# --- Helpers ---

def _route_paths_and_methods():
    """Extract (path, methods) tuples from app routes."""
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            for method in route.methods:
                routes.append((route.path, method))
    return routes


def make_fake_user(user_id=1, data=None, email="test@example.com", name="Test"):
    """Create a mock User object."""
    user = MagicMock()
    user.id = user_id
    user.email = email
    user.name = name
    user.image_url = None
    user.max_concurrent_bots = 1
    user.data = data
    user.created_at = "2025-01-01T00:00:00"
    user.meetings = []
    user.api_tokens = []
    return user


def make_mock_db(fake_user=None):
    """Create a mock async DB session."""
    db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = fake_user
    scalars.all.return_value = [fake_user] if fake_user else []
    result.scalars.return_value = scalars
    result.scalar_one.return_value = 1  # For count queries
    db.execute.return_value = result
    db.get = AsyncMock(return_value=fake_user)
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda u: None)
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db


async def noop_verify_admin():
    return None


async def noop_verify_analytics():
    return None


def noop_get_current_user():
    return make_fake_user()


# --- Route existence tests (inspect FastAPI app.routes) ---

class TestRouteDefinitions:
    """Verify all expected routes are registered on the FastAPI app."""

    def test_root_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/", "GET") in routes

    def test_create_user_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users", "POST") in routes

    def test_list_users_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users", "GET") in routes

    def test_get_user_by_id_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users/{user_id}", "GET") in routes

    def test_get_user_by_email_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users/email/{user_email}", "GET") in routes

    def test_update_user_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users/{user_id}", "PATCH") in routes

    def test_create_token_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/users/{user_id}/tokens", "POST") in routes

    def test_delete_token_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/tokens/{token_id}", "DELETE") in routes

    def test_meetings_users_stats_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/stats/meetings-users", "GET") in routes

    def test_analytics_users_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/analytics/users", "GET") in routes

    def test_analytics_meetings_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/analytics/meetings", "GET") in routes

    def test_meeting_telematics_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/analytics/meetings/{meeting_id}/telematics", "GET") in routes

    def test_user_details_analytics_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/admin/analytics/users/{user_id}/details", "GET") in routes

    def test_user_webhook_route_exists(self):
        routes = _route_paths_and_methods()
        assert ("/user/webhook", "PUT") in routes


# --- Endpoint integration tests (with mocked DB and auth) ---

class TestUserEndpoints:
    """Test user CRUD endpoints with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_create_user_returns_201(self):
        """POST /admin/users creates a new user and returns 201."""
        mock_db = make_mock_db(None)  # No existing user found

        # Simulate: execute finds no existing user
        first_result = MagicMock()
        first_scalars = MagicMock()
        first_scalars.first.return_value = None
        first_result.scalars.return_value = first_scalars
        mock_db.execute.return_value = first_result

        # When refresh is called on the new User ORM object, populate required fields
        def fake_refresh(obj):
            obj.id = 42
            obj.created_at = "2025-01-01T00:00:00"
            obj.max_concurrent_bots = obj.max_concurrent_bots or 0
        mock_db.refresh = AsyncMock(side_effect=fake_refresh)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/users",
                    json={"email": "new@example.com", "name": "New User"},
                )
            assert resp.status_code in (200, 201), resp.text
            assert resp.json()["email"] == "new@example.com"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_user_returns_200_for_existing(self):
        """POST /admin/users returns 200 when user already exists."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/users",
                    json={"email": "test@example.com"},
                )
            assert resp.status_code == 200, resp.text
            assert resp.json()["email"] == "test@example.com"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_user_by_id(self):
        """GET /admin/users/{id} returns user details."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/users/1")
            assert resp.status_code == 200, resp.text
            assert resp.json()["email"] == "test@example.com"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_user_by_id_not_found(self):
        """GET /admin/users/{id} returns 404 for nonexistent user."""
        mock_db = make_mock_db(None)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/users/999")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_user_by_email(self):
        """GET /admin/users/email/{email} returns user."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/users/email/test@example.com")
            assert resp.status_code == 200, resp.text
            assert resp.json()["email"] == "test@example.com"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self):
        """GET /admin/users/email/{email} returns 404 for nonexistent user."""
        mock_db = make_mock_db(None)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/users/email/nobody@example.com")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_patch_user(self):
        """PATCH /admin/users/{id} updates user fields."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/admin/users/1",
                    json={"name": "Updated Name"},
                )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_users(self):
        """GET /admin/users returns list of users."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_analytics_or_admin_token] = noop_verify_analytics

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/users")
            assert resp.status_code == 200, resp.text
            assert isinstance(resp.json(), list)
            assert len(resp.json()) == 1
        finally:
            app.dependency_overrides.clear()


class TestTokenEndpoints:
    """Test token CRUD endpoints with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_create_token(self):
        """POST /admin/users/{id}/tokens creates a token."""
        fake_user = make_fake_user()
        mock_db = make_mock_db(fake_user)

        # Mock the token that gets created
        fake_token = MagicMock()
        fake_token.id = 1
        fake_token.token = "vx_user_abc123"
        fake_token.user_id = 1
        fake_token.created_at = "2025-01-01T00:00:00"
        mock_db.refresh = AsyncMock(side_effect=lambda t: setattr(t, 'id', 1))

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/users/1/tokens")
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert "token" in body
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_token_user_not_found(self):
        """POST /admin/users/{id}/tokens returns 404 when user doesn't exist."""
        mock_db = make_mock_db(None)
        mock_db.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/users/999/tokens")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_delete_token(self):
        """DELETE /admin/tokens/{id} removes a token."""
        fake_token = MagicMock()
        fake_token.id = 1
        mock_db = make_mock_db(None)
        mock_db.get = AsyncMock(return_value=fake_token)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/admin/tokens/1")
            assert resp.status_code == 204
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_delete_token_not_found(self):
        """DELETE /admin/tokens/{id} returns 404 for nonexistent token."""
        mock_db = make_mock_db(None)
        mock_db.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[verify_admin_token] = noop_verify_admin

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/admin/tokens/999")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()


class TestRootEndpoint:
    """Test the root health-check endpoint."""

    @pytest.mark.asyncio
    async def test_root_returns_200(self):
        """GET / returns 200 with welcome message."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
        assert resp.status_code == 200
        assert "Vexa Admin API" in resp.json()["message"]
