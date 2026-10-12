"""
Production middleware stack -- rate limiting, request logging, security headers.

Register in the application entry point (Starlette applies middleware inside-out):

    from middleware import RateLimitMiddleware, RequestLogMiddleware, SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(RateLimitMiddleware)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP sliding-window rate limiter.

    Parameters
    ----------
    general_limit : int
        Max requests per *window* seconds for normal routes (default 100).
    auth_limit : int
        Tighter limit applied to any path containing ``/auth/`` (default 10).
    window : int
        Window size in seconds (default 60).
    """

    def __init__(
        self,
        app,
        general_limit: int = 300,
        auth_limit: int = 10,
        window: int = 60,
    ) -> None:
        super().__init__(app)
        self.general_limit = general_limit
        self.auth_limit = auth_limit
        self.window = window
        # Auth and general traffic are counted separately: a polling dashboard
        # must not exhaust the login budget, and login attempts must not hide
        # inside general traffic.
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = Lock()

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        path = request.url.path
        now = time.time()

        bucket = "auth" if "/auth/" in path else "general"
        key = (ip, bucket)
        limit = self.auth_limit if bucket == "auth" else self.general_limit

        with self._lock:
            # Evict timestamps outside the current window
            self._hits[key] = [t for t in self._hits[key] if now - t < self.window]

            if len(self._hits[key]) >= limit:
                oldest = self._hits[key][0]
                retry_after = max(int(self.window - (now - oldest)), 1)
                log.warning(
                    "rate_limit_exceeded ip=%s path=%s limit=%d", ip, path, limit
                )
                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

            self._hits[key].append(now)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Attach security-relevant response headers to every reply.

    - ``X-Content-Type-Options: nosniff`` — prevents MIME-sniffing attacks.
    - ``X-Frame-Options: DENY`` — disallows iframe embedding.
    - ``X-XSS-Protection: 1; mode=block`` — legacy XSS filter for older browsers.
    - ``Referrer-Policy: strict-origin-when-cross-origin`` — limits referrer leakage.
    - ``Cache-Control: no-store`` — added only for ``/api/`` paths to prevent
      sensitive data being cached by intermediaries.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    Structured request/response logging.

    Health-check routes (``/api/health``, ``/api/status``) are skipped to avoid
    flooding logs with liveness-probe noise.

    Log entries include: method, path, status code, duration in ms, client IP.
    """

    # Routes whose access logs are suppressed (liveness probes etc.)
    _SILENT_PATHS: frozenset[str] = frozenset({"/api/health", "/api/status"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._SILENT_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        ip = request.client.host if request.client else "unknown"
        log.info(
            "%s %s %d %.1fms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            ip,
        )
        return response
