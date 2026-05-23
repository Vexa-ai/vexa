"""Security headers middleware for FastAPI services."""

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _same_host_frame_ancestor(request: Request) -> str | None:
    """Allow dashboard-on-same-host embedding for remote browser VNC pages."""
    referer = request.headers.get("referer") or request.headers.get("origin")
    if not referer:
        return None

    try:
        referer_url = urlparse(referer)
    except ValueError:
        return None

    if referer_url.scheme not in {"http", "https"} or not referer_url.hostname:
        return None
    if referer_url.hostname != request.url.hostname:
        return None

    origin = f"{referer_url.scheme}://{referer_url.hostname}"
    if referer_url.port:
        origin = f"{origin}:{referer_url.port}"
    return origin


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Allow iframe embedding for browser session VNC pages (noVNC embedded in dashboard).
        # Dashboard runs on a different port (3002) than the gateway (8066), so SAMEORIGIN
        # won't work. Use Content-Security-Policy frame-ancestors instead (modern browsers)
        # and omit X-Frame-Options for VNC paths. All other routes keep DENY.
        path = request.url.path
        if path.startswith("/b/") and "/vnc/" in path:
            frame_ancestors = ["'self'", "http://localhost:*", "https://localhost:*"]
            same_host_ancestor = _same_host_frame_ancestor(request)
            if same_host_ancestor:
                frame_ancestors.append(same_host_ancestor)
            response.headers["Content-Security-Policy"] = f"frame-ancestors {' '.join(frame_ancestors)}"
            # Don't set X-Frame-Options — it overrides CSP in some browsers
        else:
            response.headers["X-Frame-Options"] = "DENY"
        return response
