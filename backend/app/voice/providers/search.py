"""Web search tools: real (Tavily) + deterministic mock (tests/CI).

Assignment note: "web-search query" is a valid one-tool case; kept deliberately
simple — no MCP, no extra infra. The real adapter requires TAVILY_API_KEY.
"""
from __future__ import annotations

import asyncio

import httpx

from .base import ToolResult, HttpTimeout

_TIMEOUT = HttpTimeout.default


class MockSearchTool:
    name = "web_search"
    description = "Mock web search (deterministic; tests/CI)."

    async def run(self, query: str) -> ToolResult:
        await asyncio.sleep(0.08)
        return ToolResult({
            "query": query,
            "results": [
                {"title": "Result 1 (mock)", "snippet": f"Deterministic snippet for: {query[:60]}"},
                {"title": "Result 2 (mock)", "snippet": "Second deterministic snippet."},
            ],
        })


class TavilySearchTool:
    name = "web_search"
    description = "Search the web for current information about a topic."

    async def run(self, query: str) -> ToolResult:
        from ...core.config import get_settings

        s = get_settings()
        if not s.tavily_api_key:
            return ToolResult({"query": query, "error": "tavily_key_missing"})
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": s.tavily_api_key, "query": query, "max_results": 3},
            )
            resp.raise_for_status()
            data = resp.json()
        return ToolResult({
            "query": query,
            "results": [{"title": r.get("title", ""), "snippet": r.get("content", "")[:200]}
                        for r in data.get("results", [])],
        })
