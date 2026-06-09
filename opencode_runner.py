"""OpenCode Runner — coding-focused CLI agent using agentcore + extensions.

Usage:
    python -m extensions.opencode_runner
    OPENAI_API_KEY=sk-xxx python -m extensions.opencode_runner

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
from extensions.agents_plugin import AgentsPlugin
from extensions.commands_plugin import CommandsPlugin
from extensions.config_plugin import ConfigPlugin
from extensions.context_plugin import ContextPlugin
from extensions.core_toolkit import CoreToolkitPlugin
from extensions.edit_plugin import EditPlugin
from extensions.instruction_plugin import InstructionPlugin
from extensions.lsp_plugin import LSPPlugin
from extensions.mcp_plugin import MCPPlugin
from extensions.memory_plugin import MemoryPlugin
from extensions.skills_plugin import SkillsPlugin
from extensions.tools_plugin import ToolsPlugin
from extensions.prompts.opencode import (
    OPENCODE_CODING_GUIDELINES,
    OPENCODE_CODE_REFS,
    OPENCODE_IDENTITY,
    OPENCODE_OBJECTIVITY,
    OPENCODE_PROMPTS,
    OPENCODE_STYLE,
    OPENCODE_TASK_MANAGEMENT,
    OPENCODE_TOOL_USAGE,
    OPENCODE_WORKFLOW,
)
from extensions.prompts.loader import load_prompt_sections

_PROMPTS_DIR = Path(__file__).parent / "prompts" / "opencode"


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

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


def _color(text: str, *codes: str) -> str:
    return "".join(codes) + text + Colors.RESET


def _detect_model_family(model: str) -> str:
    model_lower = model.lower()
    if "claude" in model_lower:
        return "claude"
    if "gemini" in model_lower:
        return "gemini"
    return "gpt"


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

    model = hermes.get("model", os.environ.get("OPENAI_MODEL", "gpt-4o"))
    model_family = _detect_model_family(model)
    coding_prompt = OPENCODE_PROMPTS.get(model_family, OPENCODE_PROMPTS["gpt"])

    return AgentConfig(
        model=ModelConfig(
            provider=hermes.get("provider", "openai"),
            model=model,
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL", ""),
            temperature=hermes.get("temperature", 0.3),
            max_tokens=hermes.get("max_tokens", 16384),
            timeout=float(hermes.get("timeout", 120.0)),
        ),
        runtime=RuntimeConfig(
            max_tool_rounds=hermes.get("max_tool_rounds", 20),
        ),
        system_prompt=SystemPromptConfig(template=coding_prompt),
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
    config.metadata["_engine"] = engine  # For agents_plugin

    # --- Plugin layer ---
    agents_plugin = AgentsPlugin()
    instruction_plugin = InstructionPlugin(work_dir=".")

    pm = PluginManager(
        config=config,
        hooks=None,
        tools=None,
        context=None,
        events=None,
    )
    pm.register(config_plugin)
    pm.register(CoreToolkitPlugin())
    pm.register(MCPPlugin())
    pm.register(ToolsPlugin())
    pm.register(EditPlugin())
    pm.register(MemoryPlugin())
    pm.register(ContextPlugin())
    pm.register(LSPPlugin(root_dir="."))
    pm.register(agents_plugin)
    pm.register(instruction_plugin)
    pm.register(CommandsPlugin())
    pm.register(SkillsPlugin())

    # --- Runtime (composes hooks, tools, context, events, plugins) ---
    hermes_config = config.metadata.get("hermes", {})
    session_dir = Path(hermes_config.get("session_dir", Path.home() / ".opencode" / "sessions"))
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime = AgentRuntime(engine=engine, plugins=pm, session_store=JsonlSessionStore(session_dir))
    await runtime.initialize()

    # --- Build system prompt ---
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

    # Add coding-specific instructions (load from .md files, fallback to Python constants)
    _md_sections = load_prompt_sections(_PROMPTS_DIR)
    _fallback = {
        "Style": OPENCODE_STYLE,
        "Objectivity": OPENCODE_OBJECTIVITY,
        "Coding Guidelines": OPENCODE_CODING_GUIDELINES,
        "Tool Usage": OPENCODE_TOOL_USAGE,
        "Task Management": OPENCODE_TASK_MANAGEMENT,
        "Code Refs": OPENCODE_CODE_REFS,
        "Workflow": OPENCODE_WORKFLOW,
    }
    for key, fallback_val in _fallback.items():
        extra_sections[key] = _md_sections.get(key, fallback_val)

    prompt_builder = DynamicPromptBuilder(
        tool_registry=runtime.tools,
        base_prompt=config.system_prompt.template,
        identity=OPENCODE_IDENTITY,
        extra_sections=extra_sections if extra_sections else None,
    )
    config.system_prompt = SystemPromptConfig(template=prompt_builder.build())
    engine.configure(config)

    # --- Session ---
    session_name = f"session-{int(time.time())}"
    session = await runtime.create_session(session_name)

    # --- CLI loop ---
    tool_count = len(runtime.tools.list_names())
    model_family = _detect_model_family(config.model.model)

    print(_color("OpenCode CLI", Colors.BOLD, Colors.BLUE), end="")
    print(_color(f" — {tool_count} tools, model-family: {model_family}", Colors.DIM), end="")
    print(_color(" — type /help for commands, Ctrl+C to exit", Colors.DIM))
    print()

    try:
        while True:
            try:
                user_input = input(_color("You: ", Colors.GREEN, Colors.BOLD)).strip()
            except EOFError:
                break

            if not user_input:
                continue

            try:
                print(_color("OpenCode: ", Colors.BLUE, Colors.BOLD), end="", flush=True)
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
                            print(_color(f"\n  {display}", Colors.DIM), flush=True)
                print()  # newline after response
            except KeyboardInterrupt:
                print(_color("\n[Interrupted]", Colors.YELLOW))
                continue
            except Exception as e:
                print(_color(f"\n[Error: {e}]", Colors.RED))
                continue

            print()

    except KeyboardInterrupt:
        print(_color("\nGoodbye!", Colors.BLUE))
    finally:
        await runtime.end_session()
        print(_color(f"Session saved: {session_name}", Colors.DIM))
        await runtime.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
