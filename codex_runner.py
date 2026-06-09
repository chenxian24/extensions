"""Codex Runner — AI coding assistant built on agentcore + extensions.

Usage:
    python -m extensions.codex_runner
    OPENAI_API_KEY=sk-xxx python -m extensions.codex_runner

Environment variables:
    OPENAI_API_KEY   — API key (required)
    OPENAI_MODEL     — model name (default: gpt-4o)
    OPENAI_BASE_URL  — API base URL (optional)

Config file:
    ~/.codex/config.toml — user-level config
    .codex/config.toml  — project-level config (overrides user)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from agentcore.config.schema import AgentConfig, ModelConfig, RuntimeConfig, SystemPromptConfig
from agentcore.core.engine import AgentEngine
from agentcore.models.events import StreamEvent, StreamEventType
from agentcore.plugins.manager import PluginManager
from agentcore.prompts.builder import DynamicPromptBuilder
from agentcore.core.session_store import JsonlSessionStore
from agentcore.runtime import AgentRuntime

# Extension plugins
from extensions.agents_plugin import AgentsPlugin
from extensions.approval_plugin import ApprovalPlugin
from extensions.codex_commands_plugin import CodexCommandsPlugin
from extensions.codex_config_plugin import CodexConfigPlugin
from extensions.prompts.codex import (
    CODEX_BASE_PROMPT,
    CODEX_CODING_GUIDELINES,
    CODEX_IDENTITY,
    CODEX_TOOL_USAGE,
    CODEX_WORKFLOW,
)
from extensions.context_plugin import ContextPlugin
from extensions.core_toolkit import CoreToolkitPlugin
from extensions.edit_plugin import EditPlugin
from extensions.memory_plugin import MemoryPlugin
from extensions.mcp_plugin import MCPPlugin
from extensions.sandbox_plugin import SandboxPlugin
from extensions.security_policy_plugin import SecurityPolicyPlugin
from extensions.skills_plugin import SkillsPlugin
from extensions.tools_plugin import ToolsPlugin


# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


def _color(text: str, *codes: str) -> str:
    return "".join(codes) + text + Colors.RESET


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def build_config(config_plugin: CodexConfigPlugin | None = None) -> AgentConfig:
    """Build config from CodexConfigPlugin + environment variables."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print(_color("Error: OPENAI_API_KEY environment variable is required", Colors.RED, Colors.BOLD))
        sys.exit(1)

    codex: dict[str, Any] = {}
    if config_plugin:
        codex = config_plugin.config

    model_cfg = codex.get("model", {})
    sandbox_cfg = codex.get("sandbox", {})
    approval_cfg = codex.get("approval", {})
    agent_cfg = codex.get("agent", {})

    return AgentConfig(
        model=ModelConfig(
            provider=model_cfg.get("provider", os.environ.get("CODEX_PROVIDER", "openai")),
            model=model_cfg.get("model", os.environ.get("OPENAI_MODEL", "gpt-4o")),
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL", model_cfg.get("base_url", "")),
            temperature=model_cfg.get("temperature", 0.3),
            max_tokens=model_cfg.get("max_tokens", 16384),
            timeout=float(model_cfg.get("timeout", 120.0)),
        ),
        runtime=RuntimeConfig(
            max_tool_rounds=agent_cfg.get("max_rounds", 20),
        ),
        system_prompt=SystemPromptConfig(
            template=CODEX_BASE_PROMPT,
        ),
    )


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

async def run() -> None:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        Colors.disable()

    # --- Config plugin (loads ~/.codex/config.toml) ---
    config_plugin = CodexConfigPlugin()
    config = build_config(config_plugin)

    # --- Atomic layer ---
    engine = AgentEngine(config)

    # --- Plugin layer ---
    pm = PluginManager(
        config=config,
        hooks=None,
        tools=None,
        context=None,
        events=None,
    )

    # Config plugin (first — loads config, registers instructions hook)
    pm.register(config_plugin)

    # Core tools (MCP bridge for file I/O + shell)
    pm.register(CoreToolkitPlugin())

    # File tools
    pm.register(ToolsPlugin())
    pm.register(EditPlugin())

    # Sandbox (codex-style sandboxed execution)
    codex_config = config_plugin.config
    sandbox_cfg = codex_config.get("sandbox", {})
    pm.register(SandboxPlugin(
        default_backend=sandbox_cfg.get("backend", "local"),
        docker_image=sandbox_cfg.get("docker_image", "python:3.12-slim"),
        allowed_dirs=sandbox_cfg.get("writable_roots", ["."]),
    ))

    # Security (11-layer policy pipeline)
    pm.register(SecurityPolicyPlugin())

    # Approval (human-in-the-loop gate)
    approval_cfg = codex_config.get("approval", {})
    approval_policy = approval_cfg.get("policy", "on_request")

    # Configure approval based on policy
    if approval_policy == "never":
        # No approval needed — empty list
        approval_tools: list[str] = []
    elif approval_policy == "unless_trusted":
        # Most tools need approval
        approval_tools = ["write_file", "edit_file", "execute_command", "execute_sandboxed"]
    else:
        # on_request — only dangerous tools
        approval_tools = ["execute_command", "execute_sandboxed"]

    pm.register(ApprovalPlugin(tools_needing_approval=approval_tools))

    # Sub-agents
    agents_plugin = AgentsPlugin()
    pm.register(agents_plugin)
    config.metadata["_agents_plugin"] = agents_plugin

    # Memory + Context + Skills
    pm.register(MemoryPlugin())
    pm.register(ContextPlugin())
    pm.register(SkillsPlugin())

    # MCP client (for external MCP servers)
    pm.register(MCPPlugin())

    # Codex commands
    pm.register(CodexCommandsPlugin())

    # --- Runtime ---
    session_dir = Path(codex_config.get("session_dir", Path.home() / ".codex" / "sessions"))
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime = AgentRuntime(engine=engine, plugins=pm, session_store=JsonlSessionStore(session_dir))
    await runtime.initialize()

    # --- Build system prompt ---
    codex_meta = config.metadata.get("codex", {})
    skills: list[dict[str, Any]] = codex_meta.get("skills", [])

    extra_sections: dict[str, str] = {}
    for skill in skills:
        name = skill.get("name", "unnamed")
        description = skill.get("description", "")
        content = skill.get("content", "")
        if content:
            title = f"Skill: {name}"
            if description:
                title += f" — {description}"
            extra_sections[title] = content

    prompt_builder = DynamicPromptBuilder(
        tool_registry=runtime.tools,
        base_prompt=config.system_prompt.template,
        identity=CODEX_IDENTITY,
        extra_sections=extra_sections if extra_sections else None,
    )
    config.system_prompt = SystemPromptConfig(template=prompt_builder.build())
    engine.configure(config)

    # --- Session ---
    session_name = f"session-{int(time.time())}"
    session = await runtime.create_session(session_name)

    # --- CLI loop ---
    tool_count = len(runtime.tools.list_names())
    sandbox_mode = config_plugin.sandbox_mode

    print(_color("Codex CLI", Colors.BOLD, Colors.GREEN), end="")
    print(_color(f" — {tool_count} tools, sandbox: {sandbox_mode}", Colors.DIM), end="")
    print(_color(f", approval: {approval_policy}", Colors.DIM))
    print(_color("  /help for commands, Ctrl+C to exit", Colors.DIM))
    print()

    try:
        while True:
            try:
                user_input = input(_color("You: ", Colors.BLUE, Colors.BOLD)).strip()
            except EOFError:
                break

            if not user_input:
                continue

            try:
                print(_color("Codex: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
                async for event in runtime.run(user_input, max_rounds=config.runtime.max_tool_rounds):
                    if event.type == StreamEventType.TEXT_DELTA and event.text:
                        print(event.text, end="", flush=True)
                    elif event.type == StreamEventType.ERROR:
                        print(_color(f"\n[Error: {event.error}]", Colors.RED), flush=True)
                    elif event.type == StreamEventType.TOOL_RESULT:
                        result = event.tool_result or {}
                        output = result.get("output", result.get("error", ""))
                        if output:
                            display = str(output)[:500]
                            if len(str(output)) > 500:
                                display += "..."
                            print(_color(f"\n  {display}", Colors.DIM), flush=True)
                print()
            except KeyboardInterrupt:
                print(_color("\n[Interrupted]", Colors.YELLOW))
                continue
            except Exception as e:
                print(_color(f"\n[Error: {e}]", Colors.RED))
                continue

            print()

    except KeyboardInterrupt:
        print(_color("\nGoodbye!", Colors.GREEN))
    finally:
        await runtime.end_session()
        print(_color(f"Session saved: {session_name}", Colors.DIM))
        await runtime.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
