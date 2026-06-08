"""Hermes Runner — CLI agent using agentcore + extensions.

Usage:
    python -m extensions.hermes_runner
    OPENAI_API_KEY=sk-xxx python -m extensions.hermes_runner

Environment variables:
    OPENAI_API_KEY   — API key (required)
    OPENAI_MODEL     — model name (default: gpt-4o)
    OPENAI_BASE_URL  — API base URL (optional)
    HERMES_STREAMING — enable streaming (default: true)
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
from agentcore.models.base import ChatParams
from agentcore.models.events import StreamEvent, StreamEventType
from agentcore.plugins.manager import PluginManager
from agentcore.prompts.builder import DynamicPromptBuilder
from agentcore.runtime import AgentRuntime

# Extension plugins
from extensions.approval_plugin import ApprovalPlugin
from extensions.commands_plugin import CommandsPlugin
from extensions.config_plugin import ConfigPlugin
from extensions.context_plugin import ContextPlugin
from extensions.core_toolkit import CoreToolkitPlugin
from extensions.mcp_plugin import MCPPlugin
from extensions.memory_plugin import MemoryPlugin
from extensions.skills_plugin import SkillsPlugin
from extensions.tools_plugin import ToolsPlugin
from extensions.prompts.hermes import (
    HERMES_BASE_PROMPT,
    HERMES_CONTEXT_FILES,
    HERMES_EXECUTION_DISCIPLINE,
    HERMES_IDENTITY,
    HERMES_MEMORY_GUIDANCE,
    HERMES_PROACTIVENESS,
    HERMES_SESSION_SEARCH,
    HERMES_SKILLS_GUIDANCE,
    HERMES_STYLE,
    HERMES_TOOL_USE_ENFORCEMENT,
)
from extensions.prompts.loader import load_prompt_sections

_PROMPTS_DIR = Path(__file__).parent / "prompts" / "hermes"


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
    """Build config from ConfigPlugin + environment variables."""
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
            template=HERMES_BASE_PROMPT,
        ),
    )


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

async def run() -> None:
    # Disable colors if not a terminal or NO_COLOR is set
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        Colors.disable()

    # --- Config plugin (loads ~/.hermes/config.yaml) ---
    config_plugin = ConfigPlugin()
    config = build_config(config_plugin)

    # --- Atomic layer: engine ---
    engine = AgentEngine(config)

    # --- Plugin layer ---
    pm = PluginManager(
        config=config,
        hooks=None,  # AgentRuntime will create these
        tools=None,
        context=None,
        events=None,
    )
    pm.register(config_plugin)
    pm.register(CoreToolkitPlugin())
    pm.register(MCPPlugin())
    pm.register(ToolsPlugin())
    pm.register(MemoryPlugin())
    pm.register(ContextPlugin())
    pm.register(ApprovalPlugin())
    pm.register(CommandsPlugin())
    pm.register(SkillsPlugin())

    # --- Runtime (composes hooks, tools, context, events, plugins) ---
    runtime = AgentRuntime(engine=engine, plugins=pm)
    await runtime.initialize()

    # --- Build system prompt with tool descriptions + skills ---
    hermes_config = config.metadata.get("hermes", {})
    skills: list[dict[str, Any]] = hermes_config.get("skills", [])

    # Build extra sections from skills
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

    # Add Hermes-specific sections (load from .md files, fallback to Python constants)
    _md_sections = load_prompt_sections(_PROMPTS_DIR)
    _fallback = {
        "Tool Use Enforcement": HERMES_TOOL_USE_ENFORCEMENT,
        "Execution Discipline": HERMES_EXECUTION_DISCIPLINE,
        "Memory Guidance": HERMES_MEMORY_GUIDANCE,
        "Skills Guidance": HERMES_SKILLS_GUIDANCE,
        "Session Search": HERMES_SESSION_SEARCH,
        "Style": HERMES_STYLE,
        "Proactiveness": HERMES_PROACTIVENESS,
        "Context Files": HERMES_CONTEXT_FILES,
    }
    for key, fallback_val in _fallback.items():
        extra_sections[key] = _md_sections.get(key, fallback_val)

    prompt_builder = DynamicPromptBuilder(
        tool_registry=runtime.tools,
        base_prompt=config.system_prompt.template,
        identity=HERMES_IDENTITY,
        extra_sections=extra_sections if extra_sections else None,
    )
    config.system_prompt = SystemPromptConfig(template=prompt_builder.build())
    engine.configure(config)

    # --- Session ---
    session_dir = Path(hermes_config.get("session_dir", Path.home() / ".hermes" / "sessions"))
    session_dir.mkdir(parents=True, exist_ok=True)
    session_name = f"session-{int(time.time())}"
    session = await runtime.create_session(session_name)

    # --- Session save helper ---
    def _auto_save() -> None:
        """Auto-save session on exit."""
        if not session.messages:
            return
        save_path = session_dir / f"{session_name}.json"
        messages_data = []
        for msg in session.messages:
            messages_data.append({
                "role": msg.role.value,
                "content": msg.content,
                "name": msg.name,
                "tool_call_id": msg.tool_call_id,
            })
        try:
            save_path.write_text(json.dumps(messages_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # --- CLI loop ---
    skill_count = len(skills)
    tool_count = len(runtime.tools.list_names())

    print(_color("Hermes CLI", Colors.BOLD, Colors.CYAN), end="")
    print(_color(f" — {tool_count} tools", Colors.DIM), end="")
    if skill_count:
        print(_color(f", {skill_count} skills", Colors.DIM), end="")
    print(_color(" — type /help for commands, Ctrl+C to exit", Colors.DIM))
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
                print(_color("Hermes: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
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
                        print(_color("Hermes: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
                print()  # newline after response
            except KeyboardInterrupt:
                print(_color("\n[Interrupted]", Colors.YELLOW))
                continue
            except Exception as e:
                print(_color(f"\n[Error: {e}]", Colors.RED))
                continue

            print()

    except KeyboardInterrupt:
        print(_color("\nGoodbye!", Colors.CYAN))
    finally:
        await runtime.end_session()
        _auto_save()
        print(_color(f"Session saved: {session_name}.json", Colors.DIM))
        await runtime.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
