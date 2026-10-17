"""Embedded Factory Droid session for mission decision-making.

Provides a long-lived agent session that receives photo analysis results and mission
state, makes decisions about next actions (reimage, alert operator, continue survey),
and can call tools to execute those decisions.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from ground_station.agent.store import append_decision, load_mission_state, save_mission_state


class _MockDroidSession:
    """Fallback mock session when Factory SDK is not available."""

    def __init__(self):
        self.state = load_mission_state()

    def feed_analysis(self, analysis: dict) -> None:
        """Receive photo analysis + mission state. Store for later."""
        self.state.update({
            "last_analysis": analysis,
            "updated_at": analysis.get("ts"),
        })
        save_mission_state(self.state)

    def get_decision(self) -> dict:
        """Return mock decision based on health score."""
        analysis = self.state.get("last_analysis", {})
        health = analysis.get("health_score", 0.5)
        if health >= 0.7:
            return {"action": "continue_survey", "reason": f"health {health:.2f} is good"}
        elif health >= 0.4:
            return {"action": "reimage", "reason": f"health {health:.2f} needs review"}
        else:
            return {"action": "alert_operator", "reason": f"health {health:.2f} is critical"}

    def close(self) -> None:
        """Shutdown session."""
        save_mission_state(self.state)


class DroidSession:
    """Embedded Factory Droid session.

    Long-lived agent that receives analysis results and mission state, decides
    next actions, and can call tools.
    """

    def __init__(self):
        """Initialize Droid session with tool registry."""
        try:
            import factory
            # TODO: Confirm actual Factory SDK package name and authentication
            self._impl = factory.DroidSession()  # type: ignore[attr-defined]
            self._is_mock = False
        except (ImportError, AttributeError):
            self._impl = _MockDroidSession()
            self._is_mock = True
            print("Factory SDK not available; using mock Droid session")

        # Tool registry
        self._tools = {
            "issue_drone_command": self._issue_drone_command,
            "request_reimage": self._request_reimage,
            "send_photon_alert": self._send_photon_alert,
            "query_chalk_features": self._query_chalk_features,
        }

    def feed_analysis(self, analysis: dict) -> None:
        """Feed photo analysis result + mission state to session.

        Args:
            analysis: {"photo_url": str, "health_score": float,
                      "field_id": str, "mission_state": dict, ...}
        """
        self._impl.feed_analysis(analysis)
        # Log decision for history
        append_decision({
            "type": "analysis_received",
            "photo_url": analysis.get("photo_url"),
            "health_score": analysis.get("health_score"),
        })

    def get_decision(self) -> dict:
        """Get Droid's next action decision.

        Returns:
            {"action": str, "tool_call": {...}, "reason": str, ...}
        """
        return self._impl.get_decision()

    def close(self) -> None:
        """Shutdown session and persist state."""
        self._impl.close()

    # Tool stubs (called by Droid)

    def _issue_drone_command(self, drone_id: str, waypoints: list, action: str) -> dict:
        """Issue command to drone.

        Args:
            drone_id: Target drone identifier
            waypoints: List of (lat, lon, alt) tuples
            action: Command name (e.g., "survey", "reimage", "return")

        Returns:
            {"ok": bool, "message": str}
        """
        # TODO: Wire to actual drone controller
        print(f"[STUB] Drone {drone_id}: {action} at {len(waypoints)} waypoints")
        append_decision({
            "type": "drone_command",
            "drone_id": drone_id,
            "action": action,
            "waypoint_count": len(waypoints),
        })
        return {"ok": True, "message": f"Command queued: {action}"}

    def _request_reimage(self, drone_id: str, lat: float, lon: float) -> dict:
        """Request drone to reimage a location.

        Args:
            drone_id: Target drone
            lat, lon: Coordinates to image

        Returns:
            {"ok": bool, "message": str}
        """
        # TODO: Wire to drone + mission planner
        print(f"[STUB] Reimage {drone_id} at ({lat:.4f}, {lon:.4f})")
        append_decision({
            "type": "reimage_requested",
            "drone_id": drone_id,
            "lat": lat,
            "lon": lon,
        })
        return {"ok": True, "message": f"Reimage scheduled at ({lat:.4f}, {lon:.4f})"}

    def _send_photon_alert(self, message: str, photo_url: str,
                           photo_base64: str | None = None) -> dict:
        """Send alert to operator via Photon.

        Args:
            message: Alert text
            photo_url: Remote URL or local path to photo
            photo_base64: Base64-encoded image bytes (takes priority over photo_url)

        Returns:
            {"ok": bool, ...}
        """
        from ground_station.photon_client import send_alert as _photon_send

        result = _photon_send(message, photo_url=photo_url, photo_base64=photo_base64)
        append_decision({
            "type": "alert_sent",
            "message": message,
            "photo_url": photo_url,
            "ok": result.get("ok"),
        })
        return result

    def _query_chalk_features(self, field_id: str) -> dict:
        """Query Chalk features for a field.

        Args:
            field_id: Field identifier

        Returns:
            Chalk feature evaluation result
        """
        # TODO: Wire to chalk_eval.py
        from ground_station.chalk_client import get_client

        try:
            # Placeholder: would call chalk_eval.evaluate_survey()
            return {"field_id": field_id, "health": 0.6, "action": "watch"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
