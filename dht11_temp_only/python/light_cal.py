"""Map LDR ADC -> light 1..100.

  1   = dark
  50  = room at app start
 100  = super bright

Your setup: brighter light raises raw ADC (5V—LDR—A0—100k—GND).
So we do NOT invert. If levels go the wrong way, set INVERT = True.
"""

from __future__ import annotations

from dataclasses import dataclass

ADC_MAX = 1023.0

# False: higher raw = brighter (typical 5V-LDR-A0-resistor-GND)
# True:  higher raw = darker  (5V-resistor-A0-LDR-GND)
INVERT = True


@dataclass
class LightReading:
    raw: int
    level: int  # 1..100
    band: str


class LightCalibrator:
    def __init__(self, room_level: int = 50) -> None:
        self.room_level = max(1, min(99, room_level))
        self.invert = INVERT
        self._room: float | None = None
        self._min_b: float | None = None
        self._max_b: float | None = None
        self._boot_n = 0
        self._boot_sum = 0.0
        self._boot_needed = 8  # ~fast samples at high rate

    def _brightness(self, raw: float) -> float:
        raw = max(0.0, min(ADC_MAX, raw))
        return (ADC_MAX - raw) if self.invert else raw

    def update(self, raw_adc: float) -> LightReading:
        raw = float(max(0.0, min(ADC_MAX, raw_adc)))
        b = self._brightness(raw)

        # Boot: this room = 50
        if self._room is None:
            self._boot_sum += b
            self._boot_n += 1
            if self._boot_n < self._boot_needed:
                return LightReading(raw=int(raw), level=self.room_level, band="room")
            self._room = self._boot_sum / self._boot_n
            self._min_b = self._room
            self._max_b = self._room

        assert self._room is not None
        # Track extremes so 1 and 100 stretch to what you've actually seen
        self._min_b = min(self._min_b or b, b)
        self._max_b = max(self._max_b or b, b)

        room = self._room
        dark = min(self._min_b, room - 20)
        bright = max(self._max_b, room + 20)

        # Guarantee usable spans even before you've covered / lit the sensor
        if room - dark < 15:
            dark = max(0.0, room - 80)
        if bright - room < 15:
            bright = min(ADC_MAX, room + 80)

        if b <= room:
            # darker than room -> 1 .. 50
            span = max(1.0, room - dark)
            t = (b - dark) / span
            level = 1.0 + t * (self.room_level - 1.0)
        else:
            # brighter than room -> 50 .. 100
            span = max(1.0, bright - room)
            t = (b - room) / span
            level = self.room_level + t * (100.0 - self.room_level)

        level_i = int(round(max(1.0, min(100.0, level))))
        return LightReading(raw=int(raw), level=level_i, band=self._band(level_i))

    @staticmethod
    def _band(level: int) -> str:
        if level <= 15:
            return "dark"
        if level <= 40:
            return "dim"
        if level <= 60:
            return "room"
        if level <= 85:
            return "bright"
        return "super_bright"
