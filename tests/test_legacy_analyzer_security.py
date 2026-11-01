import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app
import api_gateway
from werkzeug.serving import make_server
from database.models import Base, Project, UsageRecord, User
from sqlalchemy import select
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
        self.app_factory_patch = patch("app.get_session_factory", return_value=self.session_factory)
        self.app_factory_patch.start()
        self.addCleanup(self.app_factory_patch.stop)
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
        self.client.get("/text")

    def test_text_document_upload_is_removed_after_analysis(self):
        with patch.object(app, "analyze_text_from_string", return_value={"overall": {"label": "positive"}}) as analyzer:
            response = self.client.post(
                "/api/public/analyze/text",
                data={"file": (BytesIO(b"helpful feedback"), "../../feedback.txt")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analyzer.call_args.args[0], "helpful feedback")
        self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_uploads_are_contained_and_removed_for_each_file_analyzer(self):
        routes = (
            ("/api/public/analyze/audio", "analyze_audio_file", "escape.wav"),
            ("/api/public/analyze/video", "analyze_video_file", "escape.mp4"),
            ("/api/public/analyze/image", "analyze_image_file", "escape.png"),
        )
        for route, analyzer, filename in routes:
            with self.subTest(route=route):
                with patch.object(app, analyzer, side_effect=lambda path: {"path": path}) as mock_analyzer:
                    response = self.client.post(
                        route,
                        data={"file": (BytesIO(b"payload"), f"../../{filename}")},
                        content_type="multipart/form-data",
                    )

                saved_path = mock_analyzer.call_args.args[0]
                self.assertEqual(response.status_code, 200)
                self.assertEqual(os.path.commonpath([self.upload_directory.name, saved_path]), self.upload_directory.name)
                self.assertFalse(os.path.exists(saved_path))
                self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_upload_is_removed_when_analyzer_raises(self):
        with patch.object(app, "analyze_audio_file", side_effect=RuntimeError("analysis failed")):
            response = self.client.post(
                "/api/public/analyze/audio",
                data={"file": (BytesIO(b"payload"), "feedback.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(os.listdir(self.upload_directory.name), [])

    def test_realtime_errors_do_not_disclose_exception_details(self):
        socket_client = app.socketio.test_client(app.app, flask_test_client=self.client)
        self.addCleanup(socket_client.disconnect)
        with patch.object(app, "analyze_frame_from_b64", side_effect=RuntimeError("private internal detail")):
            socket_client.emit("public_realtime_frame", {"image": "frame"})
            reply = socket_client.get_received()[0]["args"][0]

        self.assertEqual(reply["error"], "Frame analysis could not be completed.")
        self.assertNotIn("private internal detail", str(reply))

    def test_internal_analyzer_routes_are_not_externally_callable(self):
        with patch.object(app, "analyze_text_from_string") as analyzer:
            responses = [
                self.client.post("/api/analyze/text", data={"text": "hello"}, headers={"X-API-Key": self.API_KEY}),
                self.client.post("/api/analyze/audio", data={"file": (BytesIO(b"x"), "x.wav")}, headers={"X-API-Key": self.API_KEY}),
                self.client.post("/api/analyze/video", data={"file": (BytesIO(b"x"), "x.mp4")}, headers={"X-API-Key": self.API_KEY}),
                self.client.post("/api/analyze/image", data={"file": (BytesIO(b"x"), "x.png")}, headers={"X-API-Key": self.API_KEY}),
            ]
        self.assertEqual([response.status_code for response in responses], [404] * 4)
        analyzer.assert_not_called()

    def test_public_proxy_analyzes_anonymously_without_gateway_usage(self):
        with patch.object(app, "analyze_text_from_string", return_value={"ok": True}) as analyzer:
            response = self.client.post(
                "/api/public/analyze/text", data={"text": "hello"}
            )
        self.assertEqual(response.status_code, 200)
        analyzer.assert_called_once_with("hello")
        with self.session_factory() as session:
            self.assertEqual(session.scalar(select(UsageRecord)), None)

    def test_direct_analyzers_enforce_rate_and_daily_quota(self):
        with patch.object(app, "PUBLIC_RATE_LIMIT", 1), patch.object(
            app, "analyze_text_from_string", return_value={"ok": True}
        ) as analyzer:
            self.assertEqual(self.client.post("/api/public/analyze/text", data={"text": "one"}).status_code, 200)
            limited = self.client.post("/api/public/analyze/text", data={"text": "two"})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["error"]["code"], "rate_limited")
        self.assertEqual(analyzer.call_count, 1)

    def test_public_session_rate_limit(self):
        with patch.object(app, "PUBLIC_SESSION_RATE_LIMIT", 1), patch.object(
            app, "analyze_text_from_string", return_value={"ok": True}
        ) as analyzer:
            first = self.client.post("/api/public/analyze/text", data={"text": "one"})
            limited = self.client.post("/api/public/analyze/text", data={"text": "two"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(analyzer.call_count, 1)

    def test_public_proxy_enforces_size_limit_and_upload_types(self):
        with patch.object(api_gateway, "MAX_REQUEST_BYTES", 4), patch.dict(
            app.app.config, {"MAX_CONTENT_LENGTH": 4}
        ):
            oversized = self.client.post("/api/public/analyze/text", data={"text": "longer than four bytes"})
        self.assertEqual(oversized.status_code, 413)
        unsupported = self.client.post(
            "/api/public/analyze/image",
            data={"file": (BytesIO(b"data"), "payload.gif")},
            content_type="multipart/form-data",
        )
        self.assertEqual(unsupported.status_code, 415)

    def test_socket_requires_handshake_key_and_applies_quota_per_frame(self):
        with patch.object(app, "analyze_frame_from_b64") as unauthorized_analyzer:
            anonymous = app.socketio.test_client(app.app)
            self.assertFalse(anonymous.is_connected())
            unauthorized_analyzer.assert_not_called()
        socket_client = app.socketio.test_client(app.app, flask_test_client=self.client)
        self.addCleanup(socket_client.disconnect)
        self.assertTrue(socket_client.is_connected())
        with patch.object(app, "PUBLIC_SESSION_RATE_LIMIT", 1), patch.object(
            app, "analyze_frame_from_b64", return_value={"brightness": 1}
        ) as analyzer:
            socket_client.emit("public_realtime_frame", {"image": "frame"})
            first = socket_client.get_received()[0]["args"][0]
            socket_client.emit("public_realtime_frame", {"image": "frame"})
            limited = socket_client.get_received()[0]["args"][0]
            socket_client.emit("realtime_frame", {"image": "frame", "apiKey": self.API_KEY})
            internal = socket_client.get_received()[0]["args"][0]
        self.assertEqual(first["brightness"], 1)
        self.assertEqual(limited["status"], 429)
        self.assertEqual(internal["status"], 404)
        self.assertEqual(analyzer.call_count, 1)

    def test_public_proxy_rejects_when_analysis_capacity_is_full(self):
        acquired = 0
        try:
            for _ in range(app.PUBLIC_ANALYSIS_CONCURRENCY):
                self.assertTrue(app.PUBLIC_ANALYSIS_SLOTS.acquire(blocking=False))
                acquired += 1
            response = self.client.post("/api/public/analyze/text", data={"text": "busy"})
        finally:
            for _ in range(acquired):
                app.PUBLIC_ANALYSIS_SLOTS.release()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "analysis_busy")

    def test_public_proxy_timeout_is_bounded(self):
        def slow_analysis(_text):
            time.sleep(0.05)
            return {"ok": True}

        with patch.object(app, "PUBLIC_ANALYSIS_TIMEOUT", 0.001), patch.object(
            app, "analyze_text_from_string", side_effect=slow_analysis
        ):
            response = self.client.post("/api/public/analyze/text", data={"text": "slow"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["error"]["code"], "analysis_timeout")

    def test_internal_routes_are_rejected_over_loopback_http(self):
        flask_cookie = self.client.get_cookie(app.PUBLIC_SESSION_COOKIE)
        self.assertIsNotNone(flask_cookie)
        server = make_server("127.0.0.1", 0, app.app)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_port}"
        with patch.object(app, "analyze_text_from_string", return_value={"ok": True}) as analyzer:
            public_request = Request(
                f"{base_url}/api/public/analyze/text",
                data=b"text=anonymous+loopback+check",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": f"{app.PUBLIC_SESSION_COOKIE}={flask_cookie.value}",
                },
                method="POST",
            )
            with urlopen(public_request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            for modality in ("text", "audio", "video", "image"):
                internal_request = Request(
                    f"{base_url}/api/analyze/{modality}",
                    data=b"must+not+execute",
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Cookie": f"{app.PUBLIC_SESSION_COOKIE}={flask_cookie.value}",
                        "X-API-Key": self.API_KEY,
                    },
                    method="POST",
                )
                with self.subTest(modality=modality), self.assertRaises(HTTPError) as rejected:
                    urlopen(internal_request, timeout=5)
                self.assertEqual(rejected.exception.code, 404)
        self.assertEqual(analyzer.call_count, 1)

    def test_homepage_and_analyzer_pages_remain_public(self):
        for route in ("/", "/text", "/audio", "/video", "/image", "/realtime_video"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)


if __name__ == "__main__":
    unittest.main()