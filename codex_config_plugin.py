"""Codex Config Plugin — loads codex.toml and injects sandbox/approval instructions.

Searches for config in:
1. ~/.codex/config.toml (user-level)
2. .codex/config.toml (project-level, overrides user)

Config fields:
    [model]
    provider = "openai"
    model = "gpt-4o"
    temperature = 0.3
    max_tokens = 16384
    max_rounds = 20

    [sandbox]
    mode = "workspace_write"  # read_only | workspace_write | danger_full_access
    writable_roots = ["."]

    [approval]
    policy = "on_request"  # unless_trusted | on_request | never
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext

from extensions.prompts.codex import (
    APPROVAL_POLICIES,
    CODEX_CODING_GUIDELINES,
    CODEX_ENVIRONMENT_TEMPLATE,
    CODEX_TOOL_USAGE,
    CODEX_WORKFLOW,
    SANDBOX_MODES,
)


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file using tomllib (3.11+) or a minimal fallback parser."""
    if not path.exists():
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (ImportError, ModuleNotFoundError):
        pass
    try:
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except (ImportError, ModuleNotFoundError):
        pass
    # Minimal fallback: parse simple key = "value" lines
    result: dict[str, Any] = {}
    section = result
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                key = line[1:-1]
                result[key] = {}
                section = result[key]
            elif "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v.lower() in ("true", "yes"):
                    section[k] = True
                elif v.lower() in ("false", "no"):
                    section[k] = False
                elif v.isdigit():
                    section[k] = int(v)
                else:
                    try:
                        section[k] = float(v)
                    except ValueError:
                        section[k] = v
    except (OSError, PermissionError):
        pass
    return result


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override wins on conflicts."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class CodexConfigPlugin(Plugin):
    """Loads codex.toml config and injects sandbox/approval instructions into system prompt."""

    @property
    def name(self) -> str:
        return "codex-config"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Codex configuration loader (codex.toml)"

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def sandbox_mode(self) -> str:
        return self._config.get("sandbox", {}).get("mode", "workspace_write")

    @property
    def approval_policy(self) -> str:
        return self._config.get("approval", {}).get("policy", "on_request")

    async def setup(self, ctx: PluginContext) -> None:
        # Load config from user + project
        user_config = _load_toml(Path.home() / ".codex" / "config.toml")
        project_config = _load_toml(Path(".codex") / "config.toml")
        self._config = _deep_merge(user_config, project_config)

        # Store in metadata for other plugins
        ctx.config.metadata["codex"] = self._config

        # Register hook to inject instructions into system prompt
        ctx.register_hook(HookName.POST_BUILD_MESSAGES, self._inject_instructions, priority=200)

        # Register /config command
        ctx.register_command("config", self._show_config, "Show Codex configuration")

    async def _inject_instructions(self, ctx: HookContext) -> None:
        """Inject sandbox + approval + environment instructions into messages."""
        if not ctx.messages:
            return

        from agentcore.models.base import LLMMessage

        sandbox_mode = self.sandbox_mode
        approval_policy = self.approval_policy

        parts = []

        # Sandbox instructions
        sandbox_text = SANDBOX_MODES.get(sandbox_mode, SANDBOX_MODES["workspace_write"])
        parts.append(sandbox_text)

        # Approval instructions
        approval_text = APPROVAL_POLICIES.get(approval_policy, APPROVAL_POLICIES["on_request"])
        parts.append(approval_text)

        # Environment context
        cwd = os.getcwd()
        os_name = platform.system()
        os_info = platform.platform()
        shell = "cmd.exe / PowerShell" if os_name == "Windows" else "bash/sh"

        # Detect project info
        project_info = self._detect_project(cwd)

        env_text = CODEX_ENVIRONMENT_TEMPLATE.format(
            cwd=cwd,
            os=os_info,
            shell=shell,
            project_info=project_info,
        )
        parts.append(env_text)

        # Coding guidelines
        parts.append(CODEX_CODING_GUIDELINES)
        parts.append(CODEX_TOOL_USAGE)
        parts.append(CODEX_WORKFLOW)

        # Inject as system message
        instruction_text = "\n\n".join(parts)
        instruction_msg = LLMMessage(role="system", content=instruction_text)

        # Insert after the first system message (base prompt)
        if ctx.messages and ctx.messages[0].role == "system":
            ctx.messages.insert(1, instruction_msg)
        else:
            ctx.messages.insert(0, instruction_msg)

    def _detect_project(self, cwd: str) -> str:
        """Detect project type and return info string."""
        root = Path(cwd)
        info_parts = []

        if (root / "package.json").exists():
            info_parts.append("Node.js/JavaScript project")
        if (root / "Cargo.toml").exists():
            info_parts.append("Rust project")
        if (root / "go.mod").exists():
            info_parts.append("Go project")
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists() or (root / "Pipfile").exists():
            info_parts.append("Python project")
        if (root / "Makefile").exists():
            info_parts.append("Has Makefile")
        if (root / ".git").exists():
            info_parts.append("Git repository")

        return ", ".join(info_parts) if info_parts else "Unknown project type"

    async def _show_config(self, ctx: HookContext, _args: str = "") -> str:
        """Show current Codex configuration."""
        import json
        return json.dumps(self._config, indent=2, default=str)
