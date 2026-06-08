"""Codex Commands Plugin — codex-specific slash commands.

Extends CommandsPlugin with additional commands for the Codex CLI:
    /diff       — Show git diff
    /test       — Run project tests
    /plan       — Switch to plan mode (read-only)
    /review     — Request code review via sub-agent
    /approve    — Approve pending tool call
    /reject     — Reject pending tool call
    /sandbox    — Show sandbox status
    /undo       — Show undo instructions
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import PluginContext
from extensions.commands_plugin import CommandsPlugin


class CodexCommandsPlugin(CommandsPlugin):
    """Codex-specific slash commands. Extends CommandsPlugin with codex commands."""

    @property
    def name(self) -> str:
        return "codex-commands"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Codex slash commands (/diff, /test, /plan, /review, etc.)"

    async def setup(self, ctx: PluginContext) -> None:
        # Register base hook + shared commands (/clear, /compact, /help, /status, /history, etc.)
        await super().setup(ctx)

        # Register codex-specific commands
        ctx.register_command("diff", self._cmd_diff, "Show git diff of current changes")
        ctx.register_command("test", self._cmd_test, "Run project tests")
        ctx.register_command("plan", self._cmd_plan, "Switch to plan mode (read-only exploration)")
        ctx.register_command("review", self._cmd_review, "Request code review via sub-agent")
        ctx.register_command("approve", self._cmd_approve, "Approve pending tool call")
        ctx.register_command("reject", self._cmd_reject, "Reject pending tool call")
        ctx.register_command("sandbox", self._cmd_sandbox, "Show sandbox status")
        ctx.register_command("undo", self._cmd_undo, "Show undo instructions")

    # --- Codex-specific command implementations ---

    async def _cmd_diff(self, ctx: HookContext, _args: str = "") -> str:
        """Show git diff."""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                return f"git diff --stat:\n{output}" if output else "No changes."
            return f"Git error: {result.stderr.strip()}"
        except FileNotFoundError:
            return "Git not found."
        except subprocess.TimeoutExpired:
            return "Git diff timed out."

    async def _cmd_test(self, ctx: HookContext, _args: str = "") -> str:
        """Run project tests by detecting the test framework."""
        import os
        from pathlib import Path

        root = Path(os.getcwd())
        test_cmd = None
        if (root / "package.json").exists():
            test_cmd = ["npm", "test"]
        elif (root / "Cargo.toml").exists():
            test_cmd = ["cargo", "test"]
        elif (root / "go.mod").exists():
            test_cmd = ["go", "test", "./..."]
        elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            test_cmd = ["python", "-m", "pytest", "-q"]
        elif (root / "Makefile").exists():
            test_cmd = ["make", "test"]

        if not test_cmd:
            return "Could not detect test framework. Try running tests manually."

        try:
            proc = await asyncio.create_subprocess_exec(
                *test_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            status = "passed" if proc.returncode == 0 else f"failed (exit code {proc.returncode})"
            return f"[{' '.join(test_cmd)}] {status}\n{output[-1000:]}"
        except FileNotFoundError:
            return f"Command not found: {test_cmd[0]}"

    async def _cmd_plan(self, ctx: HookContext, _args: str = "") -> str:
        """Switch to plan mode — restrict to read-only tools."""
        if ctx.engine and hasattr(ctx.engine, 'config'):
            codex = ctx.engine.config.metadata.setdefault("codex", {})
            codex["plan_mode"] = not codex.get("plan_mode", False)
            if codex["plan_mode"]:
                return "Plan mode enabled. Only read-only tools available.\nUse /plan again to exit."
            return "Plan mode disabled."
        return "Engine not available."

    async def _cmd_review(self, ctx: HookContext, _args: str = "") -> str:
        """Request code review via sub-agent."""
        if ctx.engine and hasattr(ctx.engine, 'config'):
            ctx.engine.config.metadata.setdefault("codex", {})["review_requested"] = True
        return "Code review requested. The agent will review recent changes on the next turn."

    async def _cmd_approve(self, ctx: HookContext, _args: str = "") -> str:
        """Approve pending tool call."""
        if ctx.metadata:
            ctx.metadata["approval_response"] = "approved"
        return "Approved."

    async def _cmd_reject(self, ctx: HookContext, _args: str = "") -> str:
        """Reject pending tool call."""
        if ctx.metadata:
            ctx.metadata["approval_response"] = "rejected"
        return "Rejected."

    async def _cmd_sandbox(self, ctx: HookContext, _args: str = "") -> str:
        """Show sandbox status."""
        codex_config = {}
        if ctx.engine and hasattr(ctx.engine, 'config'):
            codex_config = ctx.engine.config.metadata.get("codex", {})

        sandbox_mode = codex_config.get("sandbox", {}).get("mode", "workspace_write")
        approval_policy = codex_config.get("approval", {}).get("policy", "on_request")
        writable_roots = codex_config.get("sandbox", {}).get("writable_roots", ["."])

        return (
            f"Sandbox Status:\n"
            f"  Mode:     {sandbox_mode}\n"
            f"  Approval: {approval_policy}\n"
            f"  Writable: {', '.join(writable_roots)}"
        )

    async def _cmd_undo(self, ctx: HookContext, _args: str = "") -> str:
        """Show undo instructions."""
        return "Undo: Check /diff to see current changes.\nTo revert: git checkout -- <file> or git stash"
