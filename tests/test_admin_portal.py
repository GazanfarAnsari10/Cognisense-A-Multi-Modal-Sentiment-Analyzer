import hashlib
import os
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from admin_portal import admin_blueprint
from admin_provision import ProvisioningError, main as provision_main, promote_initial_admin
from auth import _csrf_hash, _new_csrf_token, auth_blueprint
from database.models import AuditLog, AuthSession, Base, User
from developer_portal import developer_blueprint


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
        self.assertIn("Admin access", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/v1/admin").status_code, 200)
        audit = self.client.get("/api/v1/admin/audit")
        self.assertEqual(audit.status_code, 200)
        records = audit.get_json()["data"]["records"]
        self.assertTrue(any(record["action"] == "admin.provision" for record in records))
        self.assertTrue(any(record["action"] == "admin.access" and record["outcome"] == "allowed" for record in records))
        self.assertNotIn("test-hash", audit.get_data(as_text=True))

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


if __name__ == "__main__":
    unittest.main()