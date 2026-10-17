"""Host-owned vision/features/notification services and local audit history."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ground_station.agent.analysis import SurveyAnalysis


class SurveyPipeline:
    def __init__(self, driver, analyzer, *, field_id="field-1", photon=None,
                 history_path=Path("data/surveys.sqlite3"), chalk=False, chalk_branch=None):
        # Validate the host field identifier before any camera/flight work.
        SurveyAnalysis(field_id=field_id, health_score=0.5, analysis_summary="validation")
        self.driver = driver
        self.analyzer = analyzer
        self.field_id = field_id
        self.photon = photon
        self.history_path = Path(history_path)
        self.chalk = chalk
        self.chalk_branch = chalk_branch
        self.latest = None
        self.run_id = uuid.uuid4().hex
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS surveys "
                       "(id TEXT PRIMARY KEY, field_id TEXT, created REAL, observation TEXT, decision TEXT)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.history_path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def context(self) -> dict:
        with self._db() as db:
            rows = db.execute(
                "SELECT observation, decision FROM surveys WHERE field_id = ? "
                "ORDER BY created DESC LIMIT 5", (self.field_id,),
            ).fetchall()
        return {"field_id": self.field_id, "latest": self.latest,
                "history": [{"observation": json.loads(row[0]), "decision": row[1]} for row in rows]}

    async def process_photo(self, controller, path: Path, capture_id: int) -> dict:
        analysis = SurveyAnalysis.model_validate(await self.analyzer.analyze(path, self.field_id))
        if analysis.field_id != self.field_id:
            raise ValueError("Analyzer returned another field")
        features = {"source": "local_thresholds", "health_flag":
                    "healthy" if analysis.health_score >= 0.7 else
                    "watch" if analysis.health_score >= 0.4 else "critical"}
        features["recommended_action"] = {
            "healthy": "continue", "watch": "reimage", "critical": "alert-operator",
        }[features["health_flag"]]
        features["severity"] = round(1 - analysis.health_score, 3)
        if self.chalk:
            from ground_station.chalk_quality import evaluate_survey_named

            result = await asyncio.wait_for(asyncio.to_thread(
                evaluate_survey_named, **analysis.model_dump(), drone_id=self.driver.drone_id,
                branch=self.chalk_branch,
            ), timeout=20)
            features = {"source": "chalk", **result}
        if not self.driver.accept_analysis(capture_id):
            return {"ok": False, "reason": "stale_or_stopped_analysis"}
        receipt = f"{self.run_id}:{capture_id}"
        self.latest = {
            "receipt": receipt, "capture_id": capture_id,
            "vision_source": self.analyzer.source, **analysis.model_dump(), "features": features,
        }
        with self._db() as db:
            db.execute("INSERT INTO surveys VALUES (?, ?, ?, ?, ?)",
                       (receipt, self.field_id, time.time(), json.dumps(self.latest), "pending"))
        result = await controller.handle(
            "Review this validated image analysis and get_survey_context. Make a tool-driven "
            "decision now: capture_photo for a bounded reimage, or continue_survey to return home, "
            "or land/abort. For critical findings request send_photon_alert before returning. "
            "Never wait indefinitely. A queued reimage needs local approval on real hardware.",
            {"kind": "survey_analysis", **self.latest},
        )
        with self._db() as db:
            db.execute("UPDATE surveys SET decision = ? WHERE id = ?",
                       (json.dumps({"message": result["message"], "state": result["state"]}), receipt))
        state = self.driver.snapshot()
        if (self.driver.mission.analysis_waiting() and not state["stopped"]
                and not state["photo_queued"] and state["pending_approval"] != "capture_photo"):
            self.driver.stop()
            raise RuntimeError("Droid returned without an actionable survey decision")
        return result

    def send_alert(self) -> dict:
        state = self.driver.snapshot()
        if self.latest is None or self.latest["capture_id"] != state["capture_id"]:
            return {"ok": False, "reason": "no_current_analysis"}
        if state["stopped"] or not state["analysis_ready"]:
            return {"ok": False, "reason": "stopped_or_reimage_pending"}
        if self.photon is None:
            return {"ok": False, "reason": "photon_not_configured"}
        accepted = self.photon.notify_analysis(self.latest)
        return {"ok": accepted, "status": "queued" if accepted else "already_queued_or_unavailable",
                "delivery_confirmed": False}

    async def close(self):
        await self.analyzer.close()
