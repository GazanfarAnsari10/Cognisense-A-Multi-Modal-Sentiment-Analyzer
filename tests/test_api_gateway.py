import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api_gateway


class FeedbackGatewayTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "COGNISENSE_API_KEYS": (
                    '{"example-project":"replace-with-a-long-random-secret"}'
                )
            },
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
