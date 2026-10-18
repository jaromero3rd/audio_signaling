#!/usr/bin/env python3
"""Demo entry: Photon iMessage commands → Factory Droid; local vision + RC.

Default radios: Mac Wi-Fi → Tello; USB phone hotspot → internet + Uno Q.

AprilTag/YOLO steer on the laptop. Photon text queues start/abort to Droid.
Optional --crop-analysis attaches the survey JPEG to the same Factory session.
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
