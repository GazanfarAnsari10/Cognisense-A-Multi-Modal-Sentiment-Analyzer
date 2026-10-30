import hashlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import _csrf_hash, _new_csrf_token, auth_blueprint
from database.models import ApiKey, AuthSession, Base, Plan, Project, UsageRecord, User
from developer_portal import developer_blueprint
from projects import projects_blueprint


class DeveloperPortalTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "AUTH_SECRET_KEY": "developer-portal-test-secret-with-at-least-32-bytes",
                "AUTH_SESSION_TTL_SECONDS": "3600",
                "AUTH_COOKIE_SECURE": "false",
                "AUTH_COOKIE_SAMESITE": "Lax",
                "AUTH_LOGIN_MAX_ATTEMPTS": "5",
                "AUTH_LOGIN_WINDOW_SECONDS": "900",
                "COGNISENSE_RATE_LIMIT_REQUESTS": "60",
                "COGNISENSE_RATE_LIMIT_WINDOW_SECONDS": "60",
                "COGNISENSE_DEFAULT_DAILY_QUOTA": "0",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.app = Flask("developer-portal-tests")
        self.app.config.update(TESTING=True, DATABASE_SESSION_FACTORY=self.session_factory)
        self.app.register_blueprint(auth_blueprint)
        self.app.register_blueprint(projects_blueprint)
        self.app.register_blueprint(developer_blueprint)
        self.app.add_url_rule("/text", "public_text", lambda: "public analyzer")
        self.client = self.app.test_client()
        self.owner_id, self.session_cookie, self.csrf_cookie = self._create_user("owner@example.com")
        self.client.set_cookie("cognisense_session", self.session_cookie)
        self.client.set_cookie("cognisense_csrf", self.csrf_cookie)
        self.addCleanup(self.engine.dispose)

    def _create_user(self, email):
        session_cookie = secrets_token = uuid.uuid4().hex + uuid.uuid4().hex
        csrf_token = _new_csrf_token(os.environ["AUTH_SECRET_KEY"])
        with self.session_factory.begin() as session:
            user = User(name="Developer Owner", email=email, status="active")
            session.add(user)
            session.flush()
            user_id = user.id
            session.add(
                AuthSession(
                    user_id=user_id,
                    token_hash=hashlib.sha256(session_cookie.encode()).hexdigest(),
                    csrf_token_hash=_csrf_hash(csrf_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            )
        return user_id, secrets_token, csrf_token

    def test_public_analyzer_remains_public_and_dashboard_requires_session(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/developer/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/developer/login", response.headers["Location"])
        self.assertIn("next=/developer/dashboard", response.headers["Location"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(anonymous.get("/api/v1/developer/dashboard").status_code, 401)
        self.assertEqual(anonymous.get("/api/v1/projects").status_code, 401)
        self.assertEqual(anonymous.get("/text").status_code, 200)
        self.assertIn("Developer workspace", anonymous.get("/developer").get_data(as_text=True))
        dashboard = self.client.get("/developer/dashboard")
        dashboard_html = dashboard.get_data(as_text=True)
        self.assertIn('href="/developer/dashboard" aria-label="Cognisense developer dashboard overview"', dashboard_html)
        self.assertIn('id="sidebar-collapse"', dashboard_html)
        self.assertIn('aria-controls="dashboard-navigation"', dashboard_html)

    def test_dashboard_reads_only_owner_usage_and_plan_limits(self):
        with self.session_factory.begin() as session:
            plan = Plan(
                name="Developer test plan",
                request_limit_per_minute=4,
                quota_limit_per_day=10,
                active_api_key_limit=3,
                is_default=False,
            )
            session.add(plan)
            session.flush()
            project = Project(
                owner_user_id=self.owner_id,
                display_name="Owned Project",
                public_project_id=str(uuid.uuid4()),
                status="active",
                plan_id=plan.id,
            )
            stranger = User(name="Other", email="other@example.com", status="active")
            session.add_all([project, stranger])
            session.flush()
            other_project = Project(
                owner_user_id=stranger.id,
                display_name="Private Project",
                public_project_id=str(uuid.uuid4()),
                status="active",
            )
            session.add(other_project)
            session.flush()
            key = ApiKey(
                project_id=project.id,
                key_prefix="cgs_live_testprefix123",
                key_hash="stored-hash-never-returned",
                status="active",
            )
            session.add(key)
            session.flush()
            for status in (200, 500, 429):
                session.add(
                    UsageRecord(
                        project_id=project.id,
                        api_key_id=key.id,
                        endpoint="/api/v1/analyze",
                        http_status_code=status,
                        processing_duration_ms=25,
                    )
                )
            session.add(
                UsageRecord(
                    project_id=other_project.id,
                    endpoint="/private",
                    http_status_code=200,
                    processing_duration_ms=10,
                )
            )

        response = self.client.get("/api/v1/developer/dashboard")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["summary"], {
            "total_projects": 1,
            "active_keys": 1,
            "requests_24h": 3,
            "successful_24h": 1,
            "failed_24h": 1,
            "limit_responses_24h": 1,
        })
        project_data = data["projects"][0]
        self.assertEqual(project_data["quota_used"], 2)
        self.assertEqual(project_data["quota_limit"], 10)
        self.assertEqual(project_data["limit_responses_24h"], 1)
        self.assertEqual(project_data["rate_limit"], 60)
        self.assertEqual(project_data["rate_count"], 2)
        self.assertEqual(project_data["plan_rate_limit"], 4)
        self.assertEqual(project_data["plan_rate_count"], 2)
        self.assertNotIn("Private Project", response.get_data(as_text=True))
        self.assertNotIn("stored-hash-never-returned", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store, no-cache, must-revalidate, private")

    def test_expired_session_is_rejected_by_page_and_json_endpoint(self):
        with self.session_factory.begin() as session:
            auth_session = session.scalar(select(AuthSession))
            auth_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.client.get("/developer/dashboard").status_code, 302)
        response = self.client.get("/api/v1/developer/dashboard")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "unauthorized")

    def test_register_login_logout_and_safe_next_destination(self):
        anonymous = self.app.test_client()
        token = anonymous.get("/api/v1/auth/csrf").get_json()["data"]["csrf_token"]
        registration = anonymous.post(
            "/api/v1/auth/register",
            json={"name": "New Developer", "email": "new@example.com", "password": "correct-horse-battery"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(registration.status_code, 202)
        login_token = anonymous.get("/api/v1/auth/csrf").get_json()["data"]["csrf_token"]
        login = anonymous.post(
            "/api/v1/auth/login",
            json={"email": "new@example.com", "password": "correct-horse-battery"},
            headers={"X-CSRF-Token": login_token},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(anonymous.get("/developer/login?next=https://evil.example").status_code, 302)
        self.assertEqual(anonymous.get("/developer/dashboard").status_code, 200)
        self.assertIn("no-store", anonymous.get("/developer/dashboard").headers["Cache-Control"])
        logout_token = anonymous.get("/api/v1/auth/csrf").get_json()["data"]["csrf_token"]
        logout = anonymous.post("/api/v1/auth/logout", headers={"X-CSRF-Token": logout_token})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(anonymous.get("/api/v1/developer/dashboard").status_code, 401)

    def test_mutation_requires_csrf_even_when_authenticated(self):
        response = self.client.post("/api/v1/projects", json={"name": "Missing token"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
