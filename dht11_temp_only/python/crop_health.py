"""Crop stress / dry-out ideation from temp, humidity, and sunlight.

Runs on the UNO Q Linux MPU. This is a transparent rule + score model that
mirrors what an on-device AI agent would reason about. Later you can swap
`analyze()` for an App Lab Agent / Edge Impulse brick while keeping the same
input/output shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CropAssessment:
    status: str  # healthy | watch | stress | critical
    score: float  # 0 = healthy, 1 = severe stress
    dry_out_risk: float
    light_adequacy: str
    notes: list[str]
    actions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stress_score": round(self.score, 3),
            "dry_out_risk": round(self.dry_out_risk, 3),
            "light_adequacy": self.light_adequacy,
            "notes": self.notes,
            "actions": self.actions,
        }


def analyze(
    temp_c: float,
    humidity_pct: float,
    light: float,
    *,
    invert_light: bool = False,
    vision: dict[str, Any] | None = None,
) -> CropAssessment:
    """Ideate crop health from air climate + sunlight proxy.

    Assumptions (demo / wheat-like field crop, daytime sample):
    - Low RH + high temp + strong sun => dry-out / wilt risk
    - Very low light for long periods => weak photosynthesis (watch)
    - Extreme heat => heat stress even if humidity is OK
    """
    # light is already calibrated: 0=dark … 1.0=scorching (crop burn risk)
    if invert_light:
        light = 1.0 - light
    light = max(0.0, min(1.0, light))
    rh = max(0.0, min(100.0, humidity_pct))
    t = temp_c

    notes: list[str] = []
    actions: list[str] = []
    score = 0.0

    # --- Sunlight band (1.0 = scorching) ---
    if light < 0.15:
        light_label = "too_dark"
        notes.append("Sunlight very low — photosynthesis limited.")
        score += 0.15
        actions.append("Confirm sensor is outdoors / not shaded by the drone body.")
    elif light < 0.40:
        light_label = "low"
        notes.append("Light is on the low side for vigorous growth.")
        score += 0.08
    elif light < 0.70:
        light_label = "good"
        notes.append("Sunlight in a useful growing band.")
    elif light < 0.90:
        light_label = "strong"
        notes.append("Strong sun — drying risk rises if air is also dry/hot.")
        score += 0.10
    else:
        light_label = "scorching"
        notes.append("Scorching sun level — leaf burn / rapid surface dry-out risk.")
        score += 0.22
        actions.append("Avoid mid-day survey over exposed canopy; flag heat/scorch risk.")

    # --- Dry-out: vapor demand (temp) + moisture availability (RH) + sun ---
    # Vapor pressure deficit proxy: hot + dry air pulls water from crops.
    dry = 0.0
    if rh < 30:
        dry += 0.45
        notes.append("Air humidity is low — crops lose water faster.")
    elif rh < 45:
        dry += 0.25
        notes.append("Humidity trending dry.")
    elif rh > 85:
        notes.append("Humidity high — less dry-out, but watch fungal risk later.")
        dry -= 0.05

    if t >= 35:
        dry += 0.35
        notes.append("Heat stress range — stomata close, growth slows.")
        actions.append("Prefer irrigation / survey earlier morning or late day.")
    elif t >= 30:
        dry += 0.2
        notes.append("Warm air increases evaporation.")
    elif t < 5:
        notes.append("Cold stress — not dry-out, but growth stalls.")
        score += 0.2

    if light_label in ("scorching", "strong") and rh < 50:
        dry += 0.2
        notes.append("Harsh sun + dry air: classic surface dry-out pattern.")

    dry = max(0.0, min(1.0, dry))
    score = max(0.0, min(1.0, score + dry * 0.7))

    if dry >= 0.65:
        actions.append("Flag field for soil-moisture check / irrigation priority.")
    if rh < 40 and t >= 28:
        actions.append("Prioritize wheat/crop zones with afternoon wilt symptoms.")

    # --- Optional drone-camera vision summary (not raw video) ---
    if vision:
        label = str(vision.get("crop_label") or vision.get("label") or "").lower()
        conf = float(vision.get("confidence") or vision.get("conf") or 0.0)
        wilt = bool(vision.get("wilt") or vision.get("dry_leaves"))
        yellow = bool(vision.get("yellowing") or vision.get("chlorosis"))
        notes.append(f"Drone vision hint: {label or 'unlabeled'} conf={conf:.2f}.")
        if wilt or "wilt" in label or "dry" in label:
            score = min(1.0, score + 0.25)
            dry = min(1.0, dry + 0.2)
            notes.append("Camera suggests wilt / dry canopy — climate dry-out more likely.")
            actions.append("Cross-check soil moisture where the photo was taken.")
        if yellow or "yellow" in label or "deficient" in label:
            score = min(1.0, score + 0.15)
            notes.append("Camera suggests yellowing — nutrient or water stress possible.")
            actions.append("Log GPS/tag id for a ground nutrient check.")
        if conf >= 0.6 and ("healthy" in label or "wheat" in label) and score < 0.4:
            notes.append("Vision looks healthy; climate sensors are the main watch item.")

    if score < 0.25:
        status = "healthy"
    elif score < 0.45:
        status = "watch"
    elif score < 0.7:
        status = "stress"
    else:
        status = "critical"

    if not actions and status == "healthy":
        actions.append("Continue survey; no climate stress signal.")

    return CropAssessment(
        status=status,
        score=score,
        dry_out_risk=dry,
        light_adequacy=light_label,
        notes=notes,
        actions=actions,
    )


def health_sentence(
    assessment: CropAssessment,
    temp_c: float,
    humidity_pct: float,
    *,
    light_band: str,
    light_note: str,
) -> str:
    """One plain-English sentence a farmer can hear/read."""
    status = assessment.status
    dry = assessment.dry_out_risk

    if status == "healthy":
        opener = "Crops look climate-healthy right now"
    elif status == "watch":
        opener = "Crops are mostly okay but worth watching"
    elif status == "stress":
        opener = "Crops show stress signals"
    else:
        opener = "Crops are under serious stress"

    climate = (
        f"with air at {temp_c:.1f}C and {humidity_pct:.0f}% humidity"
    )
    sun = f"under {light_band.replace('_', ' ')} light ({light_note})"

    if dry >= 0.65:
        risk = "and dry-out risk is high, so check soil moisture or irrigate soon"
    elif dry >= 0.35:
        risk = "and drying risk is moderate"
    elif light_band == "harsh":
        risk = "and harsh sun could scorch exposed leaves even if humidity is okay"
    elif light_band in ("dark", "indoor"):
        risk = "though low light may slow growth if this continues"
    else:
        risk = "with no urgent dry-out alarm from these sensors"

    action = assessment.actions[0] if assessment.actions else ""
    if action and status != "healthy":
        return f"{opener} {climate}, {sun}, {risk}. Next step: {action[0].lower() + action[1:] if action else action}"
    return f"{opener} {climate}, {sun}, {risk}."


def ai_rationale(assessment: CropAssessment, temp_c: float, humidity_pct: float, light: float) -> str:
    return health_sentence(
        assessment,
        temp_c,
        humidity_pct,
        light_band=assessment.light_adequacy,
        light_note=assessment.notes[0] if assessment.notes else "light sampled",
    )
