import csv
import io
import json
import unittest
from datetime import datetime

from PyPDF2 import PdfReader

from result_export import build_export


ANALYSIS_TIMESTAMP = "2026-10-23T09:00:00+00:00"
SAMPLE_RESULTS = [
    {
        "modality": "text",
        "source": "text",
        "result": {
            "overall": {"label": "positive", "score": 0.8},
            "word_level": [{"word": "great", "label": "positive", "score": 0.7}],
            "sentence_level": [{"sentence": "Great service", "label": "positive", "score": 0.8}],
            "ai_reasoning": {"summary": "Positive feedback.", "why": "Positive terms were detected."},
        },
    },
    {
        "modality": "text",
        "source": "review.pdf",
        "result": {"overall": {"label": "neutral", "score": 0.0}},
    },
    {
        "modality": "audio",
        "source": "call.wav",
        "result": {
            "transcription": "The service was helpful.",
            "analysis": {"overall": {"label": "positive", "score": 0.6}},
        },
    },
    {
        "modality": "video",
        "source": "meeting.mp4",
        "result": {
            "audio_analysis": {"analysis": {"overall": {"label": "neutral", "score": 0.0}}},
            "frame_brightness": {
                "frames": [{"time": 1.0, "brightness": 130.0}],
                "average": 130.0,
                "summary": "Average brightness: 130.00",
            },
        },
    },
    {
        "modality": "image",
        "source": "photo.jpg",
        "result": {
            "overall": {"label": "happy", "score": 0.9},
            "emotions": {"happy": 0.9, "sad": 0.1},
            "ai_reasoning": "A happy face was detected.",
        },
    },
    {"modality": "text", "source": "empty.txt", "error": {"code": "analysis_failed"}},
]


class ResultExportTests(unittest.TestCase):
    def export(self, file_format):
        return build_export(
            export_format=file_format,
            project_id="project-test",
            request_id="request-test",
            results=SAMPLE_RESULTS,
            analysis_timestamp=ANALYSIS_TIMESTAMP,
        )

    def test_json_export_preserves_results_timestamps_and_chart_series(self):
        body, media_type, extension = self.export("json")
        payload = json.loads(body)

        self.assertEqual(media_type, "application/json")
        self.assertEqual(extension, "json")
        self.assertEqual(payload["analysis_timestamp"], ANALYSIS_TIMESTAMP)
        datetime.fromisoformat(payload["exported_at"])
        self.assertEqual(payload["project_id"], "project-test")
        self.assertEqual(payload["data"]["status"], "partial")
        self.assertEqual(len(payload["data"]["results"]), len(SAMPLE_RESULTS))
        chart_data = payload["data"]["chart_data"]
        self.assertTrue(chart_data["sentiment_scores"])
        self.assertTrue(chart_data["word_scores"])
        self.assertTrue(chart_data["sentence_scores"])
        self.assertTrue(chart_data["frame_brightness"])
        self.assertTrue(chart_data["emotion_scores"])
        self.assertNotIn("api_key", body.decode("utf-8").lower())

    def test_csv_export_is_readable_and_contains_chart_ready_rows(self):
        body, media_type, extension = self.export("csv")
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

        self.assertTrue(media_type.startswith("text/csv"))
        self.assertEqual(extension, "csv")
        self.assertGreater(len(rows), len(SAMPLE_RESULTS))
        self.assertIn("chart_type", rows[0])
        self.assertTrue(any(row["chart_type"] == "sentiment_scores" for row in rows))
        self.assertTrue(any(row["chart_type"] == "frame_brightness" for row in rows))
        self.assertTrue(any(row["modality"] == "audio" for row in rows))
        self.assertTrue(any(row["modality"] == "image" for row in rows))

    def test_pdf_export_generates_a_report_with_chart_visualizations(self):
        body, media_type, extension = self.export("pdf")

        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(extension, "pdf")
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertGreater(len(body), 1000)

    def test_pdf_includes_modality_details_transcripts_tables_and_chart_labels(self):
        transcript = "The audio transcript is complete. " * 75 + "FINAL TRANSCRIPT SENTINEL"
        results = [
            {
                "modality": "text",
                "source": "customer-support-follow-up-feedback-report-2026.txt",
                "result": {
                    "overall": {"label": "negative", "score": -0.62},
                    "word_level": [{"word": "helpful", "label": "positive", "score": 0.42}],
                    "sentence_level": [{"sentence": "The support was helpful.", "label": "positive", "score": 0.42}],
                },
            },
            {
                "modality": "audio",
                "source": "interview.wav",
                "result": {
                    "transcription": transcript,
                    "analysis": {"overall": {"label": "positive", "score": 0.78}},
                },
            },
            {
                "modality": "image",
                "source": "portrait.jpg",
                "result": {
                    "overall": {"label": "happy", "score": 0.91},
                    "emotions": {"happy": 0.91, "sad": 0.04, "neutral": 0.05},
                    "image_analysis": {"face_count": 1, "description": "One face detected"},
                },
            },
            {
                "modality": "video",
                "source": "meeting.mp4",
                "result": {
                    "audio_analysis": {
                        "transcription": {
                            "text": "The video transcript includes spoken words.",
                            "segments": [
                                {"start": 0.4, "end": 1.8, "text": "The video transcript includes spoken words."},
                            ],
                        },
                        "analysis": {"overall": {"label": "neutral", "score": 0.01}},
                    },
                    "frame_brightness": {
                        "frames": [
                            {"time": 1.25, "brightness": 132.5},
                            {"time": 2.5, "brightness": 98.0},
                        ],
                        "average": 115.25,
                        "eval": "neutral",
                        "summary": "Average brightness: 115.25, Evaluation: neutral",
                    },
                },
            },
            {
                "modality": "multimodal",
                "source": "combined-input",
                "result": {
                    "combined_sentiment": {"label": "positive", "score": 0.73},
                    "text_analysis": {"summary": "Combined text and audio finding."},
                },
            },
        ]
        body, _, _ = build_export("pdf", "project-test", "request-test", results, ANALYSIS_TIMESTAMP)
        reader = PdfReader(io.BytesIO(body))
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

        self.assertGreater(len(reader.pages), 1)
        self.assertIn("Cognisense", extracted)
        self.assertIn("project-test", extracted)
        self.assertIn("customer-support-follow-up-feedback-report-2026.txt", extracted)
        self.assertIn("Word-level sentiment scores", extracted)
        self.assertIn("Sentence-level sentiment scores", extracted)
        self.assertIn("Emotion confidence scores", extracted)
        self.assertIn("Video frame brightness by timestamp", extracted)
        self.assertIn("Brightness (0 to 255)", extracted)
        self.assertIn("Sentiment score", extracted)
        self.assertIn("1.25", extracted)
        self.assertIn("132.5", extracted)
        self.assertIn("Full transcript", extracted)
        self.assertIn("FINAL TRANSCRIPT SENTINEL", extracted)
        self.assertIn("The video transcript includes spoken words.", extracted)
        self.assertIn("Segments", extracted)
        self.assertIn("Start", extracted)
        self.assertIn("0.4", extracted)
        self.assertIn("Combined Sentiment", extracted)
        self.assertIn("Overall scores by file", extracted)
        self.assertIn("Negative", extracted)
        self.assertIn("Neutral", extracted)
        self.assertIn("Positive", extracted)
        self.assertTrue(all(float(page.mediabox.width) == 612 for page in reader.pages))
        self.assertTrue(all(float(page.mediabox.height) == 792 for page in reader.pages))

        for item in results:
            with self.subTest(modality=item["modality"]):
                single_body, media_type, extension = build_export(
                    "pdf", "project-test", "request-test", [item], ANALYSIS_TIMESTAMP
                )
                single_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(single_body)).pages)
                self.assertEqual(media_type, "application/pdf")
                self.assertEqual(extension, "pdf")
                self.assertIn(item["source"], single_text)
                if item["modality"] == "audio":
                    self.assertIn("FINAL TRANSCRIPT SENTINEL", single_text)
                elif item["modality"] == "image":
                    self.assertIn("Emotion confidence scores", single_text)
                elif item["modality"] == "video":
                    self.assertIn("Segments", single_text)
                    self.assertIn("0.4", single_text)
                elif item["modality"] == "multimodal":
                    self.assertIn("Combined Sentiment", single_text)

    def test_pdf_handles_unicode_and_markup_in_analyzer_text(self):
        results = [
            {
                "modality": "text",
                "source": "feedback.txt",
                "result": {
                    "overall": {"label": "positive", "score": 0.8},
                    "ai_reasoning": {"summary": "Great <service> — 非常好 😊"},
                },
            }
        ]
        body, _, _ = build_export(
            "pdf",
            "project-test",
            "request-test",
            results,
            ANALYSIS_TIMESTAMP,
        )

        self.assertTrue(body.startswith(b"%PDF-"))

    def test_missing_chart_data_still_exports_all_formats(self):
        empty_results = [{"modality": "audio", "source": "silence.wav", "result": {}}]
        for file_format in ("json", "csv", "pdf"):
            with self.subTest(file_format=file_format):
                body, _, _ = build_export(
                    file_format,
                    "project-test",
                    "request-test",
                    empty_results,
                    ANALYSIS_TIMESTAMP,
                )
                self.assertTrue(body)

    def test_csv_escapes_spreadsheet_formula_prefixes(self):
        risky_results = [
            {
                "modality": "text",
                "source": "text",
                "result": {
                    "overall": {"label": "neutral", "score": 0.0},
                    "ai_reasoning": {"summary": "=HYPERLINK(\"https://example.com\")"},
                },
            }
        ]
        body, _, _ = build_export(
            "csv",
            "project-test",
            "request-test",
            risky_results,
            ANALYSIS_TIMESTAMP,
        )
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

        self.assertEqual(rows[0]["summary"], "'=HYPERLINK(\"https://example.com\")")

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(ValueError):
            self.export("xml")


if __name__ == "__main__":
    unittest.main()
