import json
import os
import tempfile
import unittest
import uuid
from io import BytesIO
from unittest.mock import patch

import app
import api_gateway
from database.models import Base, Plan, Project, UsageRecord, User
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class LegacyAnalyzerSecurityTests(unittest.TestCase):
    API_KEY = "legacy-test-secret-key-that-is-long-enough"
    PROJECT_ID = "legacy-analyzer-test"

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "COGNISENSE_API_KEYS": json.dumps({self.PROJECT_ID: self.API_KEY}),
            "COGNISENSE_LEGACY_API_KEYS_ENABLED": "true",
            "COGNISENSE_RATE_LIMIT_REQUESTS": "60",
            "COGNISENSE_RATE_LIMIT_WINDOW_SECONDS": "60",
            "COGNISENSE_DEFAULT_DAILY_QUOTA": "0",
            "AUTH_SECRET_KEY": "legacy-analyzer-test-secret-with-at-least-32-bytes",
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
        self.gateway_factory_patch = patch("api_gateway.get_session_factory", return_value=self.session_factory)
        self.gateway_factory_patch.start()
        self.addCleanup(self.gateway_factory_patch.stop)
        self.addCleanup(self.engine.dispose)
        with self.session_factory.begin() as session:
            owner = User(name="Analyzer Owner", email=f"{uuid.uuid4()}@example.com", status="active")
            session.add(owner)
            session.flush()
            session.add(Project(
                owner_user_id=owner.id,
                display_name="Analyzer Project",
                public_project_id=self.PROJECT_ID,
                status="active",
            ))

        self.upload_directory = tempfile.TemporaryDirectory()
        self.upload_folder_patch = patch.object(app, "UPLOAD_FOLDER", self.upload_directory.name)
        self.upload_folder_patch.start()
        self.addCleanup(self.upload_folder_patch.stop)
        self.addCleanup(self.upload_directory.cleanup)
        self.client = app.app.test_client()

    def test_text_document_upload_is_removed_after_analysis(self):
        with patch.object(app, "analyze_text_from_string", return_value={"overall": {"label": "positive"}}) as analyzer:
            response = self.client.post(
                "/api/analyze/text",
                data={"file": (BytesIO(b"helpful feedback"), "../../feedback.txt")},
                content_type="multipart/form-data",
                headers={"X-API-Key": self.API_KEY},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analyzer.call_args.args[0], "helpful feedback")
        self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_uploads_are_contained_and_removed_for_each_file_analyzer(self):
        routes = (
            ("/api/analyze/audio", "analyze_audio_file", "escape.wav"),
            ("/api/analyze/video", "analyze_video_file", "escape.mp4"),
            ("/api/analyze/image", "analyze_image_file", "escape.png"),
        )
        for route, analyzer, filename in routes:
            with self.subTest(route=route):
                with patch.object(app, analyzer, side_effect=lambda path: {"path": path}) as mock_analyzer:
                    response = self.client.post(
                        route,
                        data={"file": (BytesIO(b"payload"), f"../../{filename}")},
                        content_type="multipart/form-data",
                        headers={"X-API-Key": self.API_KEY},
                    )

                saved_path = mock_analyzer.call_args.args[0]
                self.assertEqual(response.status_code, 200)
                self.assertEqual(os.path.commonpath([self.upload_directory.name, saved_path]), self.upload_directory.name)
                self.assertFalse(os.path.exists(saved_path))
                self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_upload_is_removed_when_analyzer_raises(self):
        with patch.object(app, "analyze_audio_file", side_effect=RuntimeError("analysis failed")):
            response = self.client.post(
                "/api/analyze/audio",
                data={"file": (BytesIO(b"payload"), "feedback.wav")},
                content_type="multipart/form-data",
                headers={"X-API-Key": self.API_KEY},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_realtime_errors_do_not_disclose_exception_details(self):
        socket_client = app.socketio.test_client(app.app, auth={"apiKey": self.API_KEY})
        self.addCleanup(socket_client.disconnect)
        with patch.object(app, "analyze_frame_from_b64", side_effect=RuntimeError("private internal detail")):
            socket_client.emit("realtime_frame", {"image": "frame", "apiKey": self.API_KEY})
            reply = socket_client.get_received()[0]["args"][0]

        self.assertEqual(reply["error"], "Frame analysis could not be completed.")
        self.assertNotIn("private internal detail", str(reply))

    def test_direct_analyzer_routes_require_api_keys(self):
        with patch.object(app, "analyze_text_from_string") as analyzer:
            responses = [
                self.client.post("/api/analyze/text", data={"text": "hello"}),
                self.client.post("/api/analyze/audio", data={"file": (BytesIO(b"x"), "x.wav")}),
                self.client.post("/api/analyze/video", data={"file": (BytesIO(b"x"), "x.mp4")}),
                self.client.post("/api/analyze/image", data={"file": (BytesIO(b"x"), "x.png")}),
            ]
        self.assertEqual([response.status_code for response in responses], [401] * 4)
        self.assertTrue(all(response.get_json()["code"] == "unauthorized" for response in responses))
        analyzer.assert_not_called()

    def test_direct_analyzers_reserve_and_finalize_usage(self):
        with patch.object(app, "analyze_text_from_string", return_value={"ok": True}) as analyzer:
            response = self.client.post(
                "/api/analyze/text", data={"text": "hello"}, headers={"X-API-Key": self.API_KEY}
            )
        self.assertEqual(response.status_code, 200)
        analyzer.assert_called_once_with("hello")
        with self.session_factory() as session:
            usage = session.scalar(select(UsageRecord))
            self.assertEqual(usage.endpoint, "/api/analyze/text")
            self.assertEqual(usage.http_status_code, 200)

    def test_direct_analyzers_enforce_rate_and_daily_quota(self):
        headers = {"X-API-Key": self.API_KEY}
        with patch.dict(os.environ, {"COGNISENSE_RATE_LIMIT_REQUESTS": "1"}), patch.object(
            app, "analyze_text_from_string", return_value={"ok": True}
        ) as analyzer:
            self.assertEqual(self.client.post("/api/analyze/text", data={"text": "one"}, headers=headers).status_code, 200)
            limited = self.client.post("/api/analyze/text", data={"text": "two"}, headers=headers)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["Retry-After"], "60")
        self.assertEqual(analyzer.call_count, 1)

        with self.session_factory.begin() as session:
            session.execute(delete(UsageRecord))
            project = session.scalar(select(Project).where(Project.public_project_id == self.PROJECT_ID))
            plan = Plan(name="One daily request", quota_limit_per_day=1)
            session.add(plan)
            session.flush()
            project.plan_id = plan.id
        with patch.dict(os.environ, {"COGNISENSE_RATE_LIMIT_REQUESTS": "60"}), patch.object(
            app, "analyze_text_from_string", return_value={"ok": True}
        ) as analyzer:
            first = self.client.post("/api/analyze/text", data={"text": "one"}, headers=headers)
            exhausted = self.client.post("/api/analyze/text", data={"text": "two"}, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(exhausted.status_code, 429)
        self.assertEqual(analyzer.call_count, 1)

    def test_socket_requires_handshake_key_and_applies_quota_per_frame(self):
        anonymous = app.socketio.test_client(app.app)
        self.assertFalse(anonymous.is_connected())
        with self.session_factory.begin() as session:
            project = session.scalar(select(Project).where(Project.public_project_id == self.PROJECT_ID))
            plan = Plan(name="One live frame", quota_limit_per_day=1)
            session.add(plan)
            session.flush()
            project.plan_id = plan.id
        socket_client = app.socketio.test_client(app.app, auth={"apiKey": self.API_KEY})
        self.addCleanup(socket_client.disconnect)
        self.assertTrue(socket_client.is_connected())
        with patch.object(app, "analyze_frame_from_b64", return_value={"brightness": 1}) as analyzer:
            socket_client.emit("realtime_frame", {"image": "frame", "apiKey": self.API_KEY})
            first = socket_client.get_received()[0]["args"][0]
            socket_client.emit("realtime_frame", {"image": "frame", "apiKey": self.API_KEY})
            limited = socket_client.get_received()[0]["args"][0]
        self.assertEqual(first["brightness"], 1)
        self.assertEqual(limited["status"], 429)
        self.assertEqual(analyzer.call_count, 1)

    def test_homepage_and_analyzer_pages_remain_public(self):
        for route in ("/", "/text", "/audio", "/video", "/image", "/realtime_video"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)


if __name__ == "__main__":
    unittest.main()