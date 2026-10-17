#!/usr/bin/env python3
"""Demo entry: Grok on laptop Wi-Fi, Tello on USB Wi-Fi dongle."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground_station.voice_mission import main

if __name__ == "__main__":
    raise SystemExit(main())
