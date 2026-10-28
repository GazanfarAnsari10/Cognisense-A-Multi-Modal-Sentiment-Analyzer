import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import AuthSession, Base, User


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask

        from auth import auth_blueprint

        self.environment = patch.dict(
            os.environ,
            {
                "AUTH_SECRET_KEY": "test-auth-secret-with-at-least-32-bytes",
                "AUTH_SESSION_TTL_SECONDS": "3600",
                "AUTH_COOKIE_SECURE": "false",
                "AUTH_COOKIE_SAMESITE": "Lax",
                "AUTH_LOGIN_MAX_ATTEMPTS": "3",
                "AUTH_LOGIN_WINDOW_SECONDS": "900",
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
        self.app = Flask("auth-tests")
        self.app.config.update(TESTING=True, DATABASE_SESSION_FACTORY=self.session_factory)
        self.app.register_blueprint(auth_blueprint)
        self.client = self.app.test_client()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.engine.dispose()

    def _csrf_token(self):
        response = self.client.get("/api/v1/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]["csrf_token"]

    def _register(self, email="Person@example.com", password="correct-horse-battery"):
        token = self._csrf_token()
        return self.client.post(
            "/api/v1/auth/register",
            json={"name": "Test User", "email": email, "password": password},
            headers={"X-CSRF-Token": token},
        )

    def _login(self, email="person@example.com", password="correct-horse-battery"):
        token = self._csrf_token()
        return self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": token},
        )

    def test_register_normalizes_email_and_stores_argon2id_hash(self):
        response = self._register()

        self.assertEqual(response.status_code, 202)
        with self.session_factory() as session:
            user = session.scalar(select(User).where(User.email == "person@example.com"))
            self.assertIsNotNone(user)
            self.assertNotEqual(user.password_hash, "correct-horse-battery")
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
        self.assertNotIn("password_hash", response.get_data(as_text=True))
        self.assertNotIn("correct-horse-battery", response.get_data(as_text=True))

    def test_duplicate_email_is_case_insensitive_and_response_is_generic(self):
        first = self._register()
        duplicate = self._register(email=" PERSON@EXAMPLE.COM ")

        self.assertEqual(first.status_code, duplicate.status_code)
        self.assertEqual(first.get_json(), duplicate.get_json())
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(User.id).where(User.email == "person@example.com")), 1)

    def test_invalid_registration_inputs_are_rejected(self):
        token = self._csrf_token()
        for payload in (
            {"name": "", "email": "person@example.com", "password": "correct-horse-battery"},
            {"name": "Test", "email": "invalid", "password": "correct-horse-battery"},
            {"name": "Test", "email": "person@example.com", "password": "short"},
        ):
            response = self.client.post(
                "/api/v1/auth/register",
                json=payload,
                headers={"X-CSRF-Token": token},
            )
            self.assertEqual(response.status_code, 400)

    def test_login_creates_persistent_http_only_session_and_me_hides_internal_data(self):
        self._register()
        response = self._login()

        self.assertEqual(response.status_code, 200)
        session_cookie = response.headers.getlist("Set-Cookie")
        self.assertTrue(any("cognisense_session=" in cookie and "HttpOnly" in cookie for cookie in session_cookie))
        self.assertTrue(any("SameSite=Lax" in cookie for cookie in session_cookie))
        self.assertTrue(any("Secure" not in cookie for cookie in session_cookie))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        current_user = self.client.get("/api/v1/auth/me")
        self.assertEqual(current_user.status_code, 200)
        self.assertEqual(current_user.get_json()["data"]["user"], {
            "name": "Test User",
            "email": "person@example.com",
            "status": "active",
        })
        self.assertNotIn("password_hash", current_user.get_data(as_text=True))

    def test_login_rotates_a_preexisting_session_cookie(self):
        self._register()
        self.client.set_cookie("cognisense_session", "attacker-controlled-value")
        response = self._login()

        self.assertEqual(response.status_code, 200)
        issued_cookie = next(
            cookie.split(";", 1)[0].split("=", 1)[1]
            for cookie in response.headers.getlist("Set-Cookie")
            if cookie.startswith("cognisense_session=")
        )
        self.assertNotEqual(issued_cookie, "attacker-controlled-value")
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_csrf_cookie_is_secure_by_default(self):
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "true"}):
            response = self.client.get("/api/v1/auth/csrf", base_url="https://localhost")

        self.assertEqual(response.status_code, 200)
        csrf_cookie = next(
            cookie for cookie in response.headers.getlist("Set-Cookie") if cookie.startswith("cognisense_csrf=")
        )
        self.assertIn("Secure", csrf_cookie)

    def test_invalid_credentials_are_generic_and_rate_limited(self):
        self._register()
        wrong_password = self._login(password="incorrect-password")
        nonexistent_user = self._login(email="missing@example.com", password="incorrect-password")
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_password.get_json(), nonexistent_user.get_json())

        for _ in range(2):
            self._login(password="incorrect-password")
        limited = self._login(password="incorrect-password")
        self.assertEqual(limited.status_code, 429)
        self.assertNotIn("person@example.com", limited.get_data(as_text=True))

    def test_unauthorized_and_csrf_requests_are_rejected(self):
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        missing_csrf = self.client.post(
            "/api/v1/auth/register",
            json={"name": "Test", "email": "person@example.com", "password": "correct-horse-battery"},
        )
        self.assertEqual(missing_csrf.status_code, 403)

    def test_login_session_expires_and_logout_revokes_session(self):
        self._register()
        login_response = self._login()
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

        with self.session_factory.begin() as session:
            auth_session = session.scalar(select(AuthSession))
            auth_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

        self.client.delete_cookie("cognisense_session")
        login_response = self._login()
        self.assertEqual(login_response.status_code, 200)
        csrf_value = next(
            cookie.split(";", 1)[0].split("=", 1)[1]
            for cookie in login_response.headers.getlist("Set-Cookie")
            if cookie.startswith("cognisense_csrf=")
        )
        logout_response = self.client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_value},
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_logout_rejects_csrf_token_not_bound_to_session(self):
        self._register()
        self._login()
        self.client.set_cookie("cognisense_csrf", "invalid")
        response = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "invalid"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_refreshing_csrf_token_keeps_current_session_usable(self):
        self._register()
        self._login()
        token = self._csrf_token()
        response = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)

    def test_database_failure_returns_safe_error_and_rolls_back_registration(self):
        from sqlalchemy import event

        def fail_commit(session):
            raise SQLAlchemyError("database error containing secret details")

        event.listen(self.session_factory, "before_commit", fail_commit)
        try:
            response = self._register()
        finally:
            event.remove(self.session_factory, "before_commit", fail_commit)

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret details", response.get_data(as_text=True))
        with self.session_factory() as session:
            self.assertIsNone(session.scalar(select(User).where(User.email == "person@example.com")))


if __name__ == "__main__":
    unittest.main()
