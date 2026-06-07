"""Instruction Plugin — loads instruction files for OpenCode.

Scans the working directory for standard instruction files and injects
their content into the system prompt.

Search order:
1. AGENTS.md — OpenCode standard
2. CLAUDE.md — Claude Code standard
3. .cursorrules — Cursor standard
4. .opencode.md — OpenCode exclusive
5. .opencode/instructions.md — directory form
6. ~/.opencode/global-instructions.md — global
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.models.base import LLMMessage
from agentcore.plugins.base import Plugin, PluginContext

# Instruction file names to search (in priority order)
INSTRUCTION_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".opencode.md",
    ".opencode/instructions.md",
]


class InstructionPlugin(Plugin):
    """Loads instruction files and injects them into context.

    Hooks:
        POST_BUILD_MESSAGES (priority=100) — inject instruction content

    Tools:
        reload_instructions() — force re-scan instruction files
        list_instructions() — show loaded instruction files
    """

    @property
    def name(self) -> str:
        return "instructions"

    @property
    def version(self) -> str:
        return "0.2.0"

    @property
    def description(self) -> str:
        return "Loads AGENTS.md, CLAUDE.md, .cursorrules instruction files (with hot-reload)"

    def __init__(self, work_dir: str = ".") -> None:
        self._work_dir = work_dir
        self._loaded_files: dict[str, str] = {}  # path -> content
        self._file_mtimes: dict[str, float] = {}  # path -> last mtime
        self._global_path = Path.home() / ".opencode" / "global-instructions.md"

    async def setup(self, ctx: PluginContext) -> None:
        # Initial scan
        self._scan_files()

        # Register hook to inject instructions (re-scans on each call)
        ctx.register_hook(HookName.POST_BUILD_MESSAGES, self._inject_instructions, priority=100)

        # Register tools
        ctx.register_tool(
            "reload_instructions",
            self._tool_reload,
            description="Re-scan instruction files from disk",
            parameters={"type": "object", "properties": {}},
        )
        ctx.register_tool(
            "list_instructions",
            self._tool_list,
            description="List loaded instruction files",
            parameters={"type": "object", "properties": {}},
        )

    def _scan_files(self) -> None:
        """Scan for instruction files in working directory and global path."""
        self._loaded_files.clear()
        self._file_mtimes.clear()
        work = Path(self._work_dir).resolve()

        # Local instruction files
        for name in INSTRUCTION_FILES:
            path = work / name
            if path.exists() and path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        self._loaded_files[str(path)] = content
                        self._file_mtimes[str(path)] = path.stat().st_mtime
                except (OSError, PermissionError):
                    pass

        # Global instructions
        if self._global_path.exists() and self._global_path.is_file():
            try:
                content = self._global_path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    self._loaded_files[str(self._global_path)] = content
                    self._file_mtimes[str(self._global_path)] = self._global_path.stat().st_mtime
            except (OSError, PermissionError):
                pass

    def _check_and_reload(self) -> bool:
        """Check if any instruction files changed and reload if needed.

        Returns True if files were reloaded.
        """
        work = Path(self._work_dir).resolve()
        changed = False

        # Check existing files for changes
        for name in INSTRUCTION_FILES:
            path = work / name
            path_str = str(path)
            if path.exists() and path.is_file():
                try:
                    current_mtime = path.stat().st_mtime
                    if path_str not in self._file_mtimes or self._file_mtimes[path_str] != current_mtime:
                        content = path.read_text(encoding="utf-8", errors="replace").strip()
                        if content:
                            self._loaded_files[path_str] = content
                            self._file_mtimes[path_str] = current_mtime
                            changed = True
                        elif path_str in self._loaded_files:
                            del self._loaded_files[path_str]
                            del self._file_mtimes[path_str]
                            changed = True
                except (OSError, PermissionError):
                    pass
            elif path_str in self._loaded_files:
                # File was deleted
                del self._loaded_files[path_str]
                del self._file_mtimes[path_str]
                changed = True

        # Check global instructions
        if self._global_path.exists() and self._global_path.is_file():
            path_str = str(self._global_path)
            try:
                current_mtime = self._global_path.stat().st_mtime
                if path_str not in self._file_mtimes or self._file_mtimes[path_str] != current_mtime:
                    content = self._global_path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        self._loaded_files[path_str] = content
                        self._file_mtimes[path_str] = current_mtime
                        changed = True
            except (OSError, PermissionError):
                pass

        return changed

    async def _inject_instructions(self, ctx: HookContext) -> None:
        """Inject instruction file content as a system message.

        Re-scans files on each call to detect changes (hot-reload).
        """
        # Hot-reload: check for file changes
        self._check_and_reload()

        if not self._loaded_files or not ctx.messages:
            return

        parts = []
        for path, content in self._loaded_files.items():
            name = Path(path).name
            parts.append(f"## {name}\n\n{content}")

        instruction_text = "\n\n---\n\n".join(parts)
        instruction_msg = LLMMessage(
            role="system",
            content=f"[Project Instructions]\n\n{instruction_text}",
        )

        # Insert before the last user message
        insert_pos = len(ctx.messages) - 1
        ctx.messages.insert(insert_pos, instruction_msg)

    async def _tool_reload(self) -> dict[str, Any]:
        old_count = len(self._loaded_files)
        self._scan_files()
        new_count = len(self._loaded_files)
        return {"output": f"Reloaded instructions: {new_count} files (was {old_count})"}

    async def _tool_list(self) -> dict[str, Any]:
        if not self._loaded_files:
            return {"output": "No instruction files loaded"}

        lines = []
        for path, content in self._loaded_files.items():
            size = len(content)
            line_count = content.count("\n") + 1
            mtime = self._file_mtimes.get(path, 0)
            lines.append(f"  {path} ({line_count} lines, {size} chars, mtime={mtime:.0f})")
        return {"output": f"Loaded {len(self._loaded_files)} instruction files:\n" + "\n".join(lines)}
