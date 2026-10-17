"""Load config from env vars and optional .env / .yaml file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return [0]
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out or [0]


@dataclass
class Config:
    host_ip: str = "127.0.0.1"
    host_url: str = "http://127.0.0.1:8787/telemetry"
    host_port: int = 8787
    wifi_ssid: str = ""
    mock: bool = False
    force_offline: bool = False
    force_online: bool = False
    flush_backlog: bool = False
    offline_grace_s: float = 3.0
    sample_hz: float = 1.0
    sensor_backend: str = "bridge"  # bridge | mock
    i2c_addr: str = "0x76"
    sd_dir: str = "/mnt/sd/agridrone"
    apriltag_family: str = "36h11"
    apriltag_ids: list[int] = field(default_factory=lambda: [0, 1, 2])
    camera: str = "0"
    mock_frames_dir: str | None = None
    seek_deadband: float = 0.08
    seek_target_area: float = 0.12
    nav_assist: bool = False

    @property
    def sample_period_s(self) -> float:
        return 1.0 / max(self.sample_hz, 0.1)


def _apply_env(cfg: Config) -> Config:
    e = os.environ
    if "UNO_Q_HOST_IP" in e:
        cfg.host_ip = e["UNO_Q_HOST_IP"].strip()
    if "UNO_Q_HOST_URL" in e:
        cfg.host_url = e["UNO_Q_HOST_URL"].strip()
    if "UNO_Q_HOST_PORT" in e and e["UNO_Q_HOST_PORT"].strip():
        cfg.host_port = int(e["UNO_Q_HOST_PORT"])
    if "UNO_Q_WIFI_SSID" in e:
        cfg.wifi_ssid = e["UNO_Q_WIFI_SSID"].strip()
    cfg.mock = _truthy(e.get("UNO_Q_MOCK"), cfg.mock)
    cfg.force_offline = _truthy(e.get("UNO_Q_FORCE_OFFLINE"), cfg.force_offline)
    cfg.force_online = _truthy(e.get("UNO_Q_FORCE_ONLINE"), cfg.force_online)
    cfg.flush_backlog = _truthy(e.get("UNO_Q_FLUSH_BACKLOG"), cfg.flush_backlog)
    if e.get("UNO_Q_OFFLINE_GRACE_S"):
        cfg.offline_grace_s = float(e["UNO_Q_OFFLINE_GRACE_S"])
    if e.get("UNO_Q_SAMPLE_HZ"):
        cfg.sample_hz = float(e["UNO_Q_SAMPLE_HZ"])
    if e.get("UNO_Q_SENSOR_BACKEND"):
        cfg.sensor_backend = e["UNO_Q_SENSOR_BACKEND"].strip()
    if e.get("UNO_Q_I2C_ADDR"):
        cfg.i2c_addr = e["UNO_Q_I2C_ADDR"].strip()
    if e.get("UNO_Q_SD_DIR"):
        cfg.sd_dir = e["UNO_Q_SD_DIR"].strip()
    if e.get("UNO_Q_APRILTAG_FAMILY"):
        cfg.apriltag_family = e["UNO_Q_APRILTAG_FAMILY"].strip()
    if e.get("UNO_Q_APRILTAG_IDS"):
        cfg.apriltag_ids = _parse_ids(e["UNO_Q_APRILTAG_IDS"])
    if e.get("UNO_Q_CAMERA"):
        cfg.camera = e["UNO_Q_CAMERA"].strip()
    if e.get("UNO_Q_MOCK_FRAMES_DIR"):
        cfg.mock_frames_dir = e["UNO_Q_MOCK_FRAMES_DIR"].strip() or None
    if e.get("UNO_Q_SEEK_DEADBAND"):
        cfg.seek_deadband = float(e["UNO_Q_SEEK_DEADBAND"])
    if e.get("UNO_Q_SEEK_TARGET_AREA"):
        cfg.seek_target_area = float(e["UNO_Q_SEEK_TARGET_AREA"])
    cfg.nav_assist = _truthy(e.get("UNO_Q_NAV_ASSIST"), cfg.nav_assist)
    if cfg.mock and cfg.sd_dir.startswith("/mnt/"):
        cfg.sd_dir = str(Path(".uno_q_sd").resolve())
    if cfg.mock and cfg.sensor_backend == "bridge":
        cfg.sensor_backend = "mock"
    if cfg.mock and cfg.camera not in {"mock", ""} and not Path(cfg.camera).exists():
        # numeric V4L index often missing on CI — default mock frames
        if cfg.camera.isdigit():
            cfg.camera = "mock"
    return cfg


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(name, value)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(f"PyYAML required for {path}: pip install PyYAML") from exc
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"config root must be a mapping: {path}")
    return data


def _apply_yaml(cfg: Config, data: dict[str, Any]) -> Config:
    mapping = {
        "host_ip": "host_ip",
        "host_url": "host_url",
        "host_port": "host_port",
        "wifi_ssid": "wifi_ssid",
        "mock": "mock",
        "force_offline": "force_offline",
        "force_online": "force_online",
        "flush_backlog": "flush_backlog",
        "offline_grace_s": "offline_grace_s",
        "sample_hz": "sample_hz",
        "sensor_backend": "sensor_backend",
        "i2c_addr": "i2c_addr",
        "sd_dir": "sd_dir",
        "apriltag_family": "apriltag_family",
        "apriltag_ids": "apriltag_ids",
        "camera": "camera",
        "mock_frames_dir": "mock_frames_dir",
        "seek_deadband": "seek_deadband",
        "seek_target_area": "seek_target_area",
        "nav_assist": "nav_assist",
    }
    for key, attr in mapping.items():
        if key in data and data[key] is not None:
            setattr(cfg, attr, data[key])
    if isinstance(cfg.apriltag_ids, str):
        cfg.apriltag_ids = _parse_ids(cfg.apriltag_ids)
    elif isinstance(cfg.apriltag_ids, list):
        cfg.apriltag_ids = [int(x) for x in cfg.apriltag_ids]
    cfg.camera = str(cfg.camera)
    cfg.i2c_addr = str(cfg.i2c_addr)
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path:
        p = Path(path)
        if p.suffix.lower() in {".yaml", ".yml"}:
            cfg = _apply_yaml(cfg, _load_yaml(p))
        else:
            _load_dotenv(p)
    else:
        # Convenience: repo-relative defaults if present
        for candidate in (
            Path("uno_q/config.env"),
            Path("uno_q/config.yaml"),
            Path("config.env"),
        ):
            if candidate.is_file():
                if candidate.suffix.lower() in {".yaml", ".yml"}:
                    cfg = _apply_yaml(cfg, _load_yaml(candidate))
                else:
                    _load_dotenv(candidate)
                break
    return _apply_env(cfg)
