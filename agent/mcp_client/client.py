"""
mcp_client/client.py — Thin async wrapper around the Alpaca MCP server.

Connects via HTTP+SSE transport (MCP 1.x) and exposes a single
``call_tool(name, arguments)`` coroutine used by all higher-level wrappers.

Fall-back behaviour: if the MCP server is unreachable, ``call_tool`` raises
``MCPUnavailableError`` so callers can fall back to alpaca-py directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SESSION_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "daha-agent", "version": "0.1.0"},
    },
}


class MCPUnavailableError(RuntimeError):
    """Raised when the MCP server cannot be reached."""


class MCPClient:
    """
    Async MCP client for the Alpaca MCP server.

    Usage::

        async with MCPClient(url="http://localhost:3001/mcp") as client:
            result = await client.call_tool("get_account", {})
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self._url = url
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._initialized = False
        self._call_id = 2  # 1 is used for initialize

    async def __aenter__(self) -> "MCPClient":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        await self._initialize()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def _initialize(self) -> None:
        """Send MCP initialize handshake."""
        assert self._http is not None
        try:
            resp = await self._http.post(
                self._url,
                json=_SESSION_INIT_PAYLOAD,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            self._initialized = True
            logger.debug("MCP session initialized at %s", self._url)
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise MCPUnavailableError(
                f"Cannot reach MCP server at {self._url}: {exc}"
            ) from exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """
        Call an MCP tool by name and return the parsed result content.

        Raises ``MCPUnavailableError`` if the server is unreachable,
        ``MCPToolError`` if the server returns an error result.
        """
        if not self._initialized or self._http is None:
            raise MCPUnavailableError("MCP client not initialized. Use as async context manager.")

        payload = {
            "jsonrpc": "2.0",
            "id": self._call_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        self._call_id += 1

        try:
            resp = await self._http.post(
                self._url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise MCPUnavailableError(f"MCP call '{name}' failed: {exc}") from exc

        body = resp.json()
        if "error" in body:
            raise MCPToolError(name, body["error"])

        result = body.get("result", {})
        # MCP tool results come back as a list of content items
        contents = result.get("content", [])
        if contents and contents[0].get("type") == "text":
            try:
                return json.loads(contents[0]["text"])
            except json.JSONDecodeError:
                return contents[0]["text"]
        return result


class MCPToolError(RuntimeError):
    """Raised when the MCP server returns a tool-level error."""

    def __init__(self, tool_name: str, error: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.error = error
        super().__init__(f"MCP tool '{tool_name}' error: {error}")
