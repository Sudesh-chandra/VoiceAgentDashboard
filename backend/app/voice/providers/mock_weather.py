"""Deterministic mock weather tool for tests/CI and provider-free demos.

Fixed table of cities; unknown city returns a structured not_found. Always
succeeds with a small artificial delay so latency boundaries are realistic.
"""
from __future__ import annotations

import asyncio

from .base import ToolResult


_TABLE = {
    "tokyo": {"temperature": 18.4, "wind_speed": 12.1, "weather_code": 3},
    "rome": {"temperature": 23.7, "wind_speed": 6.4, "weather_code": 0},
    "berlin": {"temperature": 15.2, "wind_speed": 9.8, "weather_code": 61},
    "new york": {"temperature": 11.0, "wind_speed": 14.2, "weather_code": 2},
    "london": {"temperature": 13.9, "wind_speed": 8.0, "weather_code": 61},
    "paris": {"temperature": 16.5, "wind_speed": 5.5, "weather_code": 1},
}


class MockWeatherTool:
    name = "lookup_weather_mock"
    description = "Mock current weather for a single city (deterministic; tests/CI)."

    async def run(self, city: str) -> ToolResult:
        await asyncio.sleep(0.08)  # realistic tool latency without real network
        key = (city or "").strip().lower()
        if key in _TABLE:
            return ToolResult({"city": city, "country": "Mockland", "weather": _TABLE[key]})
        return ToolResult({"city": city, "error": "not_found"})
