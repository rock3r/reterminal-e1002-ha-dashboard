from __future__ import annotations

from typing import Any

import requests


FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
    "surface_pressure,precipitation,wind_speed_10m,wind_direction_10m,weather_code,is_day,uv_index"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset"
    "&hourly=temperature_2m,precipitation_probability,weather_code,uv_index"
    "&forecast_days=7&timezone=auto"
)

AQI_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    "?latitude={lat}&longitude={lon}&hourly=european_aqi&timezone=auto"
)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _get_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected API response payload")
    return payload


def fetch_weather(lat: float, lon: float) -> dict[str, Any]:
    return _get_json(FORECAST_URL.format(lat=lat, lon=lon))


def fetch_aqi(lat: float, lon: float) -> dict[str, Any]:
    return _get_json(AQI_URL.format(lat=lat, lon=lon))


def geocode_location(name: str) -> dict[str, Any]:
    query = name.strip()
    if not query:
        raise RuntimeError("Location cannot be empty")

    response = requests.get(
        GEOCODING_URL,
        params={
            "name": query,
            "count": 1,
            "language": "en",
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected geocoding response payload")

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"No geocoding results for location: {query}")

    top = results[0]
    if not isinstance(top, dict):
        raise RuntimeError("Invalid geocoding result format")

    latitude = top.get("latitude")
    longitude = top.get("longitude")
    timezone_name = top.get("timezone")
    if latitude is None or longitude is None or not timezone_name:
        raise RuntimeError("Geocoding result missing latitude/longitude/timezone")

    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "timezone": str(timezone_name),
        "name": str(top.get("name") or query),
        "country": str(top.get("country") or ""),
    }
