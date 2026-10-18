"""Mocked live-weather justification. No network.

Run with: PYTHONPATH=. .venv/bin/python test/test_weather_live.py
No production module imports this file. test/test_chalk_eval.py covers only
the image labels, and no other test calls fetch_weather or justify.
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "chalk"))
import weather_live as live


FORECAST = "https://api.weather.gov/gridpoints/BOX/1,1/forecast"
STATIONS = "https://api.weather.gov/gridpoints/BOX/1,1/stations"
STATION = "https://api.weather.gov/stations/KBOS"


def _obs(temp_c=18.0, wind=8.0, unit="km_h-1", when="2026-10-17T18:00:00+00:00"):
    return {
        "properties": {
            "temperature": {"value": temp_c, "unitCode": "wmoUnit:degC"},
            "windSpeed": {"value": wind, "unitCode": f"wmoUnit:{unit}"},
            "timestamp": when,
        }
    }


def _points():
    return {"properties": {"forecast": FORECAST, "observationStations": STATIONS}}


def _forecast(text):
    return {"properties": {"periods": [{"shortForecast": text}]}}


def _stations():
    return {"features": [{"id": STATION}]}


def _gfs(precip=0.0, cloud=10, shortwave=500):
    return {"current": {"precipitation": precip, "cloud_cover": cloud, "shortwave_radiation": shortwave}}


def _nasa(hours=None):
    hours = hours or {"2026101715": 400}
    return {"properties": {"parameter": {"ALLSKY_SFC_SW_DWN": hours}}}


class Script:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(url)


def _sunny(**kwargs):
    routes = {
        "api.weather.gov/points/42.3635,-71.1253": _points(),
        FORECAST: _forecast("Sunny"),
        STATIONS: _stations(),
        "/observations/latest": _obs(),
        "api.open-meteo.com": _gfs(),
        "power.larc.nasa.gov": _nasa(),
    }
    routes.update(kwargs)
    return Script(routes)


class WeatherLiveTests(unittest.TestCase):
    def test_bright_dry_weather_supports_a_low_image_score(self):
        reading = live.fetch_weather(getter=_sunny())
        judged = live.justify(0.2, "Brown canopy in the frame", reading)

        self.assertEqual(judged.image_action, "alert-operator")
        self.assertEqual(judged.justified_action, "alert-operator")
        self.assertIn("support the stress", judged.insight)
        self.assertEqual(reading.location_name, live.TREEHOUSE_NAME)
        self.assertFalse(reading.used_fallback)

    def test_dark_sky_turns_a_healthy_image_into_reimage(self):
        getter = _sunny(**{"api.open-meteo.com": _gfs(precip=0.0, cloud=10, shortwave=12)})
        getter.routes["power.larc.nasa.gov"] = _nasa({"2026101715": 800})
        reading = live.fetch_weather(getter=getter)
        judged = live.justify(0.9, "Even green canopy", reading)

        self.assertEqual(judged.image_action, "continue")
        self.assertEqual(judged.justified_action, "reimage")
        self.assertIn("too poor", judged.insight)
        self.assertEqual(reading.gfs_shortwave_wm2, 12)

    def test_storm_stops_a_critical_image_from_being_a_clean_alert(self):
        getter = _sunny(**{FORECAST: _forecast("Thunderstorms and heavy rain")})
        reading = live.fetch_weather(getter=getter)
        judged = live.justify(0.2, "Dark leaves", reading)

        self.assertEqual(judged.image_action, "alert-operator")
        self.assertEqual(judged.justified_action, "reimage")
        self.assertIn("not a clean canopy alert", judged.insight)

    def test_all_sources_down_leaves_the_image_action_unchanged(self):
        def down(url):
            if "points/42.3635,-71.1253" in url:
                return _points()
            raise RuntimeError("offline")

        reading = live.fetch_weather(getter=down)
        judged = live.justify(0.2, "", reading)

        self.assertEqual(judged.justified_action, judged.image_action)
        self.assertEqual(judged.justified_action, "alert-operator")
        self.assertIn("stands alone", judged.insight)
        self.assertFalse(reading.used_fallback)

    def test_yard_fallback_only_when_the_points_lookup_fails(self):
        getter = _sunny()
        getter.routes["api.weather.gov/points/42.3635,-71.1253"] = RuntimeError("grid rejected")
        getter.routes["api.weather.gov/points/42.3744,-71.1169"] = _points()
        reading = live.fetch_weather(getter=getter)

        self.assertTrue(reading.used_fallback)
        self.assertEqual(reading.location_name, live.YARD_NAME)
        self.assertEqual(reading.latitude, live.YARD_LAT)
        self.assertIn("Treehouse grid was unavailable", reading.weather_error)
        self.assertTrue(any("latitude=42.3744" in url for url in getter.urls))

    def test_later_nws_failure_does_not_move_the_reading(self):
        getter = _sunny(**{FORECAST: RuntimeError("forecast down")})
        reading = live.fetch_weather(getter=getter)

        self.assertFalse(reading.used_fallback)
        self.assertEqual(reading.latitude, live.TREEHOUSE_LAT)
        self.assertIn("nws detail", reading.weather_error)
        self.assertNotIn("nws points", reading.weather_error)
        self.assertEqual(reading.gfs_shortwave_wm2, 500)
        judged = live.justify(0.2, "Brown canopy", reading)
        self.assertEqual(judged.image_action, "alert-operator")
        self.assertEqual(judged.justified_action, "alert-operator")

    def test_nasa_kilowatt_series_scales_to_watts(self):
        getter = _sunny(**{
            "power.larc.nasa.gov": _nasa({"2026101612": -999, "2026101615": 0.2, "2026101608": 0.4})
        })
        reading = live.fetch_weather(getter=getter)

        self.assertEqual(reading.nasa_shortwave_wm2, 200.0)
        self.assertEqual(reading.nasa_observed_at, "2026-10-16T15:00Z")

    def test_wind_converts_from_meters_per_second(self):
        getter = _sunny(**{"/observations/latest": _obs(wind=10, unit="m_s-1")})
        reading = live.fetch_weather(getter=getter)

        self.assertEqual(reading.nws_wind_mph, 22.4)


if __name__ == "__main__":
    unittest.main()
