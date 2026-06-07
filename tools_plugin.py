"""Tools Plugin — native file operation tools for Hermes.

Provides glob_files, grep_files, edit_file, and list_directory
as pure Python tools registered via PluginContext.
"""

from __future__ import annotations

import fnmatch
import os
import re
import textwrap
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


class ToolsPlugin(Plugin):
    """Native file operation tools.

    Tools:
        glob_files(pattern, path?) — find files by glob pattern
        grep_files(pattern, path?, glob?) — search file contents with regex
        edit_file(path, old_string, new_string, replace_all?) — fuzzy file editing
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
        return "Native file operation tools (glob, grep, edit, list)"

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
            "edit_file",
            self._edit_file,
            description="Edit a file by replacing old_string with new_string. Uses fuzzy matching: exact → trimmed → normalized whitespace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Text to find and replace"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
                },
                "required": ["path", "old_string", "new_string"],
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
                    matches.append(str(rel))
                except ValueError:
                    matches.append(str(match))
            if len(matches) >= 500:
                break

        matches.sort()
        if not matches:
            return {"output": f"No files matching '{pattern}' in {path}"}
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

        matches: list[str] = []
        files_to_search: list[Path] = []

        if root.is_file():
            files_to_search.append(root)
        else:
            for f in root.rglob("*"):
                if f.is_file() and fnmatch.fnmatch(f.name, glob):
                    files_to_search.append(f)

        for fpath in files_to_search:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            for line_num, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    try:
                        rel = fpath.relative_to(root)
                        matches.append(f"{rel}:{line_num}: {line.rstrip()}")
                    except ValueError:
                        matches.append(f"{fpath}:{line_num}: {line.rstrip()}")
                    if len(matches) >= max_results:
                        return {"output": "\n".join(matches) + f"\n[truncated at {max_results} matches]"}

        if not matches:
            return {"output": f"No matches for '{pattern}' in {path}"}
        return {"output": "\n".join(matches)}

    async def _edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        p = Path(path).resolve()
        if not p.exists():
            return {"output": f"File does not exist: {path}", "error": f"File does not exist: {path}"}
        if not p.is_file():
            return {"output": f"Not a file: {path}", "error": f"Not a file: {path}"}

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as e:
            return {"output": f"Cannot read file: {e}", "error": f"Cannot read file: {e}"}

        # Try matching strategies (simplified 9-replacer chain)
        result = self._fuzzy_replace(content, old_string, new_string, replace_all)

        if result is None:
            # Show context around potential near-matches for debugging
            return {
                "output": f"old_string not found in {path}. Ensure the text matches exactly.",
                "error": f"old_string not found in {path}",
            }

        new_content, count = result
        if count == 0:
            return {"output": f"No changes made to {path}", "error": "No matches found"}

        try:
            p.write_text(new_content, encoding="utf-8")
        except (OSError, PermissionError) as e:
            return {"output": f"Cannot write file: {e}", "error": f"Cannot write file: {e}"}

        return {"output": f"Edited {path}: {count} replacement(s)"}

    @staticmethod
    def _fuzzy_replace(
        content: str,
        old: str,
        new: str,
        replace_all: bool,
    ) -> tuple[str, int] | None:
        """Try multiple matching strategies to find and replace text.

        Returns (new_content, count) or None if no match found.
        """
        # Strategy 1: Exact match
        if old in content:
            if replace_all:
                return content.replace(old, new), content.count(old)
            return content.replace(old, new, 1), 1

        # Strategy 2: Line-trimmed match (strip trailing whitespace)
        old_lines = old.splitlines()
        content_lines = content.splitlines()
        if len(old_lines) > 1:
            old_trimmed = "\n".join(l.rstrip() for l in old_lines)
            content_trimmed = "\n".join(l.rstrip() for l in content_lines)
            if old_trimmed in content_trimmed:
                # Find position in trimmed, apply to original
                idx = content_trimmed.find(old_trimmed)
                if idx >= 0:
                    # Map back to original content positions
                    orig_idx = 0
                    trim_idx = 0
                    while trim_idx < idx and orig_idx < len(content):
                        if content[orig_idx] == content_trimmed[trim_idx]:
                            trim_idx += 1
                        orig_idx += 1
                    # Find end position
                    end_trim = idx + len(old_trimmed)
                    orig_end = orig_idx
                    trim_cur = trim_idx
                    while trim_cur < end_trim and orig_end < len(content):
                        if content[orig_end] == content_trimmed[trim_cur]:
                            trim_cur += 1
                        orig_end += 1
                    return content[:orig_idx] + new + content[orig_end:], 1

        # Strategy 3: Normalized whitespace match
        def normalize_ws(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip()

        old_norm = normalize_ws(old)
        content_norm = normalize_ws(content)
        if old_norm in content_norm:
            # Find approximate position using normalized form
            norm_idx = content_norm.find(old_norm)
            # Use regex to match with flexible whitespace
            pattern = re.escape(old).replace(r"\ ", r"\s+").replace(r"\n", r"\s*\n\s*")
            try:
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    if replace_all:
                        return re.subn(pattern, new.replace("\\", "\\\\"), content, flags=re.DOTALL)
                    return content[:match.start()] + new + content[match.end():], 1
            except re.error:
                pass

        # Strategy 4: Indentation-flexible match
        old_dedent = textwrap.dedent(old) if old.startswith((" ", "\t")) else old
        content_dedent = textwrap.dedent(content) if content.startswith((" ", "\t")) else content
        if old_dedent in content_dedent:
            idx = content_dedent.find(old_dedent)
            return content_dedent[:idx] + new + content_dedent[idx + len(old_dedent):], 1

        return None

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
        try:
            items = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError as e:
            return {"output": f"Permission denied: {e}", "error": f"Permission denied: {e}"}

        for item in items:
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

        # Truncate if too many entries
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
    import shlex

    paths: list[str] = []

    # Split command by pipes and semicolons first
    segments = re.split(r'[|;]', command)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Handle redirects: > file, >> file, 2> file, &> file
        redirect_matches = re.findall(r'[\d&]*>\s*(\S+)', segment)
        for rm in redirect_matches:
            if not rm.startswith('/') and rm not in ('/dev/null', '/dev/stdout', '/dev/stderr'):
                paths.append(rm)

        # Remove redirect parts for further parsing
        cleaned = re.sub(r'[\d&]*>\s*\S+', '', segment).strip()

        try:
            tokens = shlex.split(cleaned)
        except ValueError:
            # Fallback: simple split
            tokens = cleaned.split()

        if not tokens:
            continue

        cmd = tokens[0]

        # Commands where all non-flag args are file paths
        file_commands = {
            'cat', 'less', 'more', 'head', 'tail', 'wc', 'diff', 'chmod', 'chown',
            'rm', 'unlink', 'ln', 'readlink', 'file', 'stat', 'touch', 'mkdir',
            'rmdir', 'ls', 'du', 'df', 'find', 'xargs',
        }

        # Commands where specific positional args are files
        exec_commands = {'python', 'python3', 'node', 'bash', 'sh', 'zsh', 'ruby', 'perl'}

        # Commands with src dst pattern
        copy_commands = {'cp', 'mv', 'rename'}

        if cmd in file_commands:
            skip_next = False
            for tok in tokens[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if tok.startswith('-'):
                    # Flags like -n, --count that take a value
                    if tok in ('-n', '-c', '-m', '--lines', '--count', '--max-count',
                               '-d', '--directory', '-C', '--context', '-B', '--before',
                               '-A', '--after'):
                        skip_next = True
                    continue
                paths.append(tok)
        elif cmd in exec_commands:
            # First non-flag arg is the script
            for tok in tokens[1:]:
                if tok.startswith('-'):
                    continue
                paths.append(tok)
                break
        elif cmd in copy_commands:
            # All non-flag args are paths
            for tok in tokens[1:]:
                if tok.startswith('-'):
                    continue
                paths.append(tok)
        else:
            # Generic: look for tokens that look like file paths
            for tok in tokens[1:]:
                if tok.startswith('-'):
                    continue
                # Heuristic: contains path separator or file extension
                if '/' in tok or '\\' in tok or '.' in tok:
                    paths.append(tok)

    # Filter out obvious non-paths
    result = []
    for p in paths:
        p = p.strip("'\"")
        if not p or p == '-':
            continue
        # Skip URLs
        if p.startswith(('http://', 'https://', 'ftp://')):
            continue
        result.append(p)

    return result
