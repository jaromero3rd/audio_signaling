#!/usr/bin/env python3
"""Demo entry: Factory Droid decides; laptop RC + dongle 7 → TELLO-7.

Default: --controller droid. Built-in Wi-Fi stays on internet (Factory/Grok);
USB stick from config/dongles.json number 7 joins SSID TELLO-7 via nmcli.
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
