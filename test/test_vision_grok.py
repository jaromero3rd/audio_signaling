import json
import tempfile
import unittest
from pathlib import Path

from ground_station.vision_grok import (
    RESPONSES_URL,
    analyze_photo,
    build_request,
    parse_crop_health,
)

JPEG = b"\xff\xd8\xff\xd9"


def _body(payload: dict) -> dict:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            },
        ],
    }


class VisionGrokTests(unittest.TestCase):
    def test_request_sends_image_and_schema_without_storing_history(self):
        payload = build_request(JPEG, "image/jpeg", "grok-4.7")

        self.assertEqual(payload["model"], "grok-4.7")
        self.assertFalse(payload["store"])
        content = payload["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_image")
        self.assertTrue(content[0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(
            payload["text"]["format"]["schema"]["required"],
            ["health_score", "analysis_summary", "observations"],
        )

    def test_parse_clamps_score_and_ignores_reasoning_items(self):
        result = parse_crop_health(
            _body(
                {
                    "health_score": 1.4,
                    "analysis_summary": "  Dense   green stand.  ",
                    "observations": ["even canopy", "  ", "yellow edge"],
                }
            ),
            photo_path="captures/field.jpg",
            model="grok-4.7",
        )

        self.assertEqual(result.health_score, 1.0)
        self.assertEqual(result.analysis_summary, "Dense green stand.")
        self.assertEqual(result.observations, ("even canopy", "yellow edge"))
        chalk = result.as_chalk_inputs()
        self.assertEqual(chalk["health_score"], 1.0)
        self.assertEqual(chalk["photo_url"], "captures/field.jpg")

    def test_parse_rejects_incomplete_and_bad_shapes(self):
        with self.assertRaises(RuntimeError):
            parse_crop_health({"status": "incomplete", "output_text": "{}"}, photo_path="a.jpg", model="m")
        with self.assertRaises(RuntimeError):
            parse_crop_health(
                {"output_text": json.dumps({"health_score": True, "analysis_summary": "x", "observations": []})},
                photo_path="a.jpg",
                model="m",
            )

    def test_analyze_photo_posts_the_file_and_returns_the_score(self):
        seen = {}

        def poster(url, payload, api_key, timeout):
            seen["url"] = url
            seen["api_key"] = api_key
            seen["timeout"] = timeout
            seen["model"] = payload["model"]
            self.assertIn("input_image", json.dumps(payload))
            return _body(
                {
                    "health_score": 0.42,
                    "analysis_summary": "Patchy canopy.",
                    "observations": ["thin stand"],
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "plot.jpg"
            photo.write_bytes(JPEG)
            result = analyze_photo(photo, api_key="test-key", model="grok-4.7", poster=poster, timeout=3)

        self.assertEqual(seen["url"], RESPONSES_URL)
        self.assertEqual(seen["api_key"], "test-key")
        self.assertEqual(seen["model"], "grok-4.7")
        self.assertEqual(result.health_score, 0.42)
        self.assertEqual(result.photo_path, str(photo))

    def test_rejects_missing_key_and_non_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "notes.txt"
            photo.write_text("not a photo", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                analyze_photo(photo, api_key="", poster=lambda *args: {})
            with self.assertRaises(ValueError):
                analyze_photo(photo, api_key="test-key", poster=lambda *args: {})


if __name__ == "__main__":
    unittest.main()
