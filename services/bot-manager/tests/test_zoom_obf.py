import asyncio
import unittest

from app.zoom_obf import (
    ZoomOBFError,
    get_zoom_oauth_client_credentials,
    resolve_zoom_access_token_from_user_data,
    refresh_zoom_access_token,
    mint_zoom_obf_token,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return self._response

    async def get(self, *args, **kwargs):
        return self._response


class ZoomOBFTests(unittest.TestCase):
    def test_resolve_zoom_access_token_from_user_data_valid(self):
        token = resolve_zoom_access_token_from_user_data(
            {
                "zoom": {
                    "oauth": {
                        "access_token": "abc",
                        "expires_at": 2000000000,
                    }
                }
            },
            now_epoch=1700000000,
        )
        self.assertEqual(token, "abc")

    def test_resolve_zoom_access_token_from_user_data_expired(self):
        token = resolve_zoom_access_token_from_user_data(
            {
                "zoom": {
                    "oauth": {
                        "access_token": "abc",
                        "expires_at": 1700000000,
                    }
                }
            },
            now_epoch=1700000000,
        )
        self.assertIsNone(token)

    def test_get_zoom_oauth_client_credentials_fallback(self):
        import os

        old_oauth_id = os.environ.pop("ZOOM_OAUTH_CLIENT_ID", None)
        old_oauth_secret = os.environ.pop("ZOOM_OAUTH_CLIENT_SECRET", None)
        old_id = os.environ.get("ZOOM_CLIENT_ID")
        old_secret = os.environ.get("ZOOM_CLIENT_SECRET")

        try:
            os.environ["ZOOM_CLIENT_ID"] = "cid"
            os.environ["ZOOM_CLIENT_SECRET"] = "csecret"
            cid, csecret = get_zoom_oauth_client_credentials()
            self.assertEqual(cid, "cid")
            self.assertEqual(csecret, "csecret")
        finally:
            if old_oauth_id is not None:
                os.environ["ZOOM_OAUTH_CLIENT_ID"] = old_oauth_id
            if old_oauth_secret is not None:
                os.environ["ZOOM_OAUTH_CLIENT_SECRET"] = old_oauth_secret
            if old_id is not None:
                os.environ["ZOOM_CLIENT_ID"] = old_id
            else:
                os.environ.pop("ZOOM_CLIENT_ID", None)
            if old_secret is not None:
                os.environ["ZOOM_CLIENT_SECRET"] = old_secret
            else:
                os.environ.pop("ZOOM_CLIENT_SECRET", None)

    def test_refresh_zoom_access_token_success(self):
        response = _FakeResponse(
            status_code=200,
            payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "scope": "user:read:token",
            },
        )

        refreshed = asyncio.run(
            refresh_zoom_access_token(
                "old-refresh",
                "cid",
                "csecret",
                http_client_factory=lambda *args, **kwargs: _FakeAsyncClient(response),
            )
        )

        self.assertEqual(refreshed["access_token"], "new-access")
        self.assertEqual(refreshed["refresh_token"], "new-refresh")
        self.assertEqual(refreshed["scope"], "user:read:token")
        self.assertIsInstance(refreshed["expires_at"], int)

    def test_mint_zoom_obf_token_success(self):
        response = _FakeResponse(status_code=200, payload={"token": "obf-token"})
        token = asyncio.run(
            mint_zoom_obf_token(
                "access",
                "1234567890",
                http_client_factory=lambda *args, **kwargs: _FakeAsyncClient(response),
            )
        )

        self.assertEqual(token, "obf-token")

    def test_mint_zoom_obf_token_failure(self):
        response = _FakeResponse(status_code=400, payload={}, text="bad request")
        with self.assertRaises(ZoomOBFError) as exc:
            asyncio.run(
                mint_zoom_obf_token(
                    "access",
                    "1234567890",
                    http_client_factory=lambda *args, **kwargs: _FakeAsyncClient(response),
                )
            )

        self.assertEqual(exc.exception.code, "ZOOM_OBF_MINT_FAILED")

if __name__ == "__main__":
    unittest.main()
