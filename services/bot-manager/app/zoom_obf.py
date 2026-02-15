import base64
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


class ZoomOBFError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _get_nested_zoom_oauth(user_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(user_data, dict):
        return {}
    zoom_data = user_data.get("zoom")
    if not isinstance(zoom_data, dict):
        return {}
    oauth_data = zoom_data.get("oauth")
    if not isinstance(oauth_data, dict):
        return {}
    return oauth_data


def _parse_expiry_to_epoch(expires_at: Any) -> Optional[int]:
    if expires_at is None:
        return None
    if isinstance(expires_at, (int, float)):
        return int(expires_at)
    if isinstance(expires_at, str):
        value = expires_at.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def resolve_zoom_access_token_from_user_data(
    user_data: Optional[Dict[str, Any]],
    *,
    now_epoch: Optional[int] = None,
    min_validity_seconds: int = 60,
) -> Optional[str]:
    oauth_data = _get_nested_zoom_oauth(user_data)
    access_token = oauth_data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None

    expires_at_epoch = _parse_expiry_to_epoch(oauth_data.get("expires_at"))
    if expires_at_epoch is None:
        return None

    now_ts = now_epoch if now_epoch is not None else int(datetime.now(timezone.utc).timestamp())
    if expires_at_epoch <= now_ts + min_validity_seconds:
        return None
    return access_token


def get_zoom_refresh_token(user_data: Optional[Dict[str, Any]]) -> Optional[str]:
    oauth_data = _get_nested_zoom_oauth(user_data)
    refresh_token = oauth_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        return None
    return refresh_token


def get_zoom_oauth_client_credentials() -> tuple[str, str]:
    client_id = os.getenv("ZOOM_OAUTH_CLIENT_ID") or os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_OAUTH_CLIENT_SECRET") or os.getenv("ZOOM_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ZoomOBFError(
            "Zoom OAuth client credentials are not configured",
            code="ZOOM_OAUTH_NOT_CONFIGURED",
            status_code=500,
        )
    return client_id, client_secret


async def refresh_zoom_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    http_client_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}"}
    params = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    if http_client_factory is None:
        from httpx import AsyncClient as http_client_factory

    try:
        async with http_client_factory(timeout=15.0) as client:
            resp = await client.post("https://zoom.us/oauth/token", params=params, headers=headers)
    except Exception as exc:
        raise ZoomOBFError(
            f"Failed to contact Zoom OAuth endpoint: {exc}",
            code="ZOOM_TOKEN_REFRESH_FAILED",
            status_code=502,
        ) from exc

    if resp.status_code >= 400:
        raise ZoomOBFError(
            f"Zoom token refresh failed with status {resp.status_code}: {resp.text}",
            code="ZOOM_TOKEN_REFRESH_FAILED",
            status_code=502,
        )

    payload = resp.json()
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ZoomOBFError(
            "Zoom token refresh response is missing access_token",
            code="ZOOM_TOKEN_REFRESH_FAILED",
            status_code=502,
        )

    expires_in = payload.get("expires_in", 3600)
    try:
        expires_in_int = int(expires_in)
    except (TypeError, ValueError):
        expires_in_int = 3600

    return {
        "access_token": access_token,
        "refresh_token": payload.get("refresh_token") or refresh_token,
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + expires_in_int,
        "scope": payload.get("scope"),
    }


async def mint_zoom_obf_token(
    access_token: str,
    meeting_id: str,
    http_client_factory: Optional[Callable[..., Any]] = None,
) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"type": "onbehalf", "meeting_id": meeting_id}

    if http_client_factory is None:
        from httpx import AsyncClient as http_client_factory

    try:
        async with http_client_factory(timeout=15.0) as client:
            resp = await client.get("https://api.zoom.us/v2/users/me/token", params=params, headers=headers)
    except Exception as exc:
        raise ZoomOBFError(
            f"Failed to contact Zoom OBF endpoint: {exc}",
            code="ZOOM_OBF_MINT_FAILED",
            status_code=502,
        ) from exc

    if resp.status_code >= 400:
        raise ZoomOBFError(
            f"Zoom OBF mint failed with status {resp.status_code}: {resp.text}",
            code="ZOOM_OBF_MINT_FAILED",
            status_code=502,
        )

    payload = resp.json()
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ZoomOBFError(
            "Zoom OBF response missing token",
            code="ZOOM_OBF_MINT_FAILED",
            status_code=502,
        )
    return token
