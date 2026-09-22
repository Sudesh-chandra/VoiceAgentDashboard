"""Open-Meteo weather tool — adapted from langchain-ai/voice-demo (weather.py).

Keyless, deterministic enough for testing, structured result, clear errors.
Fixed endpoint constants; the only caller-supplied value is the city name passed
as a query parameter, so there is no user-controlled base URL (SSRF-safe).
"""
from __future__ import annotations

import httpx

from .base import ToolResult, HttpTimeout

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = HttpTimeout.default


async def fetch_weather(city: str) -> dict:
    """Geocode a city name and return current weather.

    Returns one of:
      {"city": str, "country": str, "weather": {...}}        on success
      {"city": str, "error": "not_found" | "http_error"}     on failure
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            geo = await client.get(GEOCODE_URL, params={"name": city, "count": 1})
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                return {"city": city, "error": "not_found"}
            loc = results[0]
            wx = await client.get(
                FORECAST_URL,
                params={
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                    "current_weather": True,
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                },
            )
            wx.raise_for_status()
            current = wx.json().get("current_weather") or {}
            return {
                "city": loc.get("name") or city,
                "country": loc.get("country") or "",
                "weather": current,
            }
        except httpx.HTTPError:
            return {"city": city, "error": "http_error"}


class WeatherTool:
    name = "lookup_weather"
    description = "Get the current weather for a single city. Call once per city."

    async def run(self, city: str) -> ToolResult:
        return ToolResult(await fetch_weather(city))
