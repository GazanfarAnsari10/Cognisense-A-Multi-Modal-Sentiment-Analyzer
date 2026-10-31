import hashlib
import os
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from flask import Flask
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from admin_portal import admin_blueprint
from admin_provision import ProvisioningError, main as provision_main, promote_initial_admin
from auth import _csrf_hash, _new_csrf_token, auth_blueprint
from database.models import ApiKey, AuditLog, AuthSession, Base, Plan, Project, UsageRecord, User
from developer_portal import developer_blueprint
from projects import projects_blueprint


class AdminPortalTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "AUTH_SECRET_KEY": "admin-portal-test-secret-with-at-least-32-bytes",
            "AUTH_SESSION_TTL_SECONDS": "3600",
            "AUTH_COOKIE_SECURE": "false",
            "AUTH_COOKIE_SAMESITE": "Lax",
        }, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.app = Flask("admin-portal-tests")
        self.app.config.update(TESTING=True, DATABASE_SESSION_FACTORY=self.session_factory)
        self.app.register_blueprint(auth_blueprint)
        self.app.register_blueprint(developer_blueprint)
        self.app.register_blueprint(projects_blueprint)
        self.app.register_blueprint(admin_blueprint)
        self.client = self.app.test_client()
        self.user_id, self.session_cookie, self.csrf_cookie = self._create_user("regular@example.com")
        self.client.set_cookie("cognisense_session", self.session_cookie)
        self.client.set_cookie("cognisense_csrf", self.csrf_cookie)
        self.addCleanup(self.engine.dispose)

    def _create_user(self, email, password_hash="test-hash"):
        session_cookie = uuid.uuid4().hex + uuid.uuid4().hex
        csrf_token = _new_csrf_token(os.environ["AUTH_SECRET_KEY"])
        with self.session_factory.begin() as session:
            user = User(name="Test User", email=email, password_hash=password_hash, status="active")
            session.add(user)
            session.flush()
            user_id = user.id
            session.add(AuthSession(
                user_id=user_id,
                token_hash=hashlib.sha256(session_cookie.encode()).hexdigest(),
                csrf_token_hash=_csrf_hash(csrf_token),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
        return user_id, session_cookie, csrf_token

    def _make_admin(self):
        return promote_initial_admin(self.session_factory, "regular@example.com", "test-operator")

    def test_anonymous_and_regular_users_are_denied_and_attempts_audited(self):
        anonymous = self.app.test_client()
        page = anonymous.get("/admin")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/developer/login", page.headers["Location"])
        self.assertEqual(anonymous.get("/api/v1/admin").status_code, 401)
        self.assertEqual(anonymous.get("/api/v1/admin/audit").status_code, 401)

        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin").status_code, 403)
        denied_audit = self.client.get("/api/v1/admin/audit")
        self.assertEqual(denied_audit.status_code, 403)
        with self.session_factory() as session:
            rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
            self.assertGreaterEqual(len(rows), 6)
            self.assertTrue(all(row.outcome == "denied" for row in rows))

    def test_admin_can_access_page_api_and_audit_records(self):
        self.assertEqual(self._make_admin(), self.user_id)
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("id=\"view-title\">Overview", page.get_data(as_text=True))
        self.assertIn("User Management", page.get_data(as_text=True))
        self.assertIn("Plans &amp; Limits", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/v1/admin").status_code, 200)
        audit = self.client.get("/api/v1/admin/audit")
        self.assertEqual(audit.status_code, 200)
        records = audit.get_json()["data"]["records"]
        self.assertTrue(any(record["action"] == "admin.provision" for record in records))
        self.assertTrue(any(record["action"] == "admin.access" and record["outcome"] == "allowed" for record in records))
        self.assertNotIn("test-hash", audit.get_data(as_text=True))
        audit_end = datetime.now(timezone.utc)
        audit_start = audit_end - timedelta(days=30)
        paged_audit = self.client.get(
            f"/api/v1/admin/audit?per_page=1&from={audit_start.isoformat().replace('+00:00', 'Z')}&to={audit_end.isoformat().replace('+00:00', 'Z')}"
        )
        self.assertEqual(paged_audit.status_code, 200)
        self.assertEqual(paged_audit.get_json()["data"]["pagination"]["per_page"], 1)
        self.assertEqual(self.client.get("/api/v1/admin/audit?from=2020-01-01T00:00:00Z&to=2020-07-01T00:00:00Z").status_code, 400)

    def test_registration_role_fields_and_profile_requests_cannot_self_promote(self):
        client = self.app.test_client()
        csrf = client.get("/api/v1/auth/csrf").get_json()["data"]["csrf_token"]
        response = client.post(
            "/api/v1/auth/register",
            json={"name": "Self Promoter", "email": "self@example.com", "password": "correct-horse-battery", "role": "admin", "is_admin": True},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 202)
        with self.session_factory() as session:
            registered = session.scalar(select(User).where(User.email == "self@example.com"))
            self.assertEqual(registered.role, "user")
        self.assertEqual(self.client.patch("/api/v1/auth/me", json={"role": "admin"}).status_code, 405)
        self.assertEqual(self.client.post("/api/v1/admin", json={"role": "admin"}).status_code, 405)

    def test_provisioning_requires_explicit_enable_and_confirmation(self):
        args = ["--email", "regular@example.com", "--confirm-email", "regular@example.com", "--actor", "ops-ticket-123"]
        with patch.dict(os.environ, {}, clear=True), patch("admin_provision.get_session_factory") as factory:
            with redirect_stderr(StringIO()):
                self.assertEqual(provision_main(args), 2)
            factory.assert_not_called()

        with patch.dict(os.environ, {"COGNISENSE_ALLOW_ADMIN_PROVISIONING": "1"}), patch("admin_provision.get_session_factory") as factory:
            with redirect_stderr(StringIO()):
                mismatch_args = ["--email", "regular@example.com", "--confirm-email", "other@example.com", "--actor", "ops-ticket-123"]
                self.assertEqual(provision_main(mismatch_args), 2)
            factory.assert_not_called()

        with self.assertRaises(ProvisioningError):
            promote_initial_admin(self.session_factory, "missing@example.com", "ops")
        with patch.dict(os.environ, {"COGNISENSE_ALLOW_ADMIN_PROVISIONING": "1"}), patch(
            "admin_provision.get_session_factory", return_value=self.session_factory
        ), redirect_stdout(StringIO()):
            self.assertEqual(provision_main(args), 0)
        with self.assertRaises(ProvisioningError):
            promote_initial_admin(self.session_factory, "regular@example.com", "ops")
        with self.session_factory() as session:
            provisioning = session.scalars(select(AuditLog).where(AuditLog.action == "admin.provision")).all()
            self.assertCountEqual([row.outcome for row in provisioning], ["denied", "success", "denied"])
            self.assertTrue(all("@" not in row.actor_identifier for row in provisioning))

    def test_database_rejects_unsupported_roles(self):
        with self.assertRaises(IntegrityError):
            with self.session_factory.begin() as session:
                session.add(User(name="Invalid", email="invalid@example.com", role="superadmin"))

    def test_admin_management_requires_role_and_csrf(self):
        endpoints = [
            ("/api/v1/admin/overview", "GET"),
            ("/api/v1/admin/users", "GET"),
            ("/api/v1/admin/projects", "GET"),
            ("/api/v1/admin/plans", "GET"),
            ("/api/v1/admin/keys", "GET"),
            ("/api/v1/admin/usage", "GET"),
        ]
        for path, method in endpoints:
            with self.subTest(path=path):
                self.assertEqual(self.app.test_client().open(path, method=method).status_code, 401)
                self.assertEqual(self.client.open(path, method=method).status_code, 403)
            self.assertEqual(self.app.test_client().get("/api/v1/admin/users?page=invalid").status_code, 401)
            self.assertEqual(self.app.test_client().get("/api/v1/admin/usage?from=invalid").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/plans", json={}).status_code, 403)
        self.assertEqual(self._make_admin(), self.user_id)
        no_csrf = self.app.test_client()
        no_csrf.set_cookie("cognisense_session", self.session_cookie)
        no_csrf.set_cookie("cognisense_csrf", self.csrf_cookie)
        denied = no_csrf.post("/api/v1/admin/plans", json={})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["error"]["code"], "csrf_failed")

    def test_admin_manages_users_projects_keys_and_audits_without_secrets(self):
        self._make_admin()
        with self.session_factory.begin() as session:
            owner = User(name="Other User", email="other-admin-test@example.com", password_hash="never-return-hash")
            session.add(owner)
            session.flush()
            session.add(AuthSession(
                user_id=owner.id,
                token_hash=hashlib.sha256(b"other-session").hexdigest(),
                csrf_token_hash=hashlib.sha256(b"other-csrf").hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            project = Project(owner_user_id=owner.id, display_name="Other Project", public_project_id="public-project-test")
            session.add(project)
            session.flush()
            key = ApiKey(project_id=project.id, key_prefix="cgs_live_publicprefix", key_hash="secret-key-hash-never-returned")
            session.add(key)
            session.flush()
            session.add(UsageRecord(project_id=project.id, api_key_id=key.id, endpoint="/api/v1/analyze", http_status_code=429, processing_duration_ms=0))
            other_user_id, project_row_id, key_id = owner.id, project.id, key.id

        users_response = self.client.get("/api/v1/admin/users?per_page=1")
        self.assertEqual(users_response.status_code, 200)
        self.assertEqual(users_response.get_json()["data"]["pagination"]["per_page"], 1)
        user_response = self.client.get(f"/api/v1/admin/users/{other_user_id}")
        self.assertEqual(user_response.status_code, 200)
        self.assertNotIn("never-return-hash", user_response.get_data(as_text=True))

        changed = self.client.patch(
            f"/api/v1/admin/users/{other_user_id}/status",
            json={"status": "disabled"},
            headers={"X-CSRF-Token": self.csrf_cookie},
        )
        self.assertEqual(changed.status_code, 200)
        with self.session_factory() as session:
            self.assertEqual(session.get(User, other_user_id).status, "disabled")
            self.assertEqual(session.scalar(select(func.count(AuthSession.id)).where(AuthSession.user_id == other_user_id, AuthSession.revoked_at.is_not(None))), 1)

        project_response = self.client.get("/api/v1/admin/projects")
        self.assertEqual(project_response.status_code, 200)
        self.assertIn("other-admin-test@example.com", project_response.get_data(as_text=True))
        invalid_mixed = self.client.patch(
            f"/api/v1/admin/projects/{project_row_id}",
            json={"status": "archived", "plan_id": 999999},
            headers={"X-CSRF-Token": self.csrf_cookie},
        )
        self.assertEqual(invalid_mixed.status_code, 400)
        with self.session_factory() as session:
            self.assertEqual(session.get(Project, project_row_id).status, "active")
        archived = self.client.patch(
            f"/api/v1/admin/projects/{project_row_id}", json={"status": "archived"}, headers={"X-CSRF-Token": self.csrf_cookie}
        )
        self.assertEqual(archived.status_code, 200)
        key_response = self.client.get("/api/v1/admin/keys")
        self.assertEqual(key_response.status_code, 200)
        self.assertNotIn("secret-key-hash-never-returned", key_response.get_data(as_text=True))
        revoked = self.client.post(f"/api/v1/admin/keys/{key_id}/revoke", json={}, headers={"X-CSRF-Token": self.csrf_cookie})
        self.assertEqual(revoked.status_code, 200)
        with self.session_factory() as session:
            self.assertEqual(session.get(ApiKey, key_id).status, "revoked")
            actions = set(session.scalars(select(AuditLog.action)).all())
        self.assertTrue({"admin.user.disabled", "admin.project.archived", "admin.api_key.revoked"}.issubset(actions))

    def test_plan_changes_validate_and_feed_gateway_limits(self):
        self._make_admin()
        invalid = self.client.post("/api/v1/admin/plans", json={
            "name": "Unreasonable", "request_limit_per_minute": -1, "quota_limit_per_day": 0, "active_api_key_limit": 0,
        }, headers={"X-CSRF-Token": self.csrf_cookie})
        self.assertEqual(invalid.status_code, 400)

        created = self.client.post("/api/v1/admin/plans", json={
            "name": "Strict plan", "description": "Runtime test", "request_limit_per_minute": 1,
            "quota_limit_per_day": 0, "active_api_key_limit": 1,
        }, headers={"X-CSRF-Token": self.csrf_cookie})
        self.assertEqual(created.status_code, 201)
        plan_id = created.get_json()["data"]["plan"]["id"]
        with self.session_factory.begin() as session:
            project = Project(owner_user_id=self.user_id, display_name="Limited", public_project_id="limited-admin-project")
            session.add(project)
            session.flush()
            project_id = project.id
            session.add(Plan(name="Other unused plan", request_limit_per_minute=0, quota_limit_per_day=0, active_api_key_limit=0))

        assigned = self.client.patch(
            f"/api/v1/admin/projects/{project_id}", json={"plan_id": plan_id}, headers={"X-CSRF-Token": self.csrf_cookie}
        )
        self.assertEqual(assigned.status_code, 200)
        from fastapi.testclient import TestClient
        import api_gateway
        with patch("api_gateway.get_session_factory", return_value=self.session_factory), patch("api_gateway._run_analyzer", return_value={"score": 1}), patch.dict(
            os.environ, {"COGNISENSE_RATE_LIMIT_REQUESTS": "20", "COGNISENSE_DEFAULT_DAILY_QUOTA": "0"}
        ):
            principal = api_gateway.AuthenticatedProject("limited-admin-project", None)
            reservation = api_gateway._reserve_usage(principal)
            api_gateway._finish_usage(reservation, 200, __import__("time").monotonic())
            with self.assertRaises(Exception) as rejected:
                api_gateway._reserve_usage(principal)
            self.assertEqual(getattr(rejected.exception, "status_code", None), 429)

            updated = self.client.patch(
                f"/api/v1/admin/plans/{plan_id}",
                json={"request_limit_per_minute": 2},
                headers={"X-CSRF-Token": self.csrf_cookie},
            )
            self.assertEqual(updated.status_code, 200)
            reservation = api_gateway._reserve_usage(principal)
            api_gateway._finish_usage(reservation, 200, __import__("time").monotonic())
            with self.assertRaises(Exception) as rejected_again:
                api_gateway._reserve_usage(principal)
            self.assertEqual(getattr(rejected_again.exception, "status_code", None), 429)

        key_response = self.client.post(
            "/api/v1/projects/limited-admin-project/keys", json={}, headers={"X-CSRF-Token": self.csrf_cookie}
        )
        self.assertEqual(key_response.status_code, 201)
        key_limited = self.client.post(
            "/api/v1/projects/limited-admin-project/keys", json={}, headers={"X-CSRF-Token": self.csrf_cookie}
        )
        self.assertEqual(key_limited.status_code, 409)
        self.assertEqual(key_limited.get_json()["error"]["code"], "active_key_limit_reached")

        with self.session_factory() as session:
            plan = session.get(Plan, plan_id)
            self.assertEqual(plan.request_limit_per_minute, 2)
            actions = session.scalars(select(AuditLog.action)).all()
        self.assertIn("admin.plan.created", actions)
        self.assertIn("admin.project.plan_assigned", actions)

    def test_usage_grouping_and_date_bounds(self):
        self._make_admin()
        with self.session_factory.begin() as session:
            project = Project(owner_user_id=self.user_id, display_name="Usage project", public_project_id="usage-admin-project")
            session.add(project)
            session.flush()
            session.add_all([
                UsageRecord(project_id=project.id, endpoint="/api/v1/analyze", http_status_code=200, processing_duration_ms=1),
                UsageRecord(project_id=project.id, endpoint="/api/v1/analyze", http_status_code=500, processing_duration_ms=1),
                UsageRecord(project_id=project.id, endpoint="/api/v1/analyze", http_status_code=429, processing_duration_ms=0),
            ])
        response = self.client.get("/api/v1/admin/usage?group=project&per_page=100")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["groups"][0]["requests"], 3)
        self.assertEqual({row["status"]: row["requests"] for row in response.get_json()["data"]["by_status"]}, {200: 1, 429: 1, 500: 1})
        self.assertEqual(self.client.get("/api/v1/admin/usage?group=unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/admin/usage?from=2020-01-01T00:00:00Z&to=2020-07-01T00:00:00Z").status_code, 400)

    def test_admin_key_revocation_immediately_blocks_gateway_key(self):
        self._make_admin()
        project_id = self._create_gateway_project()
        created = self.client.post(
            f"/api/v1/projects/{project_id}/keys", json={}, headers={"X-CSRF-Token": self.csrf_cookie}
        )
        self.assertEqual(created.status_code, 201)
        secret = created.get_json()["data"]["secret"]
        key_id = created.get_json()["data"]["key"]["key_id"]
        with patch("api_gateway.get_session_factory", return_value=self.session_factory):
            import api_gateway
            self.assertIsNotNone(api_gateway._authenticate_database_key(secret))
            response = self.client.post(
                f"/api/v1/admin/keys/{key_id}/revoke", json={}, headers={"X-CSRF-Token": self.csrf_cookie}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(api_gateway._authenticate_database_key(secret))

    def _create_gateway_project(self):
        with self.session_factory.begin() as session:
            project = Project(owner_user_id=self.user_id, display_name="Gateway project", public_project_id="gateway-key-project")
            session.add(project)
            session.flush()
            return project.public_project_id

    def test_developer_dashboard_admin_link_is_role_gated(self):
        regular_client = self.client
        self.assertNotIn("Admin dashboard", regular_client.get("/developer/dashboard").get_data(as_text=True))
        self._make_admin()
        self.assertIn("Admin dashboard", regular_client.get("/developer/dashboard").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()