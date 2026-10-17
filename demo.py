#!/usr/bin/env python3
"""Demo entry: Factory Droid high-level control; local vision + RC; dongle 7 → TELLO-7.

AprilTag/YOLO steer on the laptop. Droid SDK session issues start/land (etc.)
via MCP tools — frames are NOT uploaded to Factory for detection.
Optional --crop-analysis adds Grok crop-health after the survey photo.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ground_station.voice_mission import main

if __name__ == "__main__":
    raise SystemExit(main())
