"""Live NOAA and NASA pull for the David Rubenstein Treehouse.

No Chalk credentials. Prints the reading and two sample justifications.

Called as a command, not from flight code:

    PYTHONPATH=. .venv/bin/python -m ground_station.weather_smoke

chalk/weather_live.py names this module as that caller. No other file
fetches api.weather.gov, Open-Meteo, or NASA POWER.
"""
from __future__ import annotations

from ground_station.chalk_client import load_weather_live


def main() -> int:
    live = load_weather_live()
    reading = live.fetch_weather()
    print(
        f"location: {reading.location_name} "
        f"({reading.latitude}, {reading.longitude}) fallback={reading.used_fallback}"
    )
    print(
        f"NWS: {reading.nws_forecast or '(none)'} "
        f"temp_c={reading.nws_temperature_c} wind_mph={reading.nws_wind_mph} "
        f"at={reading.nws_observed_at}"
    )
    print(
        f"GFS: precip_mm={reading.gfs_precip_mm} "
        f"cloud_pct={reading.gfs_cloud_cover_pct} "
        f"shortwave_wm2={reading.gfs_shortwave_wm2}"
    )
    if reading.nasa_shortwave_wm2 is None:
        print(f"NASA: no shortwave ({reading.weather_error or 'lag or missing hour'})")
    else:
        print(f"NASA: shortwave_wm2={reading.nasa_shortwave_wm2} at={reading.nasa_observed_at}")
    if reading.weather_error:
        print(f"weather_error: {reading.weather_error}")

    for score in (0.2, 0.9):
        judged = live.justify(score, f"sample image score {score:.1f}", reading)
        print(
            f"score {score:.1f}: justified_action={judged.justified_action} "
            f"image_action={judged.image_action}"
        )
        print(judged.insight)

    gfs_present = any(
        value is not None
        for value in (reading.gfs_precip_mm, reading.gfs_cloud_cover_pct, reading.gfs_shortwave_wm2)
    )
    if not reading.nws_forecast or not gfs_present:
        print("smoke incomplete: need NWS forecast text and one GFS value")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
