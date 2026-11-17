from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from mcad.api import create_app
from mcad.auth import create_admin, reset_password
from mcad.auth_models import AdminInvite, AdminSession, AdminUser
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, utcnow


CSRF = {"X-MCAD-Request": "1", "Origin": "https://admin.example.test"}
PASSWORD = "fixture-password-for-admin"
REVIEWER_PASSWORD = "fixture-password-for-reviewer"
BASE_URL = "https://admin.example.test"


@pytest.fixture
def accounts(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'auth.sqlite3'}")
    migrate(engine)
    factory = get_session_factory(engine)
    create_admin(factory, "Admin@example.test", PASSWORD)
    app = create_app(Settings(enable_workers=False, admin_secure_cookie=True), engine)
    yield app, factory
    engine.dispose()


def client_for(app):
    return TestClient(app, base_url=BASE_URL, headers=CSRF)


def login_admin(client):
    return client.post("/api/auth/login", json={"email": "ADMIN@example.test", "password": PASSWORD})


def new_invite(client, email="reviewer@example.test", role="reviewer"):
    response = client.post("/api/admin/team/invites", json={"email": email, "role": role})
    assert response.status_code == 201
    return response.json()


def accept(client, invitation):
    return client.post("/api/auth/accept-invite", json={"token": invitation["token"], "password": REVIEWER_PASSWORD})


def test_secure_session_is_hashed_and_expires_or_can_be_revoked(accounts):
    app, factory = accounts
    with client_for(app) as client:
        assert client.get("/api/auth/session").json() == {"user": None}
        assert client.get("/api/admin/team").status_code == 401
        result = login_admin(client)
        assert result.status_code == 200
        assert result.json()["user"]["email"] == "admin@example.test"
        assert result.headers["cache-control"] == "no-store"
        cookie = result.headers["set-cookie"]
        for attribute in ("__Host-mcad-session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            assert attribute in cookie
        assert "Domain=" not in cookie
        token = client.cookies["__Host-mcad-session"]
        with factory() as session:
            assert token != session.scalar(select(AdminSession.token_hash))
            assert PASSWORD != session.scalar(select(AdminUser.password_hash))
        assert client.get("/api/admin/team").status_code == 200
        assert client.post("/api/auth/logout", json={}).json() == {"user": None}
        assert client.get("/api/admin/team", headers={"Cookie": f"__Host-mcad-session={token}"}).status_code == 401
        assert login_admin(client).status_code == 200
        with factory.begin() as session:
            session.execute(update(AdminSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        assert client.get("/api/auth/session").json() == {"user": None}
        assert client.get("/api/admin/team").status_code == 401


def test_reviewer_can_review_but_cannot_manage_people_or_settings(accounts):
    app, factory = accounts
    with factory.begin() as session:
        candidate = Candidate(title="Reviewable incident", source_url="https://example.test/report", summary="Summary")
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id
    with client_for(app) as admin, client_for(app) as reviewer:
        admin_user = login_admin(admin).json()["user"]
        invitation = new_invite(admin)
        result = accept(reviewer, invitation)
        assert result.status_code == 200
        user = result.json()["user"]
        assert user["role"] == "reviewer"
        assert reviewer.get("/api/admin/candidates").status_code == 200
        assert reviewer.get(f"/api/admin/candidates/{candidate_id}").status_code == 200
        assert reviewer.get("/api/admin/catalogs").status_code == 200
        blocked = [
            reviewer.get("/api/admin/team"), reviewer.get("/api/admin/ai-monitor"),
            reviewer.post("/api/admin/team/invites", json={}), reviewer.post("/api/incidents", json={}),
            reviewer.post("/api/admin/import-workbook", json={}),
        ]
        assert all(response.status_code == 403 for response in blocked)
        assert reviewer.post(f"/api/admin/candidates/{candidate_id}/approve", json={"title": "Verified incident"}).status_code == 200
        assert admin.patch(f"/api/admin/team/members/{admin_user['id']}", json={"active": False}).status_code == 409
        assert admin.patch(f"/api/admin/team/members/{user['id']}", json={"active": False}).status_code == 200
        assert reviewer.get("/api/admin/candidates").status_code == 401
        assert admin.patch(f"/api/admin/team/members/{user['id']}", json={"active": True}).status_code == 200
        assert reviewer.get("/api/admin/candidates").status_code == 401


def test_invitation_is_single_use_even_during_competing_requests(accounts):
    app, factory = accounts
    with client_for(app) as admin:
        assert login_admin(admin).status_code == 200
        invitation = new_invite(admin)
        assert invitation["token"] not in admin.get("/api/admin/team").text
        with factory() as session:
            assert session.scalar(select(AdminInvite.token_hash)) != invitation["token"]

        def redeem(_):
            with client_for(app) as reviewer:
                return accept(reviewer, invitation).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(redeem, range(2))) == [200, 410]
        team = admin.get("/api/admin/team").json()
        assert len(team["members"]) == 2
        assert team["invites"][0]["status"] == "accepted"


def test_invitation_reissue_revocation_and_expiry(accounts):
    app, factory = accounts
    with client_for(app) as admin, client_for(app) as recipient:
        assert login_admin(admin).status_code == 200
        initial = new_invite(admin)
        updated = new_invite(admin)
        assert accept(recipient, initial).status_code == 410
        assert admin.delete(f"/api/admin/team/invites/{updated['invite']['id']}").json()["invite"]["status"] == "revoked"
        assert accept(recipient, updated).status_code == 410
        expiring = new_invite(admin)
        with factory.begin() as session:
            session.execute(update(AdminInvite).values(expires_at=utcnow() - timedelta(seconds=1)))
        assert accept(recipient, expiring).status_code == 410


def test_csrf_rejection_precedes_mutations_and_safe_transport_does_not_echo_passwords(accounts):
    app, _ = accounts
    with TestClient(app, base_url=BASE_URL) as client:
        credentials = {"email": "admin@example.test", "password": PASSWORD}
        login_url = "/api/auth/login"
        assert client.post(login_url, json=credentials).status_code == 403
        assert client.post(login_url, headers={**CSRF, "Origin": "https://other.example.test"}, json=credentials).status_code == 403
        assert client.post(login_url, headers={**CSRF, "Sec-Fetch-Site": "same-site"}, json=credentials).status_code == 403
        invalid = client.post(login_url, headers=CSRF, json=[credentials])
        assert invalid.status_code == 422
        assert PASSWORD not in invalid.text
        assert client.post(login_url, headers=CSRF, json=credentials).status_code == 200
        assert client.post("/api/admin/team/invites", json={"email": "new@example.test"}).status_code == 403
        assert client.post("/api/auth/logout", headers={**CSRF, "Origin": "https://other.example.test"}).status_code == 403
        assert client.get("/api/admin/team").json()["invites"] == []


def test_rate_limit_persists_and_password_reset_revokes_existing_sessions(accounts, monkeypatch):
    app, factory = accounts
    observed = []

    def never_validated(password, _stored):
        observed.append(password)
        return False

    with client_for(app) as admin:
        assert login_admin(admin).status_code == 200
        with monkeypatch.context() as patch, client_for(app) as intruder:
            patch.setattr("mcad.auth.verify_password", never_validated)
            for _ in range(10):
                result = intruder.post("/api/auth/login", json={"email": "missing@example.test", "password": "Wrong"})
                assert result.status_code == 401
            assert len(observed) == 10
        with client_for(app) as another_client:
            blocked = another_client.post("/api/auth/login", json={"email": "MISSING@example.test", "password": "Wrong"})
            assert blocked.status_code == 429
            assert blocked.headers["retry-after"] == "600"
        reset_password(factory, "admin@example.test", "a-brand-new-fixture-password")
        assert admin.get("/api/admin/team").status_code == 401
        assert login_admin(admin).status_code == 401
        assert admin.post("/api/auth/login", json={"email": "admin@example.test", "password": "a-brand-new-fixture-password"}).status_code == 200


def test_railway_edge_preserves_https_origin_through_http_upstream(accounts):
    local_app, _ = accounts
    settings = Settings(enable_workers=False, admin_secure_cookie=True, admin_trust_railway_proxy=True)
    app = create_app(settings, local_app.state.engine)
    edge_headers = {**CSRF, "X-Forwarded-Host": "admin.example.test", "X-Forwarded-Proto": "https",
                    "X-Railway-Request-Id": "test-edge", "X-Real-IP": "192.0.2.44"}
    password = {"email": "admin@example.test", "password": PASSWORD}
    with TestClient(app, base_url="http://internal:8080") as client:
        assert client.post("/api/auth/login", headers=CSRF, json=password).status_code == 403
        assert client.post("/api/auth/login", headers={**edge_headers, "Origin": "https://bad.test"}, json=password).status_code == 403
        result = client.post("/api/auth/login", headers=edge_headers, json=password)
        assert result.status_code == 200
        assert "__Host-mcad-session=" in result.headers["set-cookie"]
