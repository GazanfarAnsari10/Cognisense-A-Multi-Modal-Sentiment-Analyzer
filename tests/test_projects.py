import hashlib
import os
import tempfile
import threading
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
from database.models import ApiKey, AuditLog, AuthSession, Base, Plan, Project, UsageRecord, User
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

    def test_default_plan_project_cap_is_enforced_and_archived_projects_do_not_count(self):
        with self.session_factory.begin() as session:
            plan = Plan(name="Single project", max_active_projects=1, is_default=True)
            session.add(plan)
        first = self._post_project("First")
        second = self._post_project("Second")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertIn("up to 1 active projects", second.get_json()["error"]["message"])
        first_id = first.get_json()["data"]["project"]["project_id"]
        archived = self.client.delete(
            f"/api/v1/projects/{first_id}", headers={"X-CSRF-Token": self.csrf_token}
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(self._post_project("After archive").status_code, 201)

    def test_plan_active_key_limit_is_authoritative_over_environment_default(self):
        with self.session_factory.begin() as session:
            session.add(Plan(name="Two keys", active_api_key_limit=2, is_default=True))
        project_id = self._post_project().get_json()["data"]["project"]["project_id"]
        with patch.dict(os.environ, {"COGNISENSE_MAX_ACTIVE_KEYS_PER_PROJECT": "1"}):
            first = self.client.post(
                f"/api/v1/projects/{project_id}/keys", json={},
                headers={"X-CSRF-Token": self.csrf_token},
            )
            second = self.client.post(
                f"/api/v1/projects/{project_id}/keys", json={},
                headers={"X-CSRF-Token": self.csrf_token},
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

    def test_concurrent_project_creation_cannot_exceed_plan_cap(self):
        temporary_database = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_database.cleanup)
        engine = create_engine(
            f"sqlite+pysqlite:///{temporary_database.name}/projects.db",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.addCleanup(engine.dispose)
        session_cookie = self.client.get_cookie("cognisense_session").value
        with factory.begin() as session:
            user = User(name="Concurrent Owner", email="concurrent-project-owner@example.com")
            session.add(user)
            session.flush()
            session.add(AuthSession(
                user_id=user.id,
                token_hash=hashlib.sha256(session_cookie.encode()).hexdigest(),
                csrf_token_hash=_csrf_hash(self.csrf_token),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            session.add(Plan(name="Concurrent cap", max_active_projects=1, is_default=True))
        self.app.config["DATABASE_SESSION_FACTORY"] = factory
        barrier = threading.Barrier(2)

        def create(name):
            client = self.app.test_client()
            client.set_cookie("cognisense_session", session_cookie)
            client.set_cookie("cognisense_csrf", self.csrf_token)
            barrier.wait(timeout=5)
            response = client.post(
                "/api/v1/projects", json={"name": name},
                headers={"X-CSRF-Token": self.csrf_token},
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(executor.map(create, ("Concurrent One", "Concurrent Two")))
        self.assertEqual(statuses, [201, 409])

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

    def test_selectable_plans_are_owner_scoped_and_assignment_requires_csrf(self):
        project_id = self._post_project().get_json()["data"]["project"]["project_id"]
        with self.session_factory.begin() as session:
            available = Plan(
                name="Customer Starter",
                description="Published plan",
                request_limit_per_minute=3,
                quota_limit_per_day=25,
                active_api_key_limit=2,
                is_selectable=True,
            )
            private = Plan(name="Internal only", request_limit_per_minute=1)
            session.add_all([available, private])
            session.flush()
            available_id, private_id = available.id, private.id

        catalog = self.client.get("/api/v1/projects/plans")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual([item["name"] for item in catalog.get_json()["data"]["plans"]], ["Customer Starter"])
        self.assertIn("max_active_projects", catalog.get_json()["data"]["plans"][0])
        self.assertIsNone(catalog.get_json()["data"]["projects"][0]["current_plan"])
        self.assertEqual(self.app.test_client().get("/api/v1/projects/plans").status_code, 401)
        self.assertEqual(
            self.client.patch(f"/api/v1/projects/{project_id}/plan", json={"plan_id": available_id}).status_code,
            403,
        )
        with_extra_field = self.client.patch(
            f"/api/v1/projects/{project_id}/plan",
            json={"plan_id": available_id, "role": "admin"},
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(with_extra_field.status_code, 400)
        hidden_plan = self.client.patch(
            f"/api/v1/projects/{project_id}/plan",
            json={"plan_id": private_id},
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(hidden_plan.status_code, 400)

        assigned = self.client.patch(
            f"/api/v1/projects/{project_id}/plan",
            json={"plan_id": available_id},
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.get_json()["data"]["plan"]["name"], "Customer Starter")
        with self.session_factory() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == project_id))
            self.assertEqual(project.plan_id, available_id)
            self.assertEqual(session.scalar(select(AuditLog.action)), "project.plan_selected")

        other_client = self.app.test_client()
        self._authenticate_client(other_client, "other-plan-owner@example.com")
        cross_owner = other_client.patch(
            f"/api/v1/projects/{project_id}/plan",
            json={"plan_id": available_id},
            headers={"X-CSRF-Token": other_client.get_cookie("cognisense_csrf").value},
        )
        self.assertEqual(cross_owner.status_code, 404)

    def test_customer_selected_plan_is_enforced_by_gateway_without_restart(self):
        project_id = self._post_project("Selected plan project").get_json()["data"]["project"]["project_id"]
        with self.session_factory.begin() as session:
            plan = Plan(name="One request plan", request_limit_per_minute=1, is_selectable=True)
            session.add(plan)
            session.flush()
            plan_id = plan.id
        assigned = self.client.patch(
            f"/api/v1/projects/{project_id}/plan",
            json={"plan_id": plan_id},
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(assigned.status_code, 200)

        import api_gateway
        with patch("api_gateway.get_session_factory", return_value=self.session_factory), patch.dict(
            os.environ, {"COGNISENSE_RATE_LIMIT_REQUESTS": "20", "COGNISENSE_DEFAULT_DAILY_QUOTA": "0"}
        ):
            principal = api_gateway.AuthenticatedProject(project_id, None)
            reservation = api_gateway._reserve_usage(principal)
            api_gateway._finish_usage(reservation, 200, __import__("time").monotonic())
            with self.assertRaises(Exception) as rejected:
                api_gateway._reserve_usage(principal)
        self.assertEqual(getattr(rejected.exception, "status_code", None), 429)

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

    def test_delete_tombstones_project_revokes_keys_and_preserves_usage(self):
        project_id = self._post_project().get_json()["data"]["project"]["project_id"]
        with self.session_factory.begin() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == project_id))
            key = ApiKey(project_id=project.id, key_prefix="delete-test-prefix", key_hash="stored-hash")
            session.add(key)
            session.flush()
            key_id = key.id
            session.add(UsageRecord(
                project_id=project.id, api_key_id=key.id, endpoint="/api/v1/analyze",
                http_status_code=200, processing_duration_ms=1,
            ))
        deleted = self.client.delete(
            f"/api/v1/projects/{project_id}/delete", headers={"X-CSRF-Token": self.csrf_token}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["data"]["project"]["status"], "deleted")
        self.assertEqual(self.client.get("/api/v1/projects").get_json()["data"]["projects"], [])
        with self.session_factory() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == project_id))
            self.assertEqual(project.status, "deleted")
            self.assertEqual(session.get(ApiKey, key_id).status, "revoked")
            self.assertIsNotNone(session.get(ApiKey, key_id).revoked_at)
            self.assertEqual(session.scalar(select(func.count(UsageRecord.id))), 1)
            self.assertEqual(session.scalar(select(AuditLog.action).where(AuditLog.action == "project.deleted")), "project.deleted")

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
