"""LDR calibration — absolute bands + slow auto-range (no forced 1.0=scorch).

We treat the ADC as a rough sunlight meter:
  - Absolute raw bands → human labels (dark / indoor / outdoor / harsh)
  - Session min–max auto-range → a relative 0–100% so indoor demos still move
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LightReading:
    raw: int
    percent: float  # 0–100 relative to what this session has seen
    band: str  # dark | indoor | bright_indoor | outdoor | harsh
    note: str


# Absolute raw bands (10-bit). Tune only if your divider/resistor differ a lot.
# These are starting points for: 5V—LDR—A0—10k—GND
BANDS = (
    (180, "dark", "little or no light"),
    (420, "indoor", "typical indoor / shade"),
    (700, "bright_indoor", "bright room or overcast outdoor"),
    (900, "outdoor", "useful daylight for crops"),
    (1024, "harsh", "very strong sun — scorch / rapid drying risk"),
)


class LightCalibrator:
    def __init__(self) -> None:
        self._min: float | None = None
        self._max: float | None = None
        self._n = 0

    def update(self, raw_adc: float) -> LightReading:
        raw = int(max(0, min(1023, float(raw_adc))))
        self._n += 1

        # Expand session range slowly so a brief shadow doesn't dominate.
        if self._min is None:
            self._min = float(raw)
            self._max = float(raw)
        else:
            self._min = min(self._min, float(raw))
            self._max = max(self._max, float(raw))
            # Keep a usable span even if values barely move
            if self._max - self._min < 40:
                mid = 0.5 * (self._min + self._max)
                self._min = mid - 20
                self._max = mid + 20

        span = max(1.0, (self._max or 1) - (self._min or 0))
        percent = 100.0 * (raw - (self._min or 0)) / span
        percent = max(0.0, min(100.0, percent))

        band = "harsh"
        note = "very strong sun — scorch / rapid drying risk"
        for limit, name, desc in BANDS:
            if raw < limit:
                band = name
                note = desc
                break

        return LightReading(raw=raw, percent=percent, band=band, note=note)


def light_for_analyze(reading: LightReading) -> float:
    """Map band → 0..1 stress-oriented light factor for crop_health.analyze()."""
    return {
        "dark": 0.08,
        "indoor": 0.28,
        "bright_indoor": 0.48,
        "outdoor": 0.72,
        "harsh": 0.95,
    }.get(reading.band, 0.5)
