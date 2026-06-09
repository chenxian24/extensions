"""Approval Plugin — human-in-the-loop tool approval."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class ApprovalPlugin(Plugin):
    """Intercepts dangerous tool calls and requires human approval.

    Hooks:
        PRE_TOOL_CALL (priority=50) — check if tool needs approval,
        pause and wait for confirmation if so.

    Config options:
        tools_needing_approval: list of tool names (default: ["write_file", "execute_command"])
        approval_fn: async callable(tool_name, arguments) -> bool
    """

    @property
    def name(self) -> str:
        return "approval"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Human-in-the-loop approval for dangerous tools"

    def __init__(
        self,
        tools_needing_approval: list[str] | None = None,
        approval_fn: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._tools = set(tools_needing_approval or ["write_file", "execute_command"])
        self._approval_fn = approval_fn

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_hook(HookName.PRE_TOOL_CALL, self._check_approval, priority=50)

    async def _check_approval(self, ctx: HookContext) -> None:
        if not ctx.tool_call:
            return

        tool_name = ctx.tool_call.function.name
        policy_decision = ctx.metadata.get("policy_decision", "allow")

        # If policy says ASK, always require approval regardless of tool list
        # If policy says allow, check the plugin's own tool list
        needs_approval = (
            policy_decision == "ask"
            or tool_name in self._tools
        )
        if not needs_approval:
            return

        args = ctx.tool_call.function.arguments
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"raw": args}

        approved = await self._get_approval(tool_name, args, ctx)
        if not approved:
            ctx.cancel = True
            ctx.metadata["cancel_reason"] = f"Tool '{tool_name}' was denied by user approval"

    async def _get_approval(self, tool_name: str, arguments: dict, ctx: HookContext | None = None) -> bool:
        # Check if /approve or /reject command set a response
        if ctx:
            response = ctx.metadata.get("approval_response")
            if response:
                ctx.metadata.pop("approval_response", None)
                return response == "approved"

        if self._approval_fn:
            result = self._approval_fn(tool_name, arguments)
            if asyncio.iscoroutine(result):
                return await result
            return bool(result)

        # Default: prompt on stdin (non-blocking via executor)
        print(f"\n[approval] Tool '{tool_name}' requires approval.")
        print(f"  Arguments: {arguments}")
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: input("  Allow? [y/N]: "))
            return response.strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False
