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
from agentcore.context.engine import ContextEngine
from agentcore.core.engine import AgentEngine
from agentcore.core.message import Message, MessageRole
from agentcore.events.bus import EventBus
from agentcore.hooks.manager import HookManager
from agentcore.hooks.types import HookContext, HookName
from agentcore.models.base import ChatParams, LLMMessage, ToolCall
from agentcore.plugins.manager import PluginManager
from agentcore.tools.registry import ToolRegistry
from agentcore.prompts.builder import DynamicPromptBuilder

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


def _message_to_llm(msg: Message) -> LLMMessage:
    """Convert a Message to LLMMessage for provider calls."""
    return LLMMessage(
        role=msg.role.value,
        content=msg.content,
        name=msg.name,
        tool_call_id=msg.tool_call_id,
        tool_calls=msg.tool_calls,
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

    # --- Mechanism layer ---
    hooks = HookManager()
    tools = ToolRegistry()
    context = ContextEngine()
    events = EventBus()

    # --- Plugin layer ---
    pm = PluginManager(
        config=config,
        hooks=hooks,
        tools=tools,
        context=context,
        events=events,
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
    await pm.initialize_all()

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
        tool_registry=tools,
        base_prompt=config.system_prompt.template,
        identity=HERMES_IDENTITY,
        extra_sections=extra_sections if extra_sections else None,
    )
    config.system_prompt = SystemPromptConfig(template=prompt_builder.build())
    engine.configure(config)

    # --- Session persistence ---
    session_dir = Path(hermes_config.get("session_dir", Path.home() / ".hermes" / "sessions"))
    session_dir.mkdir(parents=True, exist_ok=True)
    session_name = f"session-{int(time.time())}"
    session = engine.create_session(session_name)

    # Dispatch SESSION_START hook
    await hooks.dispatch(HookContext(
        name=HookName.SESSION_START,
        session=session,
        engine=engine,
        hooks=hooks, tools=tools, context=context, events=events,
    ))

    # --- Streaming flag ---
    use_streaming = hermes_config.get("streaming", True)

    # --- Tool executor with approval hook integration ---
    async def _tool_executor(tc: ToolCall) -> dict:
        ctx = HookContext(name=HookName.PRE_TOOL_CALL, tool_call=tc)
        await hooks.dispatch(ctx)
        if ctx.cancel:
            return {"success": False, "output": None, "error": ctx.metadata.get("cancel_reason", "Denied")}
        result = await tools.execute(tc)
        post_ctx = HookContext(name=HookName.POST_TOOL_CALL, tool_call=tc, tool_result=result)
        await hooks.dispatch(post_ctx)

        # TRANSFORM_TERMINAL_OUTPUT hook — for shell command tools
        if tc.function.name in ("execute_command", "execute_sandboxed"):
            term_ctx = HookContext(
                name=HookName.TRANSFORM_TERMINAL_OUTPUT,
                tool_call=tc,
                tool_result=result,
                metadata={"output": result.get("output", "")},
            )
            await hooks.dispatch(term_ctx)
            if term_ctx.transform_result is not None:
                result["output"] = term_ctx.transform_result

        # TRANSFORM_TOOL_RESULT hook — allow plugins to modify tool output
        transform_ctx = HookContext(
            name=HookName.TRANSFORM_TOOL_RESULT,
            tool_call=tc,
            tool_result=result,
            metadata={"output": result.get("output", "")},
        )
        await hooks.dispatch(transform_ctx)
        if transform_ctx.transform_result is not None:
            result["output"] = transform_ctx.transform_result

        return result

    # --- Tool event callbacks ---
    async def _on_tool_call(tc: ToolCall, _messages: list) -> None:
        print(_color(f"\n  [{tc.function.name}]", Colors.CYAN), flush=True)

    async def _on_tool_result(tc: ToolCall, result: dict, _messages: list) -> None:
        output = result.get("output", "")
        if output:
            display = str(output)[:300]
            if len(str(output)) > 300:
                display += "..."
            print(_color(f"  {display}", Colors.DIM), flush=True)
        return None

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

    # --- Chat function ---
    async def chat(user_input: str) -> str:
        # 1. PRE_BUILD_MESSAGES hook (commands interception)
        pre_ctx = HookContext(
            name=HookName.PRE_BUILD_MESSAGES,
            user_input=user_input,
            session=session,
            engine=engine,
            hooks=hooks,
            tools=tools,
            context=context,
            events=events,
        )
        await hooks.dispatch(pre_ctx)
        if pre_ctx.cancel:
            reason = pre_ctx.metadata.get("cancel_reason", "Cancelled")
            print(_color(reason, Colors.YELLOW))
            return reason

        # 2. Add user message to session
        session.add_message(Message.user(user_input))

        # 3. Context compression
        compressed = context.compress(
            session.messages,
            max_tokens=config.context.max_tokens,
        )

        # 4. Convert to LLMMessage list
        llm_messages = [_message_to_llm(m) for m in compressed]

        # 5. Build params with tool definitions
        params = ChatParams(
            model=config.model.model,
            temperature=config.model.temperature,
            max_tokens=config.model.max_tokens,
            tools=tools.get_tool_definitions(),
        )

        # 6. LLM call — streaming or non-streaming
        original_count = len(llm_messages)
        if use_streaming:
            print(_color("Hermes: ", Colors.GREEN, Colors.BOLD), end="", flush=True)
            response = await engine.stream_with_tools(
                messages=llm_messages,
                params=params,
                tool_executor=_tool_executor,
                max_rounds=config.runtime.max_tool_rounds,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
                on_chunk=lambda chunk: print(chunk, end="", flush=True),
            )
            response_content = response.content
            print()  # newline after stream
        else:
            response = await engine.chat_with_tools(
                messages=llm_messages,
                params=params,
                tool_executor=_tool_executor,
                max_rounds=config.runtime.max_tool_rounds,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
            )
            response_content = response.content

        # 6b. Store intermediate messages (tool calls + results) into session
        for llm_msg in llm_messages[original_count:]:
            session.add_message(Message(
                role=MessageRole(llm_msg.role),
                content=llm_msg.content,
                name=llm_msg.name,
                tool_call_id=llm_msg.tool_call_id,
                tool_calls=llm_msg.tool_calls,
            ))

        # 7. TRANSFORM_LLM_OUTPUT hook — allow plugins to modify LLM response
        llm_ctx = HookContext(
            name=HookName.TRANSFORM_LLM_OUTPUT,
            response=response,
            metadata={"output": response_content},
        )
        await hooks.dispatch(llm_ctx)
        if llm_ctx.transform_result is not None:
            response_content = llm_ctx.transform_result

        # 8. Store assistant response in session
        session.add_message(Message.assistant(response_content))
        return response_content

    # --- CLI loop ---
    skill_count = len(skills)
    tool_count = len(tools.list_names())

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
                response = await chat(user_input)
                if not use_streaming:
                    print(_color("Hermes: ", Colors.GREEN, Colors.BOLD) + response)
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
        # Dispatch SESSION_END hook
        await hooks.dispatch(HookContext(
            name=HookName.SESSION_END,
            session=session,
            engine=engine,
            hooks=hooks, tools=tools, context=context, events=events,
        ))
        _auto_save()
        print(_color(f"Session saved: {session_name}.json", Colors.DIM))
        await pm.shutdown_all()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
