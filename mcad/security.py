"""Reject unauthenticated administration before buffering request bodies."""
import hmac

from starlette.responses import JSONResponse


class ProtectedRequestsMiddleware:
    def __init__(self, app, admin_token):
        self.app = app
        self.admin_token = admin_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "").rstrip("/")
        method = scope.get("method", "GET")
        is_admin = path == "/api/admin" or path.startswith("/api/admin/")
        is_incident_write = method in {"POST", "PATCH", "PUT", "DELETE"} and (path == "/api/incidents" or path.startswith("/api/incidents/"))
        if method == "OPTIONS" or not (is_admin or is_incident_write):
            return await self.app(scope, receive, send)
        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        if not self.admin_token:
            return await JSONResponse({"detail": "Administration is not configured on this server."}, status_code=503)(scope, receive, send)
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode("utf-8"), self.admin_token.encode("utf-8")):
            return await JSONResponse({"detail": "A valid administrator bearer token is required."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        # XLSX uploads have their own bounded streaming reader, preserving low memory.
        if path == "/api/admin/import-workbook" or method in {"GET", "HEAD"}:
            return await self.app(scope, receive, send)
        maximum = 1024 * 1024
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > maximum:
                return await JSONResponse({"detail": "Administrator JSON bodies must be at most 1 MiB."}, status_code=413)(scope, receive, send)
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

        return await self.app(scope, replay, send)
