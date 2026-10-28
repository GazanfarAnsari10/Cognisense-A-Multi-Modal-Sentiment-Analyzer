import hashlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import _csrf_hash, _new_csrf_token
from database.models import ApiKey, AuthSession, Base, Project, UsageRecord, User
from projects import projects_blueprint


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "AUTH_SECRET_KEY": "phase-6c-test-secret-with-at-least-32-bytes",
                "AUTH_SESSION_TTL_SECONDS": "3600",
                "AUTH_COOKIE_SECURE": "false",
                "AUTH_COOKIE_SAMESITE": "Lax",
                "AUTH_LOGIN_MAX_ATTEMPTS": "5",
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

        self.app = Flask("project-tests")
        self.app.config.update(
            TESTING=True,
            DATABASE_SESSION_FACTORY=self.session_factory,
            PROJECT_MAX_PER_USER=2,
        )
        self.app.register_blueprint(projects_blueprint)
        self.client = self.app.test_client()
        self.user_id, session_cookie, csrf_cookie = self._create_authenticated_user("owner@example.com")
        self.client.set_cookie("cognisense_session", session_cookie)
        self.client.set_cookie("cognisense_csrf", csrf_cookie)
        self.csrf_token = csrf_cookie
        self.addCleanup(self.engine.dispose)

    def _create_authenticated_user(self, email):
        session_cookie = uuid.uuid4().hex + uuid.uuid4().hex
        csrf_token = _new_csrf_token(os.environ["AUTH_SECRET_KEY"])
        with self.session_factory.begin() as session:
            user = User(name="Project Owner", email=email, status="active")
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

    def _authenticate_client(self, client, email):
        _, session_cookie, csrf_token = self._create_authenticated_user(email)
        client.set_cookie("cognisense_session", session_cookie)
        client.set_cookie("cognisense_csrf", csrf_token)
        return csrf_token

    def _post_project(self, name="Production API", **extra):
        payload = {"name": name, **extra}
        return self.client.post(
            "/api/v1/projects",
            json=payload,
            headers={"X-CSRF-Token": self.csrf_token},
        )

    def test_create_list_retrieve_update_and_archive(self):
        created = self._post_project("  Production API  ")
        self.assertEqual(created.status_code, 201)
        project = created.get_json()["data"]["project"]
        project_id = project["project_id"]
        self.assertEqual(project["name"], "Production API")
        self.assertEqual(uuid.UUID(project_id).version, 4)
        self.assertEqual(project["status"], "active")

        listed = self.client.get("/api/v1/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["data"]["projects"]), 1)

        retrieved = self.client.get(f"/api/v1/projects/{project_id}")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.get_json()["data"]["project"]["name"], "Production API")

        updated = self.client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Renamed API"},
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["project"]["name"], "Renamed API")
        self.assertEqual(updated.get_json()["data"]["project"]["project_id"], project_id)

        archived = self.client.delete(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.get_json()["data"]["project"]["status"], "archived")
        self.assertEqual(self.client.get("/api/v1/projects").get_json()["data"]["projects"][0]["status"], "archived")

    def test_project_creation_limit_counts_active_projects(self):
        first = self._post_project("Project One")
        second = self._post_project("Project Two")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.get_json()["data"]["project"]["project_id"],
            second.get_json()["data"]["project"]["project_id"],
        )
        limited = self._post_project("Project Three")
        self.assertEqual(limited.status_code, 409)
        self.assertEqual(limited.get_json()["error"]["code"], "project_limit_reached")

    def test_unauthenticated_requests_are_rejected(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/v1/projects").status_code, 401)
        self.assertEqual(anonymous.post("/api/v1/projects", json={"name": "Project"}).status_code, 401)

    def test_cross_user_project_access_is_indistinguishable_from_missing(self):
        created = self._post_project()
        project_id = created.get_json()["data"]["project"]["project_id"]
        other_client = self.app.test_client()
        self._authenticate_client(other_client, "other@example.com")

        responses = [
            other_client.get(f"/api/v1/projects/{project_id}"),
            other_client.patch(
                f"/api/v1/projects/{project_id}",
                json={"name": "Stolen"},
                headers={"X-CSRF-Token": other_client.get_cookie("cognisense_csrf").value},
            ),
            other_client.delete(
                f"/api/v1/projects/{project_id}",
                headers={"X-CSRF-Token": other_client.get_cookie("cognisense_csrf").value},
            ),
        ]
        self.assertEqual([response.status_code for response in responses], [404, 404, 404])
        self.assertEqual(len({response.get_data(as_text=True) for response in responses}), 1)
        self.assertEqual(other_client.get("/api/v1/projects").get_json()["data"]["projects"], [])
        self.assertEqual(self.client.get("/api/v1/projects").get_json()["data"]["projects"][0]["name"], "Production API")

    def test_invalid_names_malformed_payloads_and_owner_override_are_rejected(self):
        for invalid_name in ("", "  ", "x" * 101, "bad\nname", "bad\x85name", 42, None):
            self.assertEqual(self._post_project(invalid_name).status_code, 400)
        self.assertEqual(self._post_project("Project", owner_user_id=999).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/v1/projects",
                data="not json",
                content_type="application/json",
                headers={"X-CSRF-Token": self.csrf_token},
            ).status_code,
            400,
        )
        created = self._post_project()
        project_id = created.get_json()["data"]["project"]["project_id"]
        self.assertEqual(
            self.client.patch(
                f"/api/v1/projects/{project_id}",
                json={"name": "Valid", "project_id": "forged"},
                headers={"X-CSRF-Token": self.csrf_token},
            ).status_code,
            400,
        )

    def test_state_changes_require_csrf(self):
        self.assertEqual(self.client.post("/api/v1/projects", json={"name": "No token"}).status_code, 403)
        created = self._post_project()
        project_id = created.get_json()["data"]["project"]["project_id"]
        self.assertEqual(
            self.client.patch(f"/api/v1/projects/{project_id}", json={"name": "No token"}).status_code,
            403,
        )
        self.assertEqual(self.client.delete(f"/api/v1/projects/{project_id}").status_code, 403)

    def test_project_with_api_key_and_usage_is_archived_not_deleted(self):
        created = self._post_project()
        project_id = created.get_json()["data"]["project"]["project_id"]
        with self.session_factory.begin() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == project_id))
            api_key = ApiKey(project_id=project.id, key_prefix="test-prefix", key_hash="hashed-secret")
            session.add(api_key)
            session.flush()
            session.add(
                UsageRecord(
                    project_id=project.id,
                    api_key_id=api_key.id,
                    endpoint="/api/v1/analyze",
                    http_status_code=200,
                    processing_duration_ms=100,
                )
            )

        deleted = self.client.delete(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(deleted.status_code, 200)
        with self.session_factory() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == project_id))
            self.assertEqual(project.status, "archived")
            self.assertEqual(len(project.api_keys), 1)
            self.assertEqual(len(project.usage_records), 1)

    def test_public_id_collision_is_retried_and_never_reassigned(self):
        existing = self._post_project("Existing")
        existing_id = existing.get_json()["data"]["project"]["project_id"]
        with patch("projects.uuid.uuid4", return_value=uuid.UUID(existing_id)):
            collision = self._post_project("Collision")
        self.assertEqual(collision.status_code, 503)
        self.assertEqual(
            self.client.get(f"/api/v1/projects/{existing_id}").get_json()["data"]["project"]["name"],
            "Existing",
        )
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(Project.public_project_id).where(Project.display_name == "Existing")), existing_id)
            self.assertIsNone(session.scalar(select(Project.id).where(Project.display_name == "Collision")))

    def test_database_errors_return_generic_service_error(self):
        def fail_before_flush(session, flush_context, instances):
            if any(isinstance(item, Project) for item in session.new):
                raise SQLAlchemyError("sensitive database failure details")

        event.listen(self.session_factory, "before_flush", fail_before_flush)
        try:
            response = self._post_project("Database failure")
        finally:
            event.remove(self.session_factory, "before_flush", fail_before_flush)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive database failure details", response.get_data(as_text=True))
        with self.session_factory() as session:
            self.assertIsNone(session.scalar(select(Project.id).where(Project.display_name == "Database failure")))


if __name__ == "__main__":
    unittest.main()
