# Arduino Uno Q — AprilTag waypoints + climate telemetry

Hackathon edge stack (Hack Harvard): the Uno Q surveys a field marked with **AprilTag waypoints**, measures **humidity / temperature** (+ extensible sensors), and **relays samples to the host ground station** when Wi‑Fi is up. If the host is unreachable, it **logs to microSD** and **seeks AprilTags** onboard until the link returns.

| Mode | Condition | Behavior |
| --- | --- | --- |
| **ONLINE** | Host reachable over Wi‑Fi | Stream climate (+ mission / tag) samples to the laptop ground station |
| **OFFLINE** | Host unreachable | Log the same JSONL to microSD **and** run AprilTag seek/hold on the Linux MPU |

```
AprilTag markers (waypoints / survey points)
        │
Sensors (I2C) ──► MCU sketch ──Bridge──► Linux coordinator
                                          │
                     ┌────────────────────┼────────────────────┐
                     ▼                    ▼                    ▼
              Host HTTP POST        SD card JSONL      AprilTag seek
              (when ONLINE)         (when OFFLINE)     (when OFFLINE)
              ground station
```

**Primary product story:** AprilTags as waypoints/survey markers → climate telemetry → ground station. Not a wheat-anomaly / crop-CV product (other folders in the repo may still contain wheat tooling; this `uno_q/` hub does not depend on them).

## Honesty note (AprilTags + “AI”)

- **AprilTag localization is detection + control**, not supervised training of tag IDs.
- Offline autonomy uses **OpenCV ArUco AprilTag dictionaries** (same approach as `ground_station/apriltag.py`) plus a simple yaw/forward seek-hold loop.
- Optional path: `linux/uno_q_linux/nav_assist_stub.py` is a **placeholder** for a future lightweight onboard model that *assists* heading toward a visual target. It does **not** “train AprilTags.” Do not demo it as trained tag recognition.

## Folder layout

```
uno_q/
├── README.md                 # this file
├── config.example.env        # copy → config.env (or export vars)
├── config.example.yaml       # alternate YAML config
├── mcu/
│   └── sensor_bridge/
│       └── sensor_bridge.ino # STM32 sketch: climate sensors, SD, LED status
├── linux/
│   ├── requirements.txt
│   └── uno_q_linux/          # Python package (run on board or laptop mock)
│       ├── __main__.py       # python -m uno_q_linux
│       ├── coordinator.py    # ONLINE/OFFLINE state machine
│       ├── connectivity.py   # host ping / Wi‑Fi check
│       ├── sensors.py        # Bridge RPC or mock generator
│       ├── sd_logger.py      # JSONL failover (+ optional backlog flush)
│       ├── host_client.py    # HTTP telemetry to laptop ground station
│       ├── apriltag_seek.py  # detect + seek/hold when offline
│       ├── bridge_rpc.py     # Arduino Bridge helpers / mock
│       └── nav_assist_stub.py
└── scripts/
    ├── run_mock.sh
    ├── demo_online.sh
    └── demo_offline.sh
```

Host receiver stub (laptop ground station): [`../ground_station/uno_q_receiver.py`](../ground_station/uno_q_receiver.py)

## Hardware Maxim must confirm

| Item | Assumed default (verify!) | Why |
| --- | --- | --- |
| **Temp/humidity** | BME280 on I2C (`0x76`/`0x77`) — DHT22 fallback pins documented in sketch | Climate telemetry |
| **SD module** | SPI microSD (CS pin in sketch / config) | Offline logger |
| **Camera** | USB UVC webcam on Uno Q Linux **or** mock image folder | AprilTag seek needs frames |
| **Host Wi‑Fi** | Laptop hotspot / LAN IP; Uno Q joins same SSID | Connectivity watchdog |
| **Printed AprilTags** | Family `36h11` (configurable); waypoint IDs in config | Survey markers |
| **LEDs** | Built-in / GPIO status LED | ONLINE vs OFFLINE at a glance |

Pins in `sensor_bridge.ino` are **placeholders** — comment blocks mark what to verify on the physical board / shield.

## Config

Copy and edit:

```bash
cp uno_q/config.example.env uno_q/config.env
# or
cp uno_q/config.example.yaml uno_q/config.yaml
```

Important knobs:

| Variable | Meaning |
| --- | --- |
| `UNO_Q_HOST_URL` | e.g. `http://192.168.4.2:8787/telemetry` |
| `UNO_Q_HOST_IP` | IP used for reachability probe |
| `UNO_Q_WIFI_SSID` | Expected SSID (informational / optional check) |
| `UNO_Q_MOCK=1` | Laptop/CI mode: fake sensors, fake frames, local SD path |
| `UNO_Q_FORCE_OFFLINE=1` | Demo offline path without killing Wi‑Fi |
| `UNO_Q_APRILTAG_FAMILY` | `36h11` (default), `16h5`, … |
| `UNO_Q_APRILTAG_IDS` | Comma list of waypoint tag IDs, e.g. `0,1,2` |
| `UNO_Q_SD_DIR` | Log directory (real `/mnt/sd` or mock `./.uno_q_sd`) |
| `UNO_Q_CAMERA` | `0` (V4L index), path, or `mock` |

## Flash / run on Uno Q

### MCU (climate sensors + SD)

1. Open `mcu/sensor_bridge/sensor_bridge.ino` in Arduino IDE / App Lab for Uno Q.
2. Select the Uno Q board; install Adafruit BME280 (or DHT) + SD libs as needed.
3. Confirm I2C and SPI CS pins against your wiring, then upload.
4. Bridge RPC method names must match `linux/uno_q_linux/bridge_rpc.py` (`readSensors`, `sdWriteLine`, `setLedMode`).

### Linux (coordinator)

```bash
# On the board (or laptop with MOCK=1)
cd uno_q/linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# From repo root so imports resolve cleanly:
cd ../..
export $(grep -v '^#' uno_q/config.env | xargs)   # or source a dotenv helper
python3 -m uno_q_linux --config uno_q/config.env
```

`PYTHONPATH` must include `uno_q/linux` (scripts set this). The package reuses `ground_station.apriltag` and `ground_station.control` when available.

### Host laptop ground station receiver

```bash
# Terminal A — laptop
python3 -m ground_station.uno_q_receiver --port 8787

# Terminal B — board or mock
./uno_q/scripts/demo_online.sh
```

## Demo: online vs offline

```bash
# Fake hardware, force ONLINE (host must be listening or samples fall back to SD)
./uno_q/scripts/demo_online.sh

# Fake hardware, force OFFLINE → SD log + AprilTag seek on synthetic frames
./uno_q/scripts/demo_offline.sh

# Generic mock (state follows real host reachability, or FORCE_*)
./uno_q/scripts/run_mock.sh
```

**Expected:**

- **Online:** JSON climate samples POST to ground station; LED mode `online`; no seek commands (or idle).
- **Offline:** append-only JSONL under `UNO_Q_SD_DIR`; AprilTag seek prints yaw/fwd hold commands toward waypoint tags; LED mode `offline`.
- **Reconnect:** coordinator flips back to ONLINE and prefers host relay; set `UNO_Q_FLUSH_BACKLOG=1` to POST queued SD lines after reconnect (best-effort).

## State machine

```
BOOT → probe host
  ├─ reachable  → ONLINE  → read climate sensors → POST ground station → sleep
  └─ unreachable → OFFLINE → read climate sensors → SD append
                              └─ AprilTag detect → seek/hold toward waypoint
  (every cycle re-probe; hysteresis via UNO_Q_OFFLINE_GRACE_S)
```

## CI / laptop without board

`UNO_Q_MOCK=1` enables:

- Synthetic temp/humidity (+ extensible fields)
- Local filesystem “SD”
- Synthetic frames with a drawn square standing in for a tag **or** load images from `UNO_Q_MOCK_FRAMES_DIR`
- No Arduino Bridge dependency

```bash
PYTHONPATH=uno_q/linux:. UNO_Q_MOCK=1 python3 -m uno_q_linux --once
```

## Wiring sketch (verify!)

```
BME280  VIN→3V3  GND→GND  SCL→SCL  SDA→SDA
microSD VIN→5V/3V3(module)  GND→GND  MOSI/MISO/SCK/CS→SPI (CS = pin in sketch)
USB cam → Uno Q USB-C hub (Linux UVC)
Printed AprilTags → field / table as waypoints
Uno Q Wi‑Fi → same AP as laptop (HOST_IP = laptop LAN address)
```
