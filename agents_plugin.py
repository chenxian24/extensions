"""Agents Plugin — 8 specialized agents for OpenCode.

Each agent has a focused system prompt and a specific tool subset.
Uses SubAgentManager for task delegation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentcore.agents.manager import SubAgentManager, SubAgentResult, SubAgentTask
from agentcore.models.base import ChatParams, LLMMessage, ToolCall
from agentcore.plugins.base import Plugin, PluginContext


@dataclass
class AgentProfile:
    """Configuration for a specialized agent."""
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    temperature: float = 0.3
    max_tokens: int = 4096


# 8 specialized agents
AGENT_PROFILES: list[AgentProfile] = [
    AgentProfile(
        name="build",
        description="Compile, build, and test code",
        system_prompt=(
            "You are a build agent. Your job is to compile, build, and test code. "
            "Run build commands, report errors clearly, and suggest fixes. "
            "Be concise — only report what's needed."
        ),
        allowed_tools=["execute_command", "read_file"],
    ),
    AgentProfile(
        name="plan",
        description="Analyze tasks and create implementation plans",
        system_prompt=(
            "You are a planning agent. Analyze the task, break it into steps, "
            "identify dependencies and risks. Output a clear, actionable plan. "
            "Read relevant code files to understand context before planning."
        ),
        allowed_tools=["read_file", "glob_files", "grep_files", "list_directory"],
    ),
    AgentProfile(
        name="general",
        description="General-purpose coding tasks",
        system_prompt=(
            "You are a general-purpose coding agent. Handle any coding task: "
            "write code, fix bugs, refactor, add features. Use tools as needed. "
            "Be thorough and test your changes."
        ),
        allowed_tools=[],  # All tools
    ),
    AgentProfile(
        name="explore",
        description="Explore and understand codebases",
        system_prompt=(
            "You are a code exploration agent. Your job is to find and understand "
            "code: locate files, trace function calls, understand architecture. "
            "Report findings clearly with file paths and line numbers."
        ),
        allowed_tools=["read_file", "glob_files", "grep_files", "list_directory"],
    ),
    AgentProfile(
        name="scout",
        description="Quick discovery and search",
        system_prompt=(
            "You are a scout agent. Quickly find files, patterns, and references. "
            "Be fast and precise. Return file paths, line numbers, and brief context. "
            "Don't read full files — just find what's needed."
        ),
        allowed_tools=["glob_files", "grep_files", "list_directory"],
    ),
    AgentProfile(
        name="compaction",
        description="Compress and summarize conversation context",
        system_prompt=(
            "You are a compaction agent. Given a conversation history, "
            "produce a concise summary that preserves all important context: "
            "decisions made, code changes, open issues, and next steps. "
            "Be extremely concise."
        ),
        allowed_tools=[],
    ),
    AgentProfile(
        name="title",
        description="Generate concise titles for conversations",
        system_prompt=(
            "You are a title agent. Given a conversation snippet, "
            "generate a short, descriptive title (under 60 characters). "
            "Output ONLY the title, nothing else."
        ),
        allowed_tools=[],
    ),
    AgentProfile(
        name="summary",
        description="Summarize code files and changes",
        system_prompt=(
            "You are a summary agent. Read code files and produce concise summaries: "
            "what the file does, key functions/classes, dependencies, and notable patterns. "
            "Be structured and brief."
        ),
        allowed_tools=["read_file"],
    ),
]


class AgentsPlugin(Plugin):
    """8 specialized agents for task delegation.

    Tools:
        delegate_task(agent, prompt, context?) — delegate to a specialized agent
        list_agents() — list available agents
    """

    @property
    def name(self) -> str:
        return "agents"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "8 specialized agents for OpenCode (build/plan/explore/scout/etc.)"

    def __init__(self) -> None:
        self._profiles: dict[str, AgentProfile] = {p.name: p for p in AGENT_PROFILES}
        self._manager: SubAgentManager | None = None
        self._engine: Any = None
        self._tool_registry: Any = None

    async def setup(self, ctx: PluginContext) -> None:
        self._engine = ctx.config.metadata.get("_engine")
        self._tool_registry = ctx.tools

        # Create SubAgentManager with a runner that uses the engine
        self._manager = SubAgentManager(agent_runner=self._run_agent)

        ctx.register_tool(
            "delegate_task",
            self._tool_delegate,
            description="Delegate a task to a specialized agent (build/plan/explore/scout/general/compaction/title/summary)",
            parameters={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": f"Agent name: {', '.join(self._profiles.keys())}",
                    },
                    "prompt": {"type": "string", "description": "Task description for the agent"},
                    "context": {"type": "string", "description": "Additional context (file contents, error messages, etc.)"},
                },
                "required": ["agent", "prompt"],
            },
        )
        ctx.register_tool(
            "list_agents",
            self._tool_list,
            description="List all available specialized agents",
            parameters={"type": "object", "properties": {}},
        )

    async def _run_agent(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a sub-agent task using the engine."""
        agent_name = task.metadata.get("agent", "general")
        profile = self._profiles.get(agent_name)
        if not profile:
            return SubAgentResult(task_id=task.task_id, success=False, error=f"Unknown agent: {agent_name}")

        if not self._engine:
            return SubAgentResult(task_id=task.task_id, success=False, error="Engine not available")

        # Build messages
        messages = [
            LLMMessage(role="system", content=profile.system_prompt),
            LLMMessage(role="user", content=task.prompt),
        ]

        # Build tool definitions (subset)
        tools = []
        if self._tool_registry and profile.allowed_tools:
            all_defs = self._tool_registry.get_tool_definitions()
            tools = [td for td in all_defs if td.get("function", {}).get("name", "") in profile.allowed_tools]

        params = ChatParams(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            tools=tools,
        )

        try:
            # Use tool_executor if tools are available
            if tools and self._tool_registry:
                response = await self._engine.chat_with_tools(
                    messages=messages,
                    params=params,
                    tool_executor=self._tool_registry,
                    max_rounds=5,
                )
            else:
                response = await self._engine.chat(messages, params)

            return SubAgentResult(
                task_id=task.task_id,
                success=True,
                output=response.content,
            )
        except Exception as e:
            return SubAgentResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
            )

    async def _tool_delegate(
        self, agent: str, prompt: str, context: str = "",
    ) -> dict[str, Any]:
        if not self._manager:
            return {"output": "Agent system not initialized", "error": "Not initialized"}

        profile = self._profiles.get(agent)
        if not profile:
            return {"output": f"Unknown agent: {agent}. Available: {', '.join(self._profiles.keys())}", "error": "Unknown agent"}

        full_prompt = prompt
        if context:
            full_prompt = f"{prompt}\n\nContext:\n{context}"

        task = SubAgentTask(
            description=f"[{agent}] {prompt[:100]}",
            prompt=full_prompt,
            metadata={"agent": agent},
        )

        result = await self._manager.delegate(task)

        if result.success:
            return {"output": f"[{agent} agent]\n{result.output}"}
        return {"output": f"[{agent} agent] Error: {result.error}", "error": result.error}

    async def _tool_list(self) -> dict[str, Any]:
        lines = []
        for name, profile in self._profiles.items():
            tools_str = ", ".join(profile.allowed_tools) if profile.allowed_tools else "all"
            lines.append(f"  {name:14s} — {profile.description} [tools: {tools_str}]")
        return {"output": "Specialized agents:\n" + "\n".join(lines)}
