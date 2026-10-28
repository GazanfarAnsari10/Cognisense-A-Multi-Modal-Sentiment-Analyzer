import hashlib
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api_gateway
from database.models import ApiKey, Base, Plan, Project, UsageRecord, User


class FeedbackGatewayTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "COGNISENSE_API_KEYS": (
                    '{"example-project":"replace-with-a-long-random-secret"}'
                ),
                "COGNISENSE_LEGACY_API_KEYS_ENABLED": "true",
                "COGNISENSE_RATE_LIMIT_REQUESTS": "60",
                "COGNISENSE_RATE_LIMIT_WINDOW_SECONDS": "60",
                "COGNISENSE_DEFAULT_DAILY_QUOTA": "0",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.analyzer = patch(
            "api_gateway._run_analyzer",
            side_effect=lambda modality, content, filename: {
                "analyzed": modality,
                "filename": filename,
            },
        )
        self.mock_analyzer = self.analyzer.start()
        self.addCleanup(self.analyzer.stop)
        self.client = TestClient(api_gateway.app)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session_factory_patch = patch(
            "api_gateway.get_session_factory",
            return_value=self.session_factory,
        )
        self.session_factory_patch.start()
        self.addCleanup(self.session_factory_patch.stop)
        self.addCleanup(self.engine.dispose)
        self.project_id = self._create_database_project("example-project")

    def _create_database_project(self, public_project_id=None, status="active"):
        with self.session_factory.begin() as session:
            owner = User(name="Gateway Test Owner", email=f"{uuid.uuid4()}@example.com")
            session.add(owner)
            session.flush()
            project = Project(
                owner_user_id=owner.id,
                display_name="Gateway Test Project",
                public_project_id=public_project_id or str(uuid.uuid4()),
                status=status,
            )
            session.add(project)
            session.flush()
            return project.public_project_id

    def _create_database_key(
        self,
        *,
        project_id=None,
        status="active",
        revoked_at=None,
        expires_at=None,
    ):
        prefix = f"cgs_live_{uuid.uuid4().hex[:16]}"
        secret = f"{prefix}_{uuid.uuid4().hex}{uuid.uuid4().hex[:11]}"
        with self.session_factory.begin() as session:
            project = session.scalar(
                select(Project).where(
                    Project.public_project_id == (project_id or self.project_id)
                )
            )
            key = ApiKey(
                project_id=project.id,
                key_prefix=prefix,
                key_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                status=status,
                revoked_at=revoked_at,
                expires_at=expires_at,
            )
            session.add(key)
        return secret

    def _analyze_with_key(self, key, project_id=None):
        return self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": key},
            json={
                "project_id": project_id or self.project_id,
                "text": "Database key authentication",
            },
        )

    def test_health_returns_request_id(self):
        response = self.client.get(
            "/api/v1/health",
            headers={"X-Request-ID": "external-request-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "external-request-123")
        self.assertEqual(response.json()["request_id"], "external-request-123")
        self.assertEqual(response.json()["data"]["status"], "ok")

    def test_openapi_documents_both_request_formats(self):
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/analyze"]["post"]
        self.assertIn("application/json", operation["requestBody"]["content"])
        self.assertIn("multipart/form-data", operation["requestBody"]["content"])
        self.assertEqual(operation["security"], [{"APIKeyHeader": []}])
        parameters = operation["parameters"]
        self.assertTrue(
            any(
                parameter["name"] == "export_format"
                and parameter["schema"]["enum"] == ["json", "csv", "pdf"]
                for parameter in parameters
            )
        )

    def test_json_text_is_authenticated_and_standardized(self):
        response = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["project_id"], "example-project")
        self.assertEqual(payload["data"]["status"], "complete")
        self.assertEqual(payload["data"]["results"][0]["modality"], "text")
        self.assertEqual(self.mock_analyzer.call_args.args[0], "text")

    def test_json_export_returns_download_only_for_authenticated_analysis(self):
        response = self.client.post(
            "/api/v1/analyze?export_format=json",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.headers["content-disposition"].endswith('.json"'))
        payload = response.json()
        self.assertEqual(payload["project_id"], "example-project")
        self.assertEqual(payload["data"]["results"][0]["result"]["analyzed"], "text")

    def test_pdf_export_returns_downloadable_pdf(self):
        response = self.client.post(
            "/api/v1/analyze?export_format=pdf",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_csv_export_returns_downloadable_chart_data(self):
        response = self.client.post(
            "/api/v1/analyze?export_format=csv",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn(b"chart_type", response.content)

    def test_unsupported_export_format_is_rejected(self):
        response = self.client.post(
            "/api/v1/analyze?export_format=xml",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.mock_analyzer.assert_not_called()

    def test_export_requires_valid_project_authentication(self):
        response = self.client.post(
            "/api/v1/analyze?export_format=json",
            json={"project_id": "example-project", "text": "Great service"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.mock_analyzer.assert_not_called()

    def test_multipart_routes_text_and_supported_file(self):
        response = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            data={"project_id": "example-project", "text": "Text feedback"},
            files=[("files", ("note.txt", b"Document feedback", "text/plain"))],
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["data"]["results"]
        self.assertEqual([item["modality"] for item in results], ["text", "text"])
        self.assertEqual([call.args[0] for call in self.mock_analyzer.call_args_list], ["text", "document"])

    def test_combined_multimodal_request_routes_each_file(self):
        response = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            data={"project_id": "example-project", "text": "Text feedback"},
            files=[
                ("files", ("feedback.wav", b"audio bytes", "audio/wav")),
                ("files", ("meeting.mp4", b"video bytes", "video/mp4")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["data"]["results"]
        self.assertEqual([item["modality"] for item in results], ["text", "audio", "video"])
        self.assertEqual(
            [call.args[0] for call in self.mock_analyzer.call_args_list],
            ["text", "audio", "video"],
        )

    def test_unsupported_file_type_has_standard_error(self):
        response = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            data={"project_id": "example-project"},
            files=[("files", ("payload.exe", b"not supported", "application/octet-stream"))],
        )

        self.assertEqual(response.status_code, 415)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error"]["code"], "unsupported_media_type")
        self.assertIn("request_id", response.json())
        self.mock_analyzer.assert_not_called()

    def test_api_key_cannot_be_used_for_another_project(self):
        response = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "another-project", "text": "Feedback"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_missing_api_key_is_rejected(self):
        response = self.client.post(
            "/api/v1/analyze",
            json={"project_id": "example-project", "text": "Feedback"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")

    def test_database_key_authenticates_and_uses_database_project_identity(self):
        secret = self._create_database_key()
        response = self._analyze_with_key(secret)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], self.project_id)
        self.mock_analyzer.assert_called_once()

    def test_invalid_database_keys_never_fall_back_to_legacy_auth(self):
        with patch.dict(
            os.environ,
            {
                "COGNISENSE_API_KEYS": (
                    '{"example-project":"cgs_live_short"}'
                ),
            },
        ):
            for key in ("cgs_live_short", "cgs_live_" + "a" * 59):
                response = self._analyze_with_key(key, project_id="example-project")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.mock_analyzer.assert_not_called()

    def test_unknown_database_key_is_rejected(self):
        prefix = f"cgs_live_{uuid.uuid4().hex[:16]}"
        unknown = f"{prefix}_{uuid.uuid4().hex}{uuid.uuid4().hex[:11]}"

        response = self._analyze_with_key(unknown)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["message"], "A valid X-API-Key header is required.")
        self.mock_analyzer.assert_not_called()

    def test_database_key_hash_must_match_exact_presented_key(self):
        secret = self._create_database_key()
        with self.session_factory.begin() as session:
            key = session.scalar(select(ApiKey))
            key.key_hash = hashlib.sha256(b"a different key").hexdigest()

        response = self._analyze_with_key(secret)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.mock_analyzer.assert_not_called()

    def test_revoked_expired_and_inactive_project_keys_are_rejected(self):
        rejected = [
            self._create_database_key(status="revoked"),
            self._create_database_key(revoked_at=datetime.now(timezone.utc)),
            self._create_database_key(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        ]
        archived_project = self._create_database_project(status="archived")
        rejected.append(self._create_database_key(project_id=archived_project))

        for secret in rejected:
            with self.subTest(secret_prefix=secret[:20]):
                response = self._analyze_with_key(secret)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.mock_analyzer.assert_not_called()
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(func.count(UsageRecord.id))), 0)

    def test_database_key_cannot_claim_another_project(self):
        secret = self._create_database_key()
        other_project = self._create_database_project()

        response = self._analyze_with_key(secret, project_id=other_project)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")
        self.mock_analyzer.assert_not_called()

    def test_database_errors_fail_closed_without_exposing_details_or_key(self):
        secret = self._create_database_key()
        self.session_factory_patch.stop()
        failed_factory = patch(
            "api_gateway.get_session_factory",
            side_effect=SQLAlchemyError(f"sensitive database error for {secret}"),
        )
        failed_factory.start()
        self.addCleanup(failed_factory.stop)

        response = self._analyze_with_key(secret)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")
        self.assertNotIn(secret, response.text)
        self.assertNotIn("sensitive database error", response.text)
        self.mock_analyzer.assert_not_called()

    def test_legacy_auth_can_be_disabled_during_migration(self):
        with patch.dict(os.environ, {"COGNISENSE_LEGACY_API_KEYS_ENABLED": "false"}):
            legacy_response = self._analyze_with_key("replace-with-a-long-random-secret", "example-project")
            database_secret = self._create_database_key()
            database_response = self._analyze_with_key(database_secret)

        self.assertEqual(legacy_response.status_code, 401)
        self.assertEqual(database_response.status_code, 200)

    def test_usage_records_success_and_invalid_request_without_payload_data(self):
        success = self._analyze_with_key("replace-with-a-long-random-secret")
        invalid = self.client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "replace-with-a-long-random-secret"},
            json={"project_id": "example-project", "text": 42},
        )

        self.assertEqual(success.status_code, 200)
        self.assertEqual(invalid.status_code, 422)
        with self.session_factory() as session:
            records = session.scalars(
                select(UsageRecord).order_by(UsageRecord.id)
            ).all()
            self.assertEqual([record.http_status_code for record in records], [200, 422])
            self.assertTrue(all(record.project_id for record in records))
            self.assertTrue(all(record.api_key_id is None for record in records))
            self.assertTrue(all(record.processing_duration_ms >= 0 for record in records))
            self.assertTrue(all(record.endpoint == "/api/v1/analyze" for record in records))
            self.assertNotIn("Database key authentication", repr(records))

    def test_database_key_usage_is_linked_to_key(self):
        secret = self._create_database_key()
        response = self._analyze_with_key(secret)

        self.assertEqual(response.status_code, 200)
        with self.session_factory() as session:
            record = session.scalar(select(UsageRecord))
            key = session.scalar(select(ApiKey))
            self.assertEqual(record.api_key_id, key.id)
            self.assertEqual(record.project_id, key.project_id)
            self.assertEqual(record.http_status_code, 200)

    def test_analyzer_failure_is_recorded_as_failed(self):
        self.mock_analyzer.side_effect = RuntimeError("private analyzer error")

        response = self._analyze_with_key("replace-with-a-long-random-secret")

        self.assertEqual(response.status_code, 500)
        with self.session_factory() as session:
            record = session.scalar(select(UsageRecord))
            self.assertEqual(record.http_status_code, 500)
            self.assertNotIn("private analyzer error", response.text)

    def test_rate_limit_returns_retry_after_and_tracks_limited_request(self):
        with patch.dict(
            os.environ,
            {
                "COGNISENSE_RATE_LIMIT_REQUESTS": "1",
                "COGNISENSE_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
        ):
            first = self._analyze_with_key("replace-with-a-long-random-secret")
            limited = self.client.post(
                "/api/v1/analyze",
                headers={
                    "X-API-Key": "replace-with-a-long-random-secret",
                    "Content-Type": "text/plain",
                },
                content=b"not a valid analysis request",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertGreaterEqual(int(limited.headers["Retry-After"]), 1)
        self.assertEqual(limited.json()["error"]["code"], "rate_limited")
        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(UsageRecord.id)).where(UsageRecord.http_status_code == 429)
                ),
                1,
            )
        self.mock_analyzer.assert_called_once()

    def test_plan_quota_enforces_daily_limit_and_resets_after_rolling_day(self):
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            plan = Plan(
                name="one-request-test",
                request_limit_per_minute=0,
                quota_limit_per_day=1,
                active_api_key_limit=1,
                is_default=False,
            )
            session.add(plan)
            session.flush()
            project = session.scalar(
                select(Project).where(Project.public_project_id == "example-project")
            )
            project.plan_id = plan.id
            session.add(
                UsageRecord(
                    project_id=project.id,
                    endpoint="/api/v1/analyze",
                    http_status_code=200,
                    processing_duration_ms=1,
                    request_timestamp=now - timedelta(days=1, seconds=1),
                )
            )

        allowed = self._analyze_with_key("replace-with-a-long-random-secret")
        limited = self._analyze_with_key("replace-with-a-long-random-secret")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertGreaterEqual(int(limited.headers["Retry-After"]), 1)
        self.assertEqual(self.mock_analyzer.call_count, 1)

    def test_plan_request_limit_is_enforced(self):
        with self.session_factory.begin() as session:
            plan = Plan(
                name="one-per-minute-test",
                request_limit_per_minute=1,
                quota_limit_per_day=0,
                active_api_key_limit=1,
                is_default=False,
            )
            session.add(plan)
            session.flush()
            project = session.scalar(
                select(Project).where(Project.public_project_id == "example-project")
            )
            project.plan_id = plan.id

        first = self._analyze_with_key("replace-with-a-long-random-secret")
        limited = self._analyze_with_key("replace-with-a-long-random-secret")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(self.mock_analyzer.call_count, 1)

    def test_environment_daily_quota_can_limit_projects_without_plan(self):
        with patch.dict(os.environ, {"COGNISENSE_DEFAULT_DAILY_QUOTA": "1"}):
            first = self._analyze_with_key("replace-with-a-long-random-secret")
            limited = self._analyze_with_key("replace-with-a-long-random-secret")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"]["code"], "rate_limited")

    def test_rate_limit_is_concurrency_safe(self):
        temporary_database = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_database.cleanup)
        concurrent_engine = create_engine(
            f"sqlite+pysqlite:///{temporary_database.name}\\gateway.db",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(concurrent_engine)
        self.addCleanup(concurrent_engine.dispose)
        concurrent_factory = sessionmaker(bind=concurrent_engine, expire_on_commit=False)
        with concurrent_factory.begin() as session:
            owner = User(name="Concurrent Owner", email="concurrent-owner@example.com")
            session.add(owner)
            session.flush()
            session.add(
                Project(
                    owner_user_id=owner.id,
                    display_name="Concurrent Project",
                    public_project_id="example-project",
                    status="active",
                )
            )

        self.session_factory_patch.stop()
        concurrent_patch = patch(
            "api_gateway.get_session_factory",
            return_value=concurrent_factory,
        )
        concurrent_patch.start()
        self.addCleanup(concurrent_patch.stop)

        clients = [TestClient(api_gateway.app), TestClient(api_gateway.app)]
        barrier = threading.Barrier(2)

        def send(client):
            barrier.wait(timeout=5)
            return client.post(
                "/api/v1/analyze",
                headers={"X-API-Key": "replace-with-a-long-random-secret"},
                json={"project_id": "example-project", "text": "Feedback"},
            )

        with patch.dict(
            os.environ,
            {
                "COGNISENSE_RATE_LIMIT_REQUESTS": "1",
                "COGNISENSE_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(send, clients))

        self.assertEqual(sorted(response.status_code for response in responses), [200, 429])
        with concurrent_factory() as session:
            successful = session.scalar(
                select(func.count(UsageRecord.id)).where(UsageRecord.http_status_code == 200)
            )
            limited = session.scalar(
                select(func.count(UsageRecord.id)).where(UsageRecord.http_status_code == 429)
            )
            self.assertEqual((successful, limited), (1, 1))

    def test_usage_reservation_failure_fails_closed_before_analysis(self):
        self.session_factory_patch.stop()
        failed_factory = patch(
            "api_gateway.get_session_factory",
            side_effect=SQLAlchemyError("private usage database error"),
        )
        failed_factory.start()
        self.addCleanup(failed_factory.stop)

        response = self._analyze_with_key("replace-with-a-long-random-secret")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")
        self.assertNotIn("private usage database error", response.text)
        self.mock_analyzer.assert_not_called()

    def test_usage_finalization_failure_does_not_return_success(self):
        def fail_usage_update(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE USAGE_RECORDS"):
                raise SQLAlchemyError("private outcome database error")

        event.listen(self.engine, "before_cursor_execute", fail_usage_update)
        try:
            response = self._analyze_with_key("replace-with-a-long-random-secret")
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_usage_update)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")
        self.assertNotIn("private outcome database error", response.text)
        self.assertNotIn('"success":true', response.text)
        self.mock_analyzer.assert_called_once()


    def test_streaming_request_limit_returns_413(self):
        limited_app = api_gateway.FastAPI()

        @limited_app.post("/")
        async def receive_body(request: api_gateway.Request):
            await request.body()
            return {"success": True}

        limited_app.add_exception_handler(
            api_gateway.HTTPException,
            api_gateway.http_error_handler,
        )
        limited_app.add_middleware(
            api_gateway.RequestMetadataAndSizeMiddleware,
            max_bytes=8,
        )
        response = TestClient(limited_app).post("/", content=b"123456789")

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "request_too_large")


if __name__ == "__main__":
    unittest.main()
