import hashlib
import os
import threading
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import _csrf_hash, _new_csrf_token
from database.models import ApiKey, AuthSession, Base, Project, UsageRecord, User
from projects import projects_blueprint


class ApiKeyApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "AUTH_SECRET_KEY": "phase-6d-test-secret-with-at-least-32-bytes",
                "AUTH_SESSION_TTL_SECONDS": "3600",
                "AUTH_COOKIE_SECURE": "false",
                "AUTH_COOKIE_SAMESITE": "Lax",
                "AUTH_LOGIN_MAX_ATTEMPTS": "5",
                "AUTH_LOGIN_WINDOW_SECONDS": "900",
                "COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT": "10",
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

        self.app = Flask("api-key-tests")
        self.app.config.update(TESTING=True, DATABASE_SESSION_FACTORY=self.session_factory)
        self.app.register_blueprint(projects_blueprint)
        self.client = self.app.test_client()
        self.user_id, session_cookie, csrf_cookie = self._create_authenticated_user("owner@example.com")
        self.client.set_cookie("cognisense_session", session_cookie)
        self.client.set_cookie("cognisense_csrf", csrf_cookie)
        self.csrf_token = csrf_cookie
        self.project_id = self._create_project()
        self.addCleanup(self.engine.dispose)

    def _create_authenticated_user(self, email):
        session_cookie = uuid.uuid4().hex + uuid.uuid4().hex
        csrf_token = _new_csrf_token(os.environ["AUTH_SECRET_KEY"])
        with self.session_factory.begin() as session:
            user = User(name="API Key Owner", email=email, status="active")
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
        return user_id, session_cookie, csrf_token

    def _new_client(self, email):
        client = self.app.test_client()
        _, session_cookie, csrf_cookie = self._create_authenticated_user(email)
        client.set_cookie("cognisense_session", session_cookie)
        client.set_cookie("cognisense_csrf", csrf_cookie)
        return client, csrf_cookie

    def _create_project(self):
        with self.session_factory.begin() as session:
            project = Project(
                owner_user_id=self.user_id,
                display_name="API key test project",
                public_project_id=str(uuid.uuid4()),
                status="active",
            )
            session.add(project)
            session.flush()
            return project.public_project_id

    def _keys_url(self, project_id=None):
        return f"/api/v1/projects/{project_id or self.project_id}/keys"

    def _create_key(self, payload=None):
        return self.client.post(
            self._keys_url(),
            json={} if payload is None else payload,
            headers={"X-CSRF-Token": self.csrf_token},
        )

    def test_key_is_random_hash_only_and_disclosed_once(self):
        first = self._create_key()
        second = self._create_key()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        first_json = first.get_json()
        second_json = second.get_json()
        first_secret = first_json["data"]["secret"]
        second_secret = second_json["data"]["secret"]
        self.assertTrue(first_secret.startswith("cgs_live_"))
        key_prefix = first_json["data"]["key"]["prefix"]
        self.assertTrue(first_secret.startswith(key_prefix + "_"))
        self.assertEqual(len(first_secret[len(key_prefix) + 1 :]), 43)
        self.assertNotEqual(first_secret, second_secret)
        self.assertEqual(first.headers["Cache-Control"], "no-store")

        with self.session_factory() as session:
            stored = session.get(ApiKey, first_json["data"]["key"]["key_id"])
            self.assertEqual(stored.key_hash, hashlib.sha256(first_secret.encode()).hexdigest())
            self.assertNotEqual(stored.key_hash, first_secret)
            self.assertNotIn(first_secret, stored.key_hash)

        for response in (
            self.client.get(self._keys_url()),
            self.client.get(
                f'{self._keys_url()}/{first_json["data"]["key"]["key_id"]}'
            ),
        ):
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(first_secret, response.get_data(as_text=True))
            self.assertNotIn("key_hash", response.get_data(as_text=True))
        self.assertNotIn("key_hash", first.get_data(as_text=True))

    def test_list_and_detail_return_only_safe_metadata(self):
        created = self._create_key({"expires_in_days": 7})
        key = created.get_json()["data"]["key"]

        listed = self.client.get(self._keys_url()).get_json()["data"]["keys"]
        retrieved = self.client.get(f'{self._keys_url()}/{key["key_id"]}').get_json()["data"]["key"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0], retrieved)
        self.assertEqual(retrieved["prefix"][:9], "cgs_live_")
        self.assertIsNotNone(retrieved["created_at"])
        self.assertIsNotNone(retrieved["expires_at"])
        self.assertIsNone(retrieved["last_used_at"])
        self.assertFalse(retrieved["is_revoked"])

    def test_revoke_is_permanent_and_repeated_revoke_is_idempotent(self):
        created = self._create_key()
        key = created.get_json()["data"]["key"]
        url = f'{self._keys_url()}/{key["key_id"]}/revoke'
        headers = {"X-CSRF-Token": self.csrf_token}

        revoked = self.client.post(url, headers=headers)
        repeated = self.client.post(url, headers=headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        revoked_key = revoked.get_json()["data"]["key"]
        self.assertEqual(revoked_key["status"], "revoked")
        self.assertTrue(revoked_key["is_revoked"])
        self.assertEqual(revoked_key["revoked_at"], repeated.get_json()["data"]["key"]["revoked_at"])
        with self.session_factory() as session:
            stored = session.get(ApiKey, key["key_id"])
            self.assertEqual(stored.status, "revoked")
            self.assertIsNotNone(stored.revoked_at)

    def test_expiration_inputs_are_validated(self):
        for invalid in (0, -1, 366, True, "7", 1.5):
            response = self._create_key({"expires_in_days": invalid})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"]["code"], "invalid_request")
        self.assertEqual(self._create_key({"unexpected": "value"}).status_code, 400)
        self.assertEqual(self._create_key({"expires_in_days": 365}).status_code, 201)
        self.assertEqual(self._create_key().status_code, 201)

    def test_active_key_limit_and_concurrent_creations(self):
        self.app.config["MAX_ACTIVE_KEYS_PER_PROJECT"] = 1
        self.assertEqual(self._create_key().status_code, 201)
        limited = self._create_key()
        self.assertEqual(limited.status_code, 409)
        self.assertEqual(limited.get_json()["error"]["code"], "active_key_limit_reached")

        with self.session_factory.begin() as session:
            project = session.scalar(
                select(Project).where(Project.public_project_id == self.project_id)
            )
            old_key = session.scalar(select(ApiKey).where(ApiKey.project_id == project.id))
            old_key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self._create_key().status_code, 201)

        with self.session_factory.begin() as session:
            project = session.scalar(
                select(Project).where(Project.public_project_id == self.project_id)
            )
            active_key = session.scalar(
                select(ApiKey).where(
                    ApiKey.project_id == project.id,
                    ApiKey.status == "active",
                )
            )
            active_key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        temporary_database = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_database.cleanup)
        concurrent_engine = create_engine(
            f"sqlite+pysqlite:///{temporary_database.name}\\concurrent.db",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(concurrent_engine.dispose)
        Base.metadata.create_all(concurrent_engine)
        self.session_factory = sessionmaker(bind=concurrent_engine, expire_on_commit=False)
        self.app.config["DATABASE_SESSION_FACTORY"] = self.session_factory
        self.user_id, session_cookie, self.csrf_token = self._create_authenticated_user(
            "concurrent-owner@example.com"
        )
        self.project_id = self._create_project()

        self.app.config["MAX_ACTIVE_KEYS_PER_PROJECT"] = 1
        barrier = threading.Barrier(2)
        clients = [self.app.test_client(), self.app.test_client()]
        for client in clients:
            client.set_cookie("cognisense_session", session_cookie)
            client.set_cookie("cognisense_csrf", self.csrf_token)

        def create_concurrently(client):
            barrier.wait(timeout=5)
            return client.post(
                self._keys_url(),
                json={},
                headers={"X-CSRF-Token": client.get_cookie("cognisense_csrf").value},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(create_concurrently, clients))
        self.assertTrue(all(response.status_code in {201, 409, 503} for response in responses))
        self.assertEqual(sum(response.status_code == 201 for response in responses), 1)
        with self.session_factory() as session:
            project = session.scalar(
                select(Project).where(Project.public_project_id == self.project_id)
            )
            active_count = session.scalar(
                select(func.count(ApiKey.id)).where(
                    ApiKey.project_id == project.id,
                    ApiKey.status == "active",
                    ApiKey.revoked_at.is_(None),
                    (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > datetime.now(timezone.utc)),
                )
            )
            self.assertLessEqual(active_count, 1)

    def test_cross_user_key_access_uses_not_found_responses(self):
        created = self._create_key()
        key_id = created.get_json()["data"]["key"]["key_id"]
        other_client, csrf_token = self._new_client("other@example.com")
        responses = [
            other_client.get(self._keys_url()),
            other_client.get(f"{self._keys_url()}/{key_id}"),
            other_client.post(
                f"{self._keys_url()}/{key_id}/revoke",
                headers={"X-CSRF-Token": csrf_token},
            ),
        ]
        self.assertEqual([response.status_code for response in responses], [404, 404, 404])
        self.assertEqual(len({response.get_data(as_text=True) for response in responses}), 1)

    def test_authentication_and_csrf_are_required(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(self._keys_url()).status_code, 401)
        self.assertEqual(anonymous.post(self._keys_url(), json={}).status_code, 401)
        missing_csrf = self.app.test_client()
        missing_csrf.set_cookie(
            "cognisense_session",
            self.client.get_cookie("cognisense_session").value,
        )
        missing_csrf.set_cookie("cognisense_csrf", self.csrf_token)
        response = missing_csrf.post(self._keys_url(), json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "csrf_failed")

    def test_archived_projects_reject_all_key_operations(self):
        with self.session_factory.begin() as session:
            project = session.scalar(
                select(Project).where(Project.public_project_id == self.project_id)
            )
            project.status = "archived"

        responses = [
            self._create_key(),
            self.client.get(self._keys_url()),
            self.client.get(f"{self._keys_url()}/1"),
            self.client.post(
                f"{self._keys_url()}/1/revoke",
                headers={"X-CSRF-Token": self.csrf_token},
            ),
        ]
        self.assertEqual([response.status_code for response in responses], [409] * 4)

    def test_database_failure_never_discloses_unpersisted_key(self):
        def fail_before_commit(session):
            if any(isinstance(item, ApiKey) for item in session.identity_map.values()):
                raise SQLAlchemyError("sensitive database failure details")

        event.listen(self.session_factory, "before_commit", fail_before_commit)
        try:
            response = self._create_key()
        finally:
            event.remove(self.session_factory, "before_commit", fail_before_commit)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive database failure details", response.get_data(as_text=True))
        self.assertNotIn("cgs_live_", response.get_data(as_text=True))
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count(ApiKey.id))), 0)
            self.assertIsNotNone(
                session.scalar(select(Project.id).where(Project.public_project_id == self.project_id))
            )

    def test_existing_usage_records_are_preserved(self):
        created = self._create_key()
        api_key_id = created.get_json()["data"]["key"]["key_id"]
        with self.session_factory.begin() as session:
            api_key = session.get(ApiKey, api_key_id)
            session.add(
                UsageRecord(
                    project_id=api_key.project_id,
                    api_key_id=api_key.id,
                    endpoint="/api/v1/analyze",
                    http_status_code=200,
                    processing_duration_ms=25,
                )
            )

        self.client.post(
            f"{self._keys_url()}/{api_key_id}/revoke",
            headers={"X-CSRF-Token": self.csrf_token},
        )
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count(Project.id))), 1)
            self.assertEqual(session.scalar(select(func.count(UsageRecord.id))), 1)
            record = session.scalar(select(UsageRecord))
            self.assertEqual(record.api_key_id, api_key_id)


if __name__ == "__main__":
    unittest.main()
