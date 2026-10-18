"""Live NOAA and NASA readings for one field point.

Fetched at call time. No dataset is stored in the repo.
Open-Meteo data is CC BY 4.0. NWS data is public domain.
NASA POWER ALLSKY_SFC_SW_DWN is satellite-derived surface sunlight.

Called from chalk/features.py (the online resolver), from
ground_station/chalk_eval_mission.py when the deployed Chalk query has no
weather outputs yet, and from ground_station/weather_smoke.py.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

TREEHOUSE_LAT = 42.3635
TREEHOUSE_LON = -71.1253
TREEHOUSE_NAME = "David Rubenstein Treehouse"
YARD_LAT = 42.3744
YARD_LON = -71.1169
YARD_NAME = "Harvard Yard"

USER_AGENT = "AgriDrone HackHarvard 2026 (field survey)"
_STORM = re.compile(r"thunder|heavy rain|tstm|tornado|hail|snowstorm|\bstorm\b", re.I)
Getter = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class WeatherReading:
    location_name: str
    latitude: float
    longitude: float
    used_fallback: bool
    nws_temperature_c: float | None
    nws_wind_mph: float | None
    nws_forecast: str | None
    nws_observed_at: str | None
    gfs_precip_mm: float | None
    gfs_cloud_cover_pct: float | None
    gfs_shortwave_wm2: float | None
    nasa_shortwave_wm2: float | None
    nasa_observed_at: str | None
    weather_error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "location_name": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "used_fallback": self.used_fallback,
            "nws_temperature_c": self.nws_temperature_c,
            "nws_wind_mph": self.nws_wind_mph,
            "nws_forecast": self.nws_forecast,
            "nws_observed_at": self.nws_observed_at,
            "gfs_precip_mm": self.gfs_precip_mm,
            "gfs_cloud_cover_pct": self.gfs_cloud_cover_pct,
            "gfs_shortwave_wm2": self.gfs_shortwave_wm2,
            "nasa_shortwave_wm2": self.nasa_shortwave_wm2,
            "nasa_observed_at": self.nasa_observed_at,
            "weather_error": self.weather_error,
        }


@dataclass(frozen=True)
class JustifiedInsight:
    insight: str
    justified_action: str
    image_action: str
    reading: WeatherReading


def image_flag(score: float) -> str:
    if score >= 0.7:
        return "healthy"
    if score >= 0.4:
        return "watch"
    return "critical"


def image_action(score: float) -> str:
    return {"healthy": "continue", "watch": "reimage", "critical": "alert-operator"}[image_flag(score)]


def fetch_weather(
    latitude: float = TREEHOUSE_LAT,
    longitude: float = TREEHOUSE_LON,
    *,
    getter: Getter | None = None,
    timeout: float = 12.0,
) -> WeatherReading:
    """GET NWS, NOAA GFS, and NASA POWER for one point."""
    get = getter or _getter(timeout)
    reading = _fetch_at(latitude, longitude, TREEHOUSE_NAME, False, get)
    points_failed = reading.weather_error is not None and "nws points" in reading.weather_error
    if points_failed and not _same_point(latitude, longitude, YARD_LAT, YARD_LON):
        yard = _fetch_at(YARD_LAT, YARD_LON, YARD_NAME, True, get)
        note = "Treehouse grid was unavailable; reading is Harvard Yard"
        yard_error = "; ".join(part for part in (note, yard.weather_error) if part)
        return WeatherReading(
            location_name=yard.location_name,
            latitude=yard.latitude,
            longitude=yard.longitude,
            used_fallback=True,
            nws_temperature_c=yard.nws_temperature_c,
            nws_wind_mph=yard.nws_wind_mph,
            nws_forecast=yard.nws_forecast,
            nws_observed_at=yard.nws_observed_at,
            gfs_precip_mm=yard.gfs_precip_mm,
            gfs_cloud_cover_pct=yard.gfs_cloud_cover_pct,
            gfs_shortwave_wm2=yard.gfs_shortwave_wm2,
            nasa_shortwave_wm2=yard.nasa_shortwave_wm2,
            nasa_observed_at=yard.nasa_observed_at,
            weather_error=yard_error or None,
        )
    return reading


def justify(health_score: float, analysis_summary: str, reading: WeatherReading) -> JustifiedInsight:
    """Explain the image score with the live reading. Does not change image-only labels."""
    action = image_action(health_score)
    flag = image_flag(health_score)
    finding = _finding(health_score, flag, analysis_summary)
    if _all_failed(reading):
        text = f"{finding} Live weather was unavailable, so the image stands alone."
        return JustifiedInsight(text, action, action, reading)

    shortwave = reading.gfs_shortwave_wm2
    if shortwave is None:
        shortwave = reading.nasa_shortwave_wm2
    dark = (
        reading.gfs_cloud_cover_pct is not None and reading.gfs_cloud_cover_pct >= 80
    ) or (shortwave is not None and shortwave < 50)
    bright_dry = (
        reading.gfs_precip_mm is not None
        and reading.gfs_precip_mm < 0.2
        and shortwave is not None
        and shortwave >= 200
    )
    storm = _is_storm(reading.nws_forecast) or (
        reading.nws_wind_mph is not None and reading.nws_wind_mph >= 20
    )
    place = reading.location_name
    cite = _cite(reading)

    if action == "alert-operator" and storm:
        why = f"Weather over {place} confounds that frame ({cite}), so this is not a clean canopy alert."
        return JustifiedInsight(f"{finding} {why}", "reimage", action, reading)
    if action == "continue" and dark:
        why = f"Light at {place} is too poor to trust a healthy call ({cite})."
        return JustifiedInsight(f"{finding} {why}", "reimage", action, reading)
    if flag == "watch" and dark:
        why = f"Light at {place} is too poor to separate crop stress from the sky ({cite})."
        return JustifiedInsight(f"{finding} {why}", "reimage", action, reading)
    if action in ("alert-operator", "reimage") and bright_dry:
        why = f"Live sun and dry air at {place} support the stress in the photo ({cite})."
        return JustifiedInsight(f"{finding} {why}", action, action, reading)
    if action == "continue":
        why = f"The NOAA observation at {place} does not contradict a healthy canopy ({cite})."
        return JustifiedInsight(f"{finding} {why}", "continue", action, reading)
    why = f"Live conditions at {place} do not override the image ({cite})."
    return JustifiedInsight(f"{finding} {why}", action, action, reading)


def _fetch_at(lat: float, lon: float, name: str, fallback: bool, get: Getter) -> WeatherReading:
    errors: list[str] = []
    nws = _nws(lat, lon, get, errors)
    gfs = _gfs(lat, lon, get, errors)
    nasa = _nasa(lat, lon, get, errors)
    return WeatherReading(
        location_name=name,
        latitude=lat,
        longitude=lon,
        used_fallback=fallback,
        nws_temperature_c=nws.get("temp"),
        nws_wind_mph=nws.get("wind"),
        nws_forecast=nws.get("forecast"),
        nws_observed_at=nws.get("observed_at"),
        gfs_precip_mm=gfs.get("precip"),
        gfs_cloud_cover_pct=gfs.get("cloud"),
        gfs_shortwave_wm2=gfs.get("shortwave"),
        nasa_shortwave_wm2=nasa.get("shortwave"),
        nasa_observed_at=nasa.get("observed_at"),
        weather_error="; ".join(errors) or None,
    )


def _nws(lat: float, lon: float, get: Getter, errors: list[str]) -> dict[str, Any]:
    try:
        points = get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
        props = points["properties"]
    except Exception as exc:
        errors.append(f"nws points: {exc}")
        return {}
    try:
        forecast = get(props["forecast"])
        period = forecast["properties"]["periods"][0]
        stations = get(props["observationStations"])
        features = stations.get("features") or []
        observed_at = None
        temp = None
        wind = None
        if features:
            station = str(features[0]["id"]).rstrip("/")
            obs = get(f"{station}/observations/latest")["properties"]
            temp = _quantity(obs.get("temperature"), "degC")
            wind = _wind_mph(obs.get("windSpeed"))
            observed_at = obs.get("timestamp")
        return {
            "temp": temp,
            "wind": wind,
            "forecast": period.get("shortForecast"),
            "observed_at": observed_at,
        }
    except Exception as exc:
        errors.append(f"nws detail: {exc}")
        return {}


def _gfs(lat: float, lon: float, get: Getter, errors: list[str]) -> dict[str, Any]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=precipitation,cloud_cover,shortwave_radiation"
        "&models=gfs_seamless&precipitation_unit=mm&timezone=UTC"
    )
    try:
        current = get(url)["current"]
        return {
            "precip": _num(current.get("precipitation")),
            "cloud": _num(current.get("cloud_cover")),
            "shortwave": _num(current.get("shortwave_radiation")),
        }
    except Exception as exc:
        errors.append(f"open-meteo gfs: {exc}")
        return {}


def _nasa(lat: float, lon: float, get: Getter, errors: list[str]) -> dict[str, Any]:
    # ALLSKY hourly was fill-only (-999) for the prior two weeks on 2026-10-17.
    # The newest real hour was 2026-07-31, so the lookback has to cover that lag.
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=120)
    url = (
        "https://power.larc.nasa.gov/api/temporal/hourly/point"
        "?parameters=ALLSKY_SFC_SW_DWN&community=AG"
        f"&longitude={lon}&latitude={lat}&format=JSON"
        f"&start={start:%Y%m%d}&end={end:%Y%m%d}&time-standard=UTC"
    )
    try:
        series = get(url)["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
        valid = {key: float(value) for key, value in series.items() if _usable_power(value)}
        if not valid:
            errors.append("nasa power: no recent hour")
            return {}
        peak = max(valid.values())
        scale = 1000.0 if 0 < peak < 5 else 1.0
        stamp = max(valid)
        return {"shortwave": round(valid[stamp] * scale, 1), "observed_at": _power_stamp(stamp)}
    except Exception as exc:
        errors.append(f"nasa power: {exc}")
        return {}


def _getter(timeout: float) -> Getter:
    def get(url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:180]
            raise RuntimeError(f"HTTP {exc.code} {detail}") from exc
        if not isinstance(body, dict):
            raise RuntimeError("response was not an object")
        return body

    return get


def _quantity(node: Any, unit: str) -> float | None:
    if not isinstance(node, dict):
        return None
    value = _num(node.get("value"))
    if value is None:
        return None
    code = str(node.get("unitCode") or "")
    if unit == "degC" and code.endswith("degF"):
        return round((value - 32) * 5 / 9, 1)
    return round(value, 1)


def _wind_mph(node: Any) -> float | None:
    if not isinstance(node, dict):
        return None
    value = _num(node.get("value"))
    if value is None:
        return None
    code = str(node.get("unitCode") or "")
    if code.endswith("m_s-1"):
        value *= 2.23694
    elif code.endswith("km_h-1") or code == "":
        value *= 0.621371
    return round(value, 1)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _usable_power(value: Any) -> bool:
    number = _num(value)
    return number is not None and number != -999.0


def _power_stamp(stamp: str) -> str:
    if len(stamp) == 10 and stamp.isdigit():
        return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:00Z"
    return stamp


def _same_point(lat: float, lon: float, other_lat: float, other_lon: float) -> bool:
    return abs(lat - other_lat) < 0.0001 and abs(lon - other_lon) < 0.0001


def _all_failed(reading: WeatherReading) -> bool:
    return all(
        value is None
        for value in (
            reading.nws_forecast,
            reading.nws_temperature_c,
            reading.gfs_precip_mm,
            reading.gfs_cloud_cover_pct,
            reading.gfs_shortwave_wm2,
            reading.nasa_shortwave_wm2,
        )
    )


def _is_storm(forecast: str | None) -> bool:
    return bool(forecast and _STORM.search(forecast))


def _finding(score: float, flag: str, summary: str) -> str:
    text = " ".join(summary.split())
    if text:
        if text[-1] not in ".!?":
            text += "."
        return text[:280]
    return f"The image health score is {score:.2f} ({flag})."


def _cite(reading: WeatherReading) -> str:
    parts: list[str] = []
    if reading.nws_forecast:
        parts.append(reading.nws_forecast)
    if reading.nws_wind_mph is not None:
        parts.append(f"wind {reading.nws_wind_mph:.0f} mph")
    if reading.gfs_precip_mm is not None:
        parts.append(f"precip {reading.gfs_precip_mm:.2f} mm")
    if reading.gfs_cloud_cover_pct is not None:
        parts.append(f"cloud {reading.gfs_cloud_cover_pct:.0f}%")
    if reading.gfs_shortwave_wm2 is not None:
        parts.append(f"GFS sun {reading.gfs_shortwave_wm2:.0f} W/m2")
    if reading.nasa_shortwave_wm2 is not None:
        when = reading.nasa_observed_at or "lagged"
        parts.append(f"NASA sun {reading.nasa_shortwave_wm2:.0f} W/m2 at {when}")
    if reading.nws_observed_at:
        parts.append(f"NWS {reading.nws_observed_at}")
    return ", ".join(parts) if parts else "partial weather"
