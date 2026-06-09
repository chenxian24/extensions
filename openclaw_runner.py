"""OpenClaw Runner — secure CLI agent using agentcore + extensions.

Usage:
    python -m extensions.openclaw_runner
    OPENAI_API_KEY=sk-xxx python -m extensions.openclaw_runner

Environment variables:
    OPENAI_API_KEY   — API key (required)
    OPENAI_MODEL     — model name (default: gpt-4o)
    OPENAI_BASE_URL  — API base URL (optional)
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
from extensions.commands_plugin import CommandsPlugin
from extensions.config_plugin import ConfigPlugin
from extensions.context_plugin import ContextPlugin
from extensions.core_toolkit import CoreToolkitPlugin
from extensions.dm_pairing_plugin import DMPairingPlugin
from extensions.mcp_plugin import MCPPlugin
from extensions.memory_plugin import MemoryPlugin
from extensions.sandbox_plugin import SandboxPlugin
from extensions.security_policy_plugin import SecurityPolicyPlugin
from extensions.session_dag_plugin import SessionDAGPlugin
from extensions.skills_plugin import SkillsPlugin
from extensions.tools_plugin import ToolsPlugin
from extensions.tool_search_plugin import ToolSearchPlugin
from extensions.trajectory_plugin import TrajectoryPlugin
from extensions.prompts.openclaw import (
    OPENCLAW_BASE_PROMPT,
    OPENCLAW_BEHAVIOR_CONTRACT,
    OPENCLAW_COMMUNICATION,
    OPENCLAW_DOCS,
    OPENCLAW_EXECUTION_BIAS,
    OPENCLAW_IDENTITY,
    OPENCLAW_INTERACTION_STYLE,
    OPENCLAW_SAFETY,
    OPENCLAW_SANDBOX,
    OPENCLAW_SECURITY_POLICY,
    OPENCLAW_SILENT_REPLIES,
    OPENCLAW_SKILLS,
    OPENCLAW_SUBAGENT_DELEGATION,
    OPENCLAW_TOOL_CALL_STYLE,
    OPENCLAW_TOOLING,
    OPENCLAW_TRAJECTORY,
)
from extensions.prompts.loader import load_prompt_sections

_PROMPTS_DIR = Path(__file__).parent / "prompts" / "openclaw"


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

def build_config(config_plugin: ConfigPlugin | None = None) -> AgentConfig:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print(_color("Error: OPENAI_API_KEY environment variable is required", Colors.RED, Colors.BOLD))
        sys.exit(1)

    hermes: dict[str, Any] = {}
    if config_plugin:
        hermes = config_plugin.config

    return AgentConfig(
        model=ModelConfig(
            provider=hermes.get("provider", "openai"),
            model=hermes.get("model", os.environ.get("OPENAI_MODEL", "gpt-4o")),
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL", ""),
            temperature=hermes.get("temperature", 0.7),
            max_tokens=hermes.get("max_tokens", 16384),
            timeout=float(hermes.get("timeout", 120.0)),
        ),
        runtime=RuntimeConfig(
            max_tool_rounds=hermes.get("max_tool_rounds", 20),
        ),
        system_prompt=SystemPromptConfig(
            template=OPENCLAW_BASE_PROMPT,
        ),
    )


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

async def run() -> None:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        Colors.disable()

    # --- Config ---
    config_plugin = ConfigPlugin()
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
    # Base plugins
    pm.register(config_plugin)
    pm.register(CoreToolkitPlugin())
    pm.register(MCPPlugin())
    pm.register(ToolsPlugin())
    pm.register(MemoryPlugin())
    pm.register(ContextPlugin())
    # OpenClaw security plugins
    pm.register(SecurityPolicyPlugin())
    pm.register(SandboxPlugin())
    pm.register(DMPairingPlugin())
    # OpenClaw feature plugins
    pm.register(TrajectoryPlugin())
    pm.register(SessionDAGPlugin())
    pm.register(ToolSearchPlugin())
    # CLI plugins
    pm.register(CommandsPlugin())
    pm.register(SkillsPlugin())

    # --- Runtime (composes hooks, tools, context, events, plugins) ---
    hermes = config.metadata.get("hermes", {})
    session_dir = Path(hermes.get("session_dir", Path.home() / ".openclaw" / "sessions"))
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime = AgentRuntime(engine=engine, plugins=pm, session_store=JsonlSessionStore(session_dir))
    await runtime.initialize()

    # --- Build system prompt ---
    hermes_config = config.metadata.get("hermes", {})
    skills: list[dict[str, Any]] = hermes_config.get("skills", [])

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

    # Add environment info
    import platform
    _os_name = platform.system()
    _os_info = platform.platform()
    _shell = "cmd.exe / PowerShell" if _os_name == "Windows" else "bash/sh"
    extra_sections["Environment"] = (
        f"OS: {_os_info}\n"
        f"Shell: {_shell}\n"
        f"Use OS-appropriate commands. "
        f"On Windows, use `dir` not `ls`, `type` not `cat`, `findstr` not `grep`, "
        f"`more` not `head`/`tail`, `del` not `rm`, `copy` not `cp`."
    )

    # Add OpenClaw sections (load from .md files, fallback to Python constants)
    _md_sections = load_prompt_sections(_PROMPTS_DIR)
    _fallback = {
        "Tooling": OPENCLAW_TOOLING,
        "Tool Call Style": OPENCLAW_TOOL_CALL_STYLE,
        "Execution Bias": OPENCLAW_EXECUTION_BIAS,
        "Safety": OPENCLAW_SAFETY,
        "Security Policy": OPENCLAW_SECURITY_POLICY,
        "Sandbox": OPENCLAW_SANDBOX,
        "Subagent Delegation": OPENCLAW_SUBAGENT_DELEGATION,
        "Trajectory": OPENCLAW_TRAJECTORY,
        "Skills": OPENCLAW_SKILLS,
        "Docs": OPENCLAW_DOCS,
        "Silent Replies": OPENCLAW_SILENT_REPLIES,
        "Communication": OPENCLAW_COMMUNICATION,
        "Behavior Contract": OPENCLAW_BEHAVIOR_CONTRACT,
        "Interaction Style": OPENCLAW_INTERACTION_STYLE,
    }
    for key, fallback_val in _fallback.items():
        extra_sections[key] = _md_sections.get(key, fallback_val)

    prompt_builder = DynamicPromptBuilder(
        tool_registry=runtime.tools,
        base_prompt=config.system_prompt.template,
        identity=OPENCLAW_IDENTITY,
        extra_sections=extra_sections if extra_sections else None,
    )
    config.system_prompt = SystemPromptConfig(template=prompt_builder.build())
    engine.configure(config)

    # --- Session ---
    session_name = f"session-{int(time.time())}"
    session = await runtime.create_session(session_name)

    # --- CLI loop ---
    tool_count = len(runtime.tools.list_names())
    skill_count = len(skills)

    print(_color("OpenClaw CLI", Colors.BOLD, Colors.MAGENTA), end="")
    print(_color(f" — {tool_count} tools", Colors.DIM), end="")
    if skill_count:
        print(_color(f", {skill_count} skills", Colors.DIM), end="")
    print(_color(" — security: 11-layer policy + sandbox", Colors.DIM))
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
                print(_color("OpenClaw: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
                async for event in runtime.run(user_input, max_rounds=config.runtime.max_tool_rounds):
                    if event.type == StreamEventType.TEXT_DELTA and event.text:
                        print(event.text, end="", flush=True)
                    elif event.type == StreamEventType.ERROR:
                        print(_color(f"\n[Error: {event.error}]", Colors.RED), flush=True)
                    elif event.type == StreamEventType.TOOL_RESULT:
                        result = event.tool_result or {}
                        output = result.get("output", result.get("error", ""))
                        if output:
                            display = str(output)[:300]
                            if len(str(output)) > 300:
                                display += "..."
                            print(_color(f"  {display}", Colors.DIM), flush=True)
                        print(_color("OpenClaw: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
                print()  # newline after response
            except KeyboardInterrupt:
                print(_color("\n[Interrupted]", Colors.YELLOW))
                continue
            except Exception as e:
                print(_color(f"\n[Error: {e}]", Colors.RED))
                continue

            print()

    except KeyboardInterrupt:
        print(_color("\nGoodbye!", Colors.MAGENTA))
    finally:
        await runtime.end_session()
        print(_color(f"Session saved: {session_name}", Colors.DIM))
        await runtime.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
