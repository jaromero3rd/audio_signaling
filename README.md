# 🌾 AgriDrone: AI-Powered Field Survey Agent

**Autonomous crop health monitoring with embedded Droid intelligence.** Fly drones, analyze photos with Grok vision, issue voice commands via Grok Voice API, receive real-time mobile alerts on Photon 2.0, and let an embedded Factory Droid decide the next action.

**HackHarvard 2026** · Tracks: [SpaceXAI](#tracks) | Factory Droid SDK | Photon 2.0 | Daytona | Chalk | ASUS Sustainability

---

## 🎯 Tracks & Technology

| Track | Role | Status |
|-------|------|--------|
| **SpaceXAI** | Grok Voice API + Grok Bot coordination | P0 |
| **Factory Droid SDK** | Embedded agent session making decisions at runtime | P0 |
| **Photon 2.0** | Real-time operator alerts + text commands | P0 |
| **Daytona** | Sandbox execution for Droid-generated scripts | P1 |
| **Chalk** | Survey results and agent decisions as features | P1 |
| **ASUS Sustainability** | Crop monitoring for agricultural sustainability | P1 |

---

## 🏗️ Architecture

```
User (voice/text)
    ├─ Grok Voice API (voice commands)
    └─ Photon 2.0 (mobile alerts + text)
              ↓
        Ground Station
              ↓
        Drone captures photo
              ↓
    Grok Image Understanding
    (crop health analysis)
              ↓
    Factory Droid Session (embedded)
    [Tools: drone_command, photon_alert, chalk_query]
              ↓
        Decision & Actions
    ├─ Issue new flight commands
    ├─ Alert operator with results
    └─ Write features to Chalk
```

### How Droid is Used at Runtime

1. **Session Startup**: Ground station launches a persistent Factory Droid session on boot.
2. **Vision Input**: Each drone photo → Grok analysis → structured crop-health JSON sent to Droid.
3. **Tool Calls**: Droid has access to:
   - `issue_drone_command(drone_id, waypoints, action)` — command next flight
   - `send_photon_alert(message, photo_url)` — notify operator
   - `query_chalk_features(field_id)` — fetch historical field data
4. **Decision Loop**: Droid reads analysis + mission state, decides whether to:
   - Survey a new area
   - Return for recharge
   - Alert the operator
   - Write results to Chalk for long-term trend analysis
5. **Execution**: Commands execute in Daytona sandbox; results loop back to the session.

See [PRD](prd.md) for full flow diagram and requirements.

---

## 🚀 Quick Start

### Prerequisites
- **Credentials**: xAI API key, Photon 2.0 project, Factory Droid authentication, Daytona API key, Chalk service tokens. [Full setup checklist →](SETUP.md)
- **Hardware** (optional): DJI or compatible drone with camera + radio link, or test with sample images.

### Installation

```bash
# Clone and install dependencies
git clone <repo>
cd audio_signaling
pip install -r requirements.txt

# Verify credentials (see SETUP.md for details)
python3 - <<'PY'
import os
from pathlib import Path
values = {}
path = Path(".env")
if path.is_file():
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip("\"'")
values.update(os.environ)
services = {"xAI": ("XAI_API_KEY",), "Photon": ("PHOTON_API_KEY",), 
            "Daytona": ("DAYTONA_API_KEY",), "Chalk": ("CHALK_CLIENT_ID", "CHALK_CLIENT_SECRET")}
for service, names in services.items():
    status = "✓" if all(values.get(name, "").strip() for name in names) else "✗"
    print(f"{status} {service}")
PY
```

### Run the Ground Station

```bash
python ground_station/agent/session.py
```

This starts the embedded Droid session. Feed it drone photos and watch it decide actions.

---

## 🌾 Wheat Head Detection (Optional)

YOLOv8s detector fine-tuned on [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021).

```bash
# Download dataset
python wheat_detection/download_dataset.py

# Train
python wheat_detection/train.py

# Predict on a field image
python wheat_detection/predict.py path/to/field.png
```

Checkpoint: `models/wheat_detection.pt` (training) and `wheat_detection.onnx` (inference, 1024px).

---

## 📁 Repository Structure

```
audio_signaling/
├── README.md                    # This file
├── prd.md                       # Full product requirements
├── SETUP.md                     # Credential & infrastructure checklist
│
├── ground_station/
│   ├── vision/                  # Grok image understanding
│   ├── agent/                   # Droid embedded session & tools
│   ├── voice/                   # Grok Voice API integration
│   ├── messaging/               # Photon 2.0 alerting
│   └── sandbox/                 # Daytona execution wrapper
│
├── wheat_detection/             # YOLOv8s crop health detector
│   ├── train.py
│   ├── predict.py
│   ├── download_dataset.py
│   ├── requirements.txt
│   └── models/
│
├── uno_q/                       # Arduino Uno Q edge hub (sensors, SD failover, offline AprilTag)
│   ├── mcu/sensor_bridge/       # STM32 sketch (Bridge RPC)
│   ├── linux/uno_q_linux/       # ONLINE host relay / OFFLINE SD + seek
│   └── README.md                # Wiring, flash/run, demo scripts
│
├── chalk/                       # Feature definitions & evals
├── requirements.txt             # Python dependencies
└── .env.example                 # Credential template
```

### Uno Q offline / online hub

See **[uno_q/README.md](uno_q/README.md)**. Quick mock (no board):

```bash
# Terminal A — laptop receiver
python3 -m ground_station.uno_q_receiver --port 8787

# Terminal B — mock ONLINE or OFFLINE
./uno_q/scripts/demo_online.sh
./uno_q/scripts/demo_offline.sh
```

---

## 🔧 Configuration

Create `.env` from `.env.example` with credentials from [SETUP.md](SETUP.md):

```bash
XAI_API_KEY=sk-...
PHOTON_API_KEY=...
PHOTON_PROJECT_ID=...
DAYTONA_API_KEY=...
CHALK_CLIENT_ID=...
CHALK_CLIENT_SECRET=...
CHALK_ENVIRONMENT=dev
```

---

## ✅ Success Criteria (Demo Flow)

1. ✓ Operator sends voice command: *"Survey the wheat field."*
2. ✓ Drone captures photo → Grok analyzes crop health.
3. ✓ Embedded Droid decides next action (survey more, alert, recharge).
4. ✓ Operator receives photo + analysis on Photon 2.0.
5. ✓ Results written to Chalk for trend analysis.

---

## 📚 Documentation

- **[PRD](prd.md)**: Full product & requirements breakdown.
- **[Setup Checklist](SETUP.md)**: Credential & infrastructure setup (read first).
- **[Grok Docs](https://docs.x.ai/)**: xAI API reference.
- **[Factory Droid](https://docs.factory.ai/)**: Embedded agent SDK.
- **[Photon 2.0](https://photon.codes/)**: Mobile messaging.
- **[Chalk](https://docs.chalk.ai/)**: Feature engineering platform.

---

## 👥 Team Workflow

- **Coding**: Factory Droid CLI inside Cursor.
- **Coordination**: Grok Bots (SpaceXAI track requirement).
- **Task Tracking**: [AGENTS.md](AGENTS.md) (auto-read by Droid CLI).

---

## 📝 License & Attribution

HackHarvard 2026 submission. Uses:
- [Global Wheat Head Dataset 2021](https://kaggle.com/datasets/vbookshelf/global-wheat-head-dataset-2021) (CC BY 4.0)
- YOLOv8 (Ultralytics, AGPL-3.0)

---

**Live demo, flight footage, and results coming soon.** 🚁✨
