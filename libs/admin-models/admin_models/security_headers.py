"""Security headers middleware for FastAPI services."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self' http://localhost:* https://localhost:*"
            # Don't set X-Frame-Options — it overrides CSP in some browsers
        else:
            response.headers["X-Frame-Options"] = "DENY"
        return response
