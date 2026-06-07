"""MCP Plugin — configures MCPManager to connect to MCP servers.

This extension creates an MCPManager and connects to configured servers.
It reads server configs from PluginConfig.options and connects on ENGINE_INIT.
"""

from __future__ import annotations

from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.mcp.manager import MCPManager
from agentcore.models.base import ToolCall, ToolCallFunction
from agentcore.plugins.base import Plugin, PluginContext
from agentcore.tools.registry import ToolRegistry


class MCPPlugin(Plugin):
    """Connects to MCP servers and registers their tools.

    Config (via PluginConfig.options):
        servers: list of {name, command, args, env} or {name, host, port}
    """

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def version(self) -> str:
        return "0.2.0"

    @property
    def description(self) -> str:
        return "MCP client — connects to MCP servers and registers their tools"

    def __init__(self, servers: list[dict[str, Any]] | None = None) -> None:
        self._server_configs = servers or []
        self._mcp_manager: MCPManager | None = None
        self._tools: ToolRegistry | None = None

    async def setup(self, ctx: PluginContext) -> None:
        self._tools = ctx.tools
        self._mcp_manager = MCPManager()

        # If no servers passed via constructor, try loading from config
        if not self._server_configs:
            hermes_config = ctx.config.metadata.get("hermes", {})
            mcp_servers = hermes_config.get("mcp_servers", {})
            if isinstance(mcp_servers, dict):
                for name, cfg in mcp_servers.items():
                    if isinstance(cfg, dict):
                        cfg["name"] = name
                        self._server_configs.append(cfg)

        ctx.register_hook(HookName.ENGINE_INIT, self._connect_servers, priority=100)
        ctx.register_hook(HookName.ENGINE_SHUTDOWN, self._disconnect_servers, priority=100)

    async def _connect_servers(self, _ctx: HookContext) -> None:
        if not self._mcp_manager:
            return
        for server_cfg in self._server_configs:
            name = server_cfg.get("name", "unnamed")
            try:
                if "command" in server_cfg:
                    await self._mcp_manager.add_server_stdio(
                        name=name,
                        command=server_cfg["command"],
                        args=server_cfg.get("args", []),
                        cwd=server_cfg.get("cwd"),
                    )
                elif "port" in server_cfg:
                    await self._mcp_manager.add_server_tcp(
                        name=name,
                        host=server_cfg.get("host", "127.0.0.1"),
                        port=server_cfg["port"],
                    )
                # Register MCP tools into the ToolRegistry
                tools = self._mcp_manager.get_server_tools(name)
                for tool_def in tools:
                    tool_name = tool_def["function"]["name"]

                    def _make_handler(tn: str):
                        async def handler(**kwargs):
                            import json
                            tc = ToolCall(
                                id="mcp",
                                type="function",
                                function=ToolCallFunction(name=tn, arguments=json.dumps(kwargs)),
                            )
                            return await self._mcp_manager.call_tool(tc)
                        return handler

                    if self._tools:
                        self._tools.register(
                            name=tool_name,
                            handler=_make_handler(tool_name),
                            description=tool_def["function"].get("description", ""),
                            parameters=tool_def["function"].get("parameters", {}),
                        )

                print(f"[mcp] Connected to '{name}', registered {len(tools)} tools")
            except Exception as e:
                print(f"[mcp] Failed to connect to '{name}': {e}")

    async def _disconnect_servers(self, _ctx: HookContext) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()

    async def teardown(self, _ctx: PluginContext) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
