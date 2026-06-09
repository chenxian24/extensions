"""Core Toolkit — spawns core_toolkit MCP server and bridges its tools into agentcore."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext

logger = logging.getLogger(__name__)

_SERVER_SCRIPT = Path(__file__).parent / "mcp_servers" / "core_toolkit_server.py"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MCPBridge:
    """Bridges a TCP MCP server process into agentcore's ToolRegistry."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._req_id = 0
        self._tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        port = _find_free_port()
        logger.info("Starting MCP server on port %d", port)

        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_SERVER_SCRIPT), "--port", str(port),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for server to start with retry loop
        deadline = asyncio.get_event_loop().time() + 10.0
        last_err = None
        while asyncio.get_event_loop().time() < deadline:
            # Check if process has exited
            if self._proc.returncode is not None:
                stderr_data = await self._proc.stderr.read()
                raise RuntimeError(
                    f"MCP server exited with code {self._proc.returncode}: "
                    f"{stderr_data.decode('utf-8', errors='replace')}"
                )
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port, limit=10 * 1024 * 1024), timeout=1.0
                )
                break
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
                last_err = e
                await asyncio.sleep(0.2)
        else:
            # Timeout — try to capture stderr
            stderr_data = b""
            try:
                self._proc.kill()
                stderr_data = await asyncio.wait_for(self._proc.stderr.read(), timeout=2)
            except Exception:
                pass
            raise RuntimeError(
                f"MCP server failed to start within 10s on port {port} "
                f"(last error: {last_err}, stderr: {stderr_data.decode('utf-8', errors='replace')})"
            )

        # MCP initialize handshake
        await self._send({
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentcore", "version": "0.2.0"},
            },
        })
        await self._notify({"method": "notifications/initialized"})

        # Discover tools
        resp = await self._send({"method": "tools/list", "params": {}})
        self._tools = resp.get("result", {}).get("tools", [])
        logger.info("MCP server started, discovered %d tools", len(self._tools))

    async def _send(self, msg: dict) -> dict:
        if not self._writer or not self._reader:
            raise RuntimeError("MCP server not connected")
        self._req_id += 1
        msg.update({"jsonrpc": "2.0", "id": self._req_id})
        payload = json.dumps(msg) + "\n"
        try:
            self._writer.write(payload.encode("utf-8"))
            await self._writer.drain()
        except (ConnectionError, OSError) as e:
            raise RuntimeError(f"MCP server connection error: {e}") from e

        line = await asyncio.wait_for(self._reader.readline(), timeout=30)
        if not line:
            raise RuntimeError("MCP server disconnected")
        return json.loads(line)

    async def _notify(self, msg: dict) -> None:
        msg["jsonrpc"] = "2.0"
        payload = json.dumps(msg) + "\n"
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()

    async def call_tool(self, name: str, arguments: dict) -> dict[str, Any]:
        try:
            resp = await self._send({
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            logger.warning("MCP call failed (%s), attempting reconnect: %s", name, e)
            try:
                await self._reconnect()
                resp = await self._send({
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
            except Exception as e2:
                return {"error": f"MCP server unavailable after reconnect: {e2}"}
        result = resp.get("result", {})
        parts = result.get("content", [])
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if result.get("isError"):
            return {"error": text}
        return {"output": text}

    async def _reconnect(self) -> None:
        """Restart the MCP server and reconnect."""
        logger.info("Reconnecting MCP server...")
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        return self._tools

    async def stop(self) -> None:
        if self._writer:
            try:
                self._writer.close()
            except (ConnectionError, OSError):
                pass
        self._reader = None
        self._writer = None
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        self._proc = None


class CoreToolkitPlugin(Plugin):
    """Spawns the core-toolkit MCP server and registers its tools.

    Tools (provided by MCP server):
        read_file, write_file, execute_command, search_files
    """

    @property
    def name(self) -> str:
        return "core-toolkit"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "File I/O and shell tools via MCP server"

    def __init__(self) -> None:
        self._bridge = _MCPBridge()

    async def setup(self, ctx: PluginContext) -> None:
        await self._bridge.start()

        for tool_def in self._bridge.tool_definitions:
            tool_name = tool_def["name"]

            def _make_handler(tn: str):
                async def handler(**kwargs):
                    return await self._bridge.call_tool(tn, kwargs)
                return handler

            ctx.register_tool(
                name=tool_name,
                handler=_make_handler(tool_name),
                description=tool_def.get("description", ""),
                parameters=tool_def.get("inputSchema", {"type": "object", "properties": {}}),
            )

    async def teardown(self, ctx: PluginContext) -> None:
        await self._bridge.stop()
