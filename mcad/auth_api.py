"""Same-origin endpoints for signing in and managing the review team."""
from ipaddress import ip_address

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from . import auth


def secure_cookie(request, settings):
    if settings.admin_secure_cookie is not None:
        return settings.admin_secure_cookie
    return request.url.scheme == "https"


def session_cookie_name(request, settings):
    return "__Host-mcad-session" if secure_cookie(request, settings) else "mcad-session"


def session_token(request, settings):
    return request.cookies.get(session_cookie_name(request, settings))


def _set_session_cookie(request, response, settings, token):
    response.set_cookie(session_cookie_name(request, settings), token, max_age=auth.SESSION_SECONDS,
                        path="/", secure=secure_cookie(request, settings), httponly=True, samesite="strict")


def trusted_railway_edge(request, settings):
    return (settings.admin_trust_railway_proxy and request.headers.get("x-forwarded-proto") == "https"
            and bool(request.headers.get("x-railway-request-id")))


def _address(request, settings):
    if trusted_railway_edge(request, settings):
        try:
            return str(ip_address(request.headers.get("x-real-ip", "")))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def auth_router(factory, settings, require_admin):
    router = APIRouter()

    @router.get("/api/auth/session")
    def get_session(request: Request):
        return {"user": auth.get_session_user(factory, session_token(request, settings))}

    @router.post("/api/auth/login")
    def login(request: Request, response: Response, payload: dict = Body(...)):
        try:
            user, token = auth.login(factory, payload.get("email"), payload.get("password"), _address(request, settings),
                                     session_token(request, settings))
        except auth.InvalidCredentials:
            raise HTTPException(401, "That email and password did not match.") from None
        except auth.TooManyAttempts:
            raise HTTPException(429, "Too many sign-in attempts. Try again in a few minutes.",
                                headers={"Retry-After": str(int(auth.ATTEMPT_WINDOW.total_seconds()))}) from None
        _set_session_cookie(request, response, settings, token)
        return {"user": user}

    @router.post("/api/auth/accept-invite")
    def accept_invite(request: Request, response: Response, payload: dict = Body(...)):
        try:
            user, token = auth.accept_invite(factory, payload.get("token"), payload.get("password"), _address(request, settings),
                                             session_token(request, settings))
        except auth.InvalidInvite:
            raise HTTPException(410, "This invitation has expired, was already used, or is no longer valid.") from None
        except auth.TooManyAttempts:
            raise HTTPException(429, "Too many attempts. Try again in a few minutes.",
                                headers={"Retry-After": str(int(auth.ATTEMPT_WINDOW.total_seconds()))}) from None
        _set_session_cookie(request, response, settings, token)
        return {"user": user}

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.revoke_session(factory, session_token(request, settings))
        response.delete_cookie(session_cookie_name(request, settings), path="/", secure=secure_cookie(request, settings),
                               httponly=True, samesite="strict")
        return {"user": None}

    @router.get("/api/admin/team", dependencies=[Depends(require_admin)])
    def get_team():
        return auth.list_team(factory)

    @router.post("/api/admin/team/invites", status_code=201, dependencies=[Depends(require_admin)])
    def invite_member(payload: dict = Body(...)):
        try:
            invite, token = auth.issue_invite(factory, payload.get("email"), payload.get("role", "reviewer"))
        except auth.AccountConflict as error:
            raise HTTPException(409, str(error)) from None
        return {"invite": invite, "token": token}

    @router.delete("/api/admin/team/invites/{invite_id}", dependencies=[Depends(require_admin)])
    def revoke_invite(invite_id: str):
        invite = auth.revoke_invite(factory, invite_id)
        if invite is None:
            raise HTTPException(404, "No active invitation found.")
        return {"invite": invite}

    @router.patch("/api/admin/team/members/{user_id}", dependencies=[Depends(require_admin)])
    def edit_member(user_id: str, payload: dict = Body(...)):
        try:
            user = auth.update_member(factory, user_id, payload)
        except auth.AccountConflict as error:
            raise HTTPException(409, str(error)) from None
        if user is None:
            raise HTTPException(404, "Team member not found.")
        return {"user": user}

    return router
