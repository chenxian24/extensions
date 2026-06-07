"""Commands Plugin — slash command interception."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class CommandsPlugin(Plugin):
    """Intercepts slash commands (e.g. /clear, /model) before they reach the LLM.

    Hooks:
        PRE_BUILD_MESSAGES (priority=10) — detect and handle commands

    Built-in commands:
        /clear    — clear session messages
        /compact  — trigger context compression
        /model    — show/switch model info
        /plugins  — list loaded plugins
        /hooks    — list registered hooks
    """

    @property
    def name(self) -> str:
        return "commands"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Slash command interception and handling"

    def __init__(self) -> None:
        self._commands: dict[str, Any] = {}

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_hook(HookName.PRE_BUILD_MESSAGES, self._intercept_command, priority=10)

        # Register built-in commands via metadata
        ctx.register_command("clear", self._cmd_clear, "Clear session messages")
        ctx.register_command("compact", self._cmd_compact, "Trigger context compression")
        ctx.register_command("model", self._cmd_model, "Show model info")
        ctx.register_command("plugins", self._cmd_plugins, "List loaded plugins")
        ctx.register_command("hooks", self._cmd_hooks, "List registered hooks")
        ctx.register_command("help", self._cmd_help, "List all available commands")
        ctx.register_command("status", self._cmd_status, "Show engine status and stats")
        ctx.register_command("history", self._cmd_history, "Show session message history")
        ctx.register_command("save", self._cmd_save, "Save current session to file")
        ctx.register_command("load", self._cmd_load, "Load a saved session")

    async def _intercept_command(self, ctx: HookContext) -> None:
        user_input = ctx.user_input.strip()
        if not user_input.startswith("/"):
            return

        parts = user_input.split(maxsplit=1)
        cmd_name = parts[0][1:]  # remove leading /
        cmd_args = parts[1] if len(parts) > 1 else ""

        # Check registered commands in metadata
        commands = ctx.engine.config.metadata.get("commands", {})
        cmd_info = commands.get(cmd_name)

        if cmd_info and "handler" in cmd_info:
            handler = cmd_info["handler"]
            result = handler(ctx, cmd_args) if cmd_args else handler(ctx)
            # Await if coroutine
            import asyncio
            if asyncio.iscoroutine(result):
                result = await result
            ctx.cancel = True
            ctx.metadata["cancel_reason"] = str(result) if result else f"Command /{cmd_name} executed"
        else:
            ctx.cancel = True
            ctx.metadata["cancel_reason"] = f"Unknown command: /{cmd_name}"

    @staticmethod
    def _cmd_clear(ctx: HookContext, _args: str = "") -> str:
        if ctx.session:
            ctx.session.clear()
            return "Session cleared."
        return "No active session."

    @staticmethod
    async def _cmd_compact(ctx: HookContext, _args: str = "") -> str:
        if ctx.session and ctx.context:
            messages = ctx.session.messages
            max_tokens = ctx.engine.config.context.max_tokens if ctx.engine else 128000
            compressed = ctx.context.compress(messages, max_tokens=max_tokens)
            ctx.session.clear()
            for msg in compressed:
                ctx.session.add_message(msg)
            return f"Compacted {len(messages)} messages to {len(compressed)}."
        return "No active session or context engine."

    @staticmethod
    def _cmd_model(ctx: HookContext, _args: str = "") -> str:
        mc = ctx.engine.config.model
        return f"Model: {mc.model} (provider: {mc.provider}, temp: {mc.temperature})"

    @staticmethod
    def _cmd_plugins(ctx: HookContext, _args: str = "") -> str:
        plugins = ctx.metadata.get("plugins", [])
        if not plugins:
            return "No plugins registered."
        lines = [f"  {p['name']} v{p['version']} — {p.get('description', '')}" for p in plugins]
        return "Loaded plugins:\n" + "\n".join(lines)

    @staticmethod
    def _cmd_hooks(ctx: HookContext, _args: str = "") -> str:
        hooks = ctx.hooks.list_hooks() if ctx.hooks else {}
        if not hooks:
            return "No hooks registered."
        lines = [f"  {name}: {', '.join(handlers)}" for name, handlers in hooks.items()]
        return "Registered hooks:\n" + "\n".join(lines)

    @staticmethod
    def _cmd_help(ctx: HookContext, _args: str = "") -> str:
        commands = ctx.engine.config.metadata.get("commands", {})
        if not commands:
            return "No commands available."
        lines = []
        for name in sorted(commands.keys()):
            desc = commands[name].get("description", "")
            lines.append(f"  /{name:12s} — {desc}")
        return "Available commands:\n" + "\n".join(lines)

    @staticmethod
    def _cmd_status(ctx: HookContext, _args: str = "") -> str:
        mc = ctx.engine.config.model
        stats = ctx.engine.stats
        session_msg_count = len(ctx.session.messages) if ctx.session else 0
        lines = [
            f"Model:    {mc.model} ({mc.provider})",
            f"Temp:     {mc.temperature}",
            f"Session:  {session_msg_count} messages",
        ]
        if stats:
            agg = stats.get_aggregate()
            if agg:
                lines.append(f"Requests: {agg.total_requests}")
                lines.append(f"Tokens:   {agg.total_prompt_tokens} in / {agg.total_completion_tokens} out")
                lines.append(f"Avg latency: {agg.avg_latency_ms:.0f}ms")
        hermes_config = ctx.engine.config.metadata.get("hermes", {})
        if hermes_config:
            lines.append(f"Streaming: {hermes_config.get('streaming', True)}")
            lines.append(f"Theme:     {hermes_config.get('theme', 'default')}")
        return "\n".join(lines)

    @staticmethod
    def _cmd_history(ctx: HookContext, args: str = "") -> str:
        if not ctx.session:
            return "No active session."
        messages = ctx.session.messages
        if not messages:
            return "Session is empty."
        # Parse optional count argument
        count = 20
        if args.strip().isdigit():
            count = min(int(args.strip()), 100)
        recent = messages[-count:]
        lines = []
        for i, msg in enumerate(recent):
            role = msg.role.value if hasattr(msg.role, 'value') else str(msg.role)
            content = msg.content or ""
            # Truncate long messages
            if len(content) > 200:
                content = content[:200] + "..."
            # Handle multi-line content
            if "\n" in content:
                first_line = content.split("\n")[0]
                lines.append(f"  [{role}] {first_line}...")
            else:
                lines.append(f"  [{role}] {content}")
        header = f"Last {len(recent)} of {len(messages)} messages:" if len(messages) > count else f"{len(messages)} messages:"
        return header + "\n" + "\n".join(lines)

    @staticmethod
    async def _cmd_save(ctx: HookContext, args: str = "") -> str:
        if not ctx.session:
            return "No active session."
        hermes_config = ctx.engine.config.metadata.get("hermes", {})
        session_dir = Path(hermes_config.get("session_dir", Path.home() / ".hermes" / "sessions"))
        session_dir.mkdir(parents=True, exist_ok=True)

        name = args.strip() or f"session-{int(time.time())}"
        if not name.endswith(".json"):
            name += ".json"
        path = session_dir / name

        # Serialize messages
        messages_data = []
        for msg in ctx.session.messages:
            messages_data.append({
                "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                "content": msg.content,
                "name": msg.name,
                "tool_call_id": msg.tool_call_id,
            })

        try:
            path.write_text(json.dumps(messages_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"Session saved to {path.name} ({len(messages_data)} messages)"
        except Exception as e:
            return f"Failed to save session: {e}"

    @staticmethod
    async def _cmd_load(ctx: HookContext, args: str = "") -> str:
        if not ctx.session:
            return "No active session."
        hermes_config = ctx.engine.config.metadata.get("hermes", {})
        session_dir = Path(hermes_config.get("session_dir", Path.home() / ".hermes" / "sessions"))

        name = args.strip()
        if not name:
            # List available sessions
            if not session_dir.exists():
                return "No saved sessions found."
            files = sorted(session_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            if not files:
                return "No saved sessions found."
            lines = [f"  {f.name}" for f in files[:20]]
            return "Saved sessions:\n" + "\n".join(lines) + "\n\nUsage: /load <filename>"

        if not name.endswith(".json"):
            name += ".json"
        path = session_dir / name

        if not path.exists():
            return f"Session not found: {name}"

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            from agentcore.core.message import Message, MessageRole
            ctx.session.clear()
            for item in data:
                role = item.get("role", "user")
                msg = Message(
                    role=MessageRole(role),
                    content=item.get("content", ""),
                    name=item.get("name"),
                    tool_call_id=item.get("tool_call_id"),
                )
                ctx.session.add_message(msg)
            return f"Loaded session: {name} ({len(data)} messages)"
        except Exception as e:
            return f"Failed to load session: {e}"
