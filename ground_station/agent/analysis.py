"""Native Droid image attachments and strict, host-validated assessments."""

from __future__ import annotations

import json
from pathlib import Path

from droid_sdk import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    health_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    analysis_summary: str = Field(min_length=1, max_length=3000)

    @field_validator("analysis_summary")
    @classmethod
    def nonblank_summary(cls, value):
        if not value.strip():
            raise ValueError("Summary must not be blank")
        return value


class SurveyAnalysis(ImageAssessment):
    field_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class AnalysisError(RuntimeError):
    pass


def _unique_fields(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Duplicate JSON field")
        value[name] = item
    return value


class DroidImageAnalyzer:
    source = "droid"

    def image(self, path: Path) -> Image:
        # No path comes from a model tool. Bound the read, including file races.
        try:
            with path.open("rb") as capture:
                data = capture.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise AnalysisError("Missing captured image") from None
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise AnalysisError("Captured image must be nonempty and at most 5 MiB")
        if data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise AnalysisError("Capture must be JPEG, PNG, GIF or WebP")
        return Image.from_bytes(data, media_type=mime)

    @staticmethod
    def parse(text: str, field_id: str) -> SurveyAnalysis:
        try:
            if not isinstance(text, str) or len(text) > 20000:
                raise ValueError("Invalid assessment size")
            assessment = ImageAssessment.model_validate(json.loads(text, object_pairs_hook=_unique_fields))
            return SurveyAnalysis(field_id=field_id, **assessment.model_dump())
        except ValueError:
            raise AnalysisError("Droid returned invalid crop-health JSON") from None

    async def close(self):
        pass


class FakeImageAnalyzer:
    """Explicit test/demo fixture, never a fallback for failed analysis."""

    source = "fake"

    async def analyze(self, path: Path, field_id: str) -> SurveyAnalysis:
        if not path.is_file():
            raise AnalysisError("Missing simulated capture")
        return SurveyAnalysis(
            field_id=field_id, health_score=0.8,
            analysis_summary="SIMULATED result for pipeline testing. No crop diagnosis performed.",
        )

    async def close(self):
        pass
