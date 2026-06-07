"""Tool Search Plugin — dynamic tool discovery for OpenClaw.

Searches registered tools by keyword, name pattern, or capability.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


class ToolSearchPlugin(Plugin):
    """Dynamic tool directory search.

    Tools:
        search_tools(query) — search tools by keyword
        tool_info(tool_name) — get detailed tool information
        list_tools_by_capability(capability) — list tools by capability category
    """

    @property
    def name(self) -> str:
        return "tool-search"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Dynamic tool discovery and search"

    def __init__(self) -> None:
        self._tools: Any = None  # ToolRegistry reference

    async def setup(self, ctx: PluginContext) -> None:
        self._tools = ctx.tools

        ctx.register_tool(
            "search_tools",
            self._tool_search,
            description="Search available tools by keyword in name or description",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or pattern"},
                    "limit": {"type": "integer", "description": "Max results (default: 20)"},
                },
                "required": ["query"],
            },
        )
        ctx.register_tool(
            "tool_info",
            self._tool_info,
            description="Get detailed information about a specific tool",
            parameters={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Tool name"},
                },
                "required": ["tool_name"],
            },
        )
        ctx.register_tool(
            "list_tools_by_capability",
            self._tool_by_capability,
            description="List tools grouped by capability category",
            parameters={
                "type": "object",
                "properties": {
                    "capability": {"type": "string", "description": "Capability filter (file, shell, memory, search, security, sandbox, session, network)"},
                },
            },
        )

    # Capability keywords mapping
    CAPABILITIES: dict[str, list[str]] = {
        "file": ["read_file", "write_file", "edit_file", "glob_files", "list_directory"],
        "shell": ["execute_command", "execute_sandboxed"],
        "memory": ["save_memory", "search_memory", "recall_memory"],
        "search": ["search_files", "grep_files", "search_tools", "search_trajectory"],
        "security": ["add_policy_rule", "remove_policy_rule", "list_policy_rules", "set_sender_policy"],
        "sandbox": ["execute_sandboxed", "sandbox_status"],
        "session": ["create_branch", "list_branches", "switch_branch", "merge_branch", "delete_branch"],
        "network": ["web_search", "web_extract"],
        "pairing": ["generate_pairing_code", "approve_pairing", "list_pairings", "revoke_pairing"],
        "trajectory": ["export_trajectory", "trajectory_stats"],
    }

    # --- Tool implementations ---

    async def _tool_search(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not self._tools:
            return {"output": "Tool registry not available", "error": "Not initialized"}

        query_lower = query.lower()
        matches = []

        for entry in self._tools.list_tools():
            score = 0
            name = entry.name
            desc = entry.description or ""

            # Exact name match
            if query_lower == name.lower():
                score = 100
            # Name contains query
            elif query_lower in name.lower():
                score = 80
            # Glob pattern match
            elif fnmatch.fnmatch(name.lower(), f"*{query_lower}*"):
                score = 60
            # Description contains query
            elif query_lower in desc.lower():
                score = 40

            if score > 0:
                matches.append((score, name, desc))

        matches.sort(key=lambda x: -x[0])
        matches = matches[:limit]

        if not matches:
            return {"output": f"No tools matching: {query}"}

        lines = []
        for score, name, desc in matches:
            preview = desc[:80] + "..." if len(desc) > 80 else desc
            lines.append(f"  {name}: {preview}")
        return {"output": f"Found {len(matches)} tools:\n" + "\n".join(lines)}

    async def _tool_info(self, tool_name: str) -> dict[str, Any]:
        if not self._tools:
            return {"output": "Tool registry not available", "error": "Not initialized"}

        entry = self._tools.get(tool_name)
        if not entry:
            return {"output": f"Tool not found: {tool_name}", "error": "Not found"}

        lines = [
            f"Name: {entry.name}",
            f"Description: {entry.description or '(none)'}",
        ]

        params = entry.parameters
        if params and params.get("properties"):
            lines.append("Parameters:")
            properties = params.get("properties", {})
            required = params.get("required", [])
            for pname, pinfo in properties.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req = " (required)" if pname in required else ""
                lines.append(f"  {pname} ({ptype}){req}: {pdesc}")

        return {"output": "\n".join(lines)}

    async def _tool_by_capability(self, capability: str = "") -> dict[str, Any]:
        if not self._tools:
            return {"output": "Tool registry not available", "error": "Not initialized"}

        registered_names = set(self._tools.list_names())

        if capability:
            # Show specific capability
            tool_names = self.CAPABILITIES.get(capability.lower(), [])
            available = [n for n in tool_names if n in registered_names]
            if not available:
                return {"output": f"No tools for capability '{capability}' or capability unknown.\n"
                        f"Known: {', '.join(self.CAPABILITIES.keys())}"}
            lines = [f"[{capability}]"]
            for name in available:
                entry = self._tools.get(name)
                desc = entry.description if entry else ""
                lines.append(f"  {name}: {desc[:60]}")
            return {"output": "\n".join(lines)}

        # Show all capabilities
        lines = []
        for cap, tool_names in self.CAPABILITIES.items():
            available = [n for n in tool_names if n in registered_names]
            if available:
                lines.append(f"[{cap}] {', '.join(available)}")
        if not lines:
            return {"output": "No capability-matched tools found"}
        return {"output": "Tools by capability:\n" + "\n".join(lines)}
