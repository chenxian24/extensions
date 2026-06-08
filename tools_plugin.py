"""Tools Plugin — native file operation tools.

Provides glob_files, grep_files, and list_directory
as pure Python tools registered via PluginContext.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


class ToolsPlugin(Plugin):
    """Native file operation tools.

    Tools:
        glob_files(pattern, path?) — find files by glob pattern
        grep_files(pattern, path?, glob?) — search file contents with regex
        list_directory(path?) — list directory contents
    """

    @property
    def name(self) -> str:
        return "tools"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Native file operation tools (glob, grep, list)"

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(
            "glob_files",
            self._glob_files,
            description="Find files matching a glob pattern. Returns sorted file paths.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"},
                    "path": {"type": "string", "description": "Directory to search in (default: current directory)"},
                },
                "required": ["pattern"],
            },
        )
        ctx.register_tool(
            "grep_files",
            self._grep_files,
            description="Search file contents using a regex pattern. Returns matching lines with file paths and line numbers.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search in (default: current directory)"},
                    "glob": {"type": "string", "description": "Glob filter for files (e.g. '*.py', '*.{ts,tsx}')"},
                    "case_insensitive": {"type": "boolean", "description": "Case insensitive search (default: false)"},
                    "max_results": {"type": "integer", "description": "Maximum results to return (default: 100)"},
                },
                "required": ["pattern"],
            },
        )
        ctx.register_tool(
            "list_directory",
            self._list_directory,
            description="List files and directories at a given path. Returns entries with type indicators.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
                    "show_hidden": {"type": "boolean", "description": "Include hidden files (default: false)"},
                },
            },
        )

    # --- Tool implementations ---

    async def _glob_files(self, pattern: str, path: str = ".") -> dict[str, Any]:
        root = Path(path).resolve()
        if not root.exists():
            return {"output": f"Path does not exist: {path}", "error": f"Path does not exist: {path}"}

        matches = []
        for match in root.glob(pattern):
            if match.is_file():
                try:
                    rel = match.relative_to(root)
                except ValueError:
                    rel = match
                matches.append(str(rel))

        matches.sort()
        if not matches:
            return {"output": f"No files matching '{pattern}' in {path}"}

        if len(matches) > 200:
            matches = matches[:200]
            matches.append(f"... and more (showing first 200)")

        return {"output": "\n".join(matches)}

    async def _grep_files(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "*",
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> dict[str, Any]:
        root = Path(path).resolve()
        if not root.exists():
            return {"output": f"Path does not exist: {path}", "error": f"Path does not exist: {path}"}

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"output": f"Invalid regex: {e}", "error": f"Invalid regex: {e}"}

        matches = []
        search_path = root if root.is_dir() else root.parent
        file_pattern = root.name if root.is_file() else glob

        for file_path in search_path.rglob(file_pattern):
            if not file_path.is_file():
                continue
            # Skip binary files and common non-text extensions
            if file_path.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".jpg", ".png", ".gif", ".zip", ".tar", ".gz"):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, PermissionError):
                continue

            for line_num, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    try:
                        rel = file_path.relative_to(root)
                    except ValueError:
                        rel = file_path
                    matches.append(f"{rel}:{line_num}: {line.rstrip()[:200]}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        if not matches:
            return {"output": f"No matches for '{pattern}' in {path}"}

        return {"output": "\n".join(matches)}

    async def _list_directory(
        self,
        path: str = ".",
        show_hidden: bool = False,
    ) -> dict[str, Any]:
        root = Path(path).resolve()
        if not root.exists():
            return {"output": f"Path does not exist: {path}", "error": f"Path does not exist: {path}"}
        if not root.is_dir():
            return {"output": f"Not a directory: {path}", "error": f"Not a directory: {path}"}

        entries = []

        for item in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if not show_hidden and item.name.startswith("."):
                continue
            prefix = "[dir] " if item.is_dir() else "[file]"
            try:
                size = item.stat().st_size if item.is_file() else ""
                size_str = f" ({self._format_size(size)})" if size else ""
            except OSError:
                size_str = ""
            entries.append(f"{prefix} {item.name}{size_str}")

        if not entries:
            return {"output": f"Directory is empty: {path}"}

        if len(entries) > 200:
            entries = entries[:200]
            entries.append(f"... and more (showing first 200)")

        return {"output": "\n".join(entries)}

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"


def extract_shell_paths(command: str) -> list[str]:
    """Extract file paths from a shell command string.

    Parses common command patterns to find file path arguments:
    - cat/vim/less/head/tail/wc/diff file.py
    - python/node/bash script.sh
    - cp/mv src dst
    - redirect targets (> file, >> file)
    - pipe chains (each segment)

    Returns a list of extracted path strings (may include false positives).
    """
    paths: list[str] = []
    parts = command.split()

    if not parts:
        return paths

    # Known commands and their path argument positions
    path_commands = {
        "cat": [0], "less": [0], "head": [0], "tail": [0],
        "wc": [0], "diff": [0, 1], "vim": [0], "vi": [0],
        "nano": [0], "emacs": [0],
        "python": [0], "python3": [0], "node": [0],
        "bash": [0], "sh": [0], "zsh": [0],
        "cp": [0, 1], "mv": [0, 1], "ln": [0, 1],
        "rm": [0], "mkdir": [0], "rmdir": [0],
        "touch": [0], "chmod": [1], "chown": [1],
        "tar": [],  # tar has complex arg parsing
        "git": [],  # git subcommands make this complex
    }

    cmd = parts[0]
    if cmd in path_commands:
        for idx in path_commands[cmd]:
            if idx + 1 < len(parts):
                arg = parts[idx + 1]
                if not arg.startswith("-"):
                    paths.append(arg)

    # Check for redirect targets
    for i, part in enumerate(parts):
        if part in (">", ">>") and i + 1 < len(parts):
            paths.append(parts[i + 1])

    # Pipe chains
    if "|" in command:
        pipe_parts = command.split("|")
        for pipe_part in pipe_parts[1:]:
            paths.extend(extract_shell_paths(pipe_part.strip()))

    return paths
