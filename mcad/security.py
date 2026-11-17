"""Reject unauthenticated administration before buffering request bodies."""
import hmac
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import get_session_user
from .auth_api import secure_cookie, session_token, trusted_railway_edge


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_origin_write(request, settings):
    # The custom header requires a CORS preflight if sent from another origin.
    if request.headers.get("x-mcad-request") != "1":
        return False
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        return False
    origin_header = request.headers.get("origin")
    if origin_header is None:
        return True
    try:
        origin = urlsplit(origin_header)
    except ValueError:
        return False
    scheme = "https" if secure_cookie(request, settings) else request.url.scheme
    authority = request.url.netloc
    if trusted_railway_edge(request, settings):
        authority = request.headers.get("x-forwarded-host", authority)
    return (origin.scheme == scheme and origin.netloc.lower() == authority.lower()
            and not (origin.path or origin.query or origin.fragment))


def _reviewer_route(path, method):
    if method in {"GET", "HEAD"}:
        if path in {"/api/admin/catalogs", "/api/admin/candidates"}:
            return True
        prefix, _, candidate = path.rpartition("/")
        return prefix == "/api/admin/candidates" and bool(candidate)
    if method == "POST":
        prefix, _, action = path.rpartition("/")
        return action in {"approve", "reject"} and prefix.rpartition("/")[0] == "/api/admin/candidates"
    return False


class ProtectedRequestsMiddleware:
    def __init__(self, app, settings, factory):
        self.app = app
        self.settings = settings
        self.factory = factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "").rstrip("/")
        method = scope.get("method", "GET")
        is_admin = path == "/api/admin" or path.startswith("/api/admin/")
        is_auth = path == "/api/auth" or path.startswith("/api/auth/")
        is_admin_page = path == "/admin" or path.startswith("/admin/")
        is_incident_write = method not in SAFE_METHODS and (path == "/api/incidents" or path.startswith("/api/incidents/"))
        protected = is_admin or is_incident_write
        if method == "OPTIONS" or not (protected or is_auth or is_admin_page):
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        async def reject(status, detail, headers=None):
            return await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, private_send)

        request = Request(scope)
        if protected:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            admin_token = self.settings.admin_api_token
            bearer_admin = bool(admin_token) and scheme.lower() == "bearer" and hmac.compare_digest(
                token.encode("utf-8"), admin_token.encode("utf-8")
            )
            if bearer_admin:
                request.state.principal = {"role": "admin", "id": None}
            else:
                cookie = session_token(request, self.settings)
                if not cookie:
                    return await reject(401, "Sign in to open the administration workspace.", {"WWW-Authenticate": "Bearer"})
                if method not in SAFE_METHODS and not _same_origin_write(request, self.settings):
                    return await reject(403, "This action requires a request from the administration site.")
                try:
                    user = await run_in_threadpool(get_session_user, self.factory, cookie)
                except SQLAlchemyError:
                    return await reject(503, "The database is temporarily unavailable.")
                if user is None:
                    return await reject(401, "Your session expired. Sign in again.")
                if user["role"] == "reviewer" and not _reviewer_route(path, method):
                    return await reject(403, "Administrator access is required for this action.")
                request.state.principal = user
        elif is_auth and method not in SAFE_METHODS and not _same_origin_write(request, self.settings):
            return await reject(403, "This action requires a request from the administration site.")

        # XLSX uploads have their own bounded streaming reader, preserving low memory.
        if is_admin_page or path == "/api/admin/import-workbook" or method in SAFE_METHODS:
            return await self.app(scope, receive, private_send)
        maximum = 16 * 1024 if is_auth else 1024 * 1024
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > maximum:
                return await reject(413, "The request is larger than this endpoint accepts.")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        return await self.app(scope, replay, private_send)
