"""Edit Plugin — 9-replacer fuzzy edit chain + apply_patch for OpenCode.

Provides advanced file editing with a 9-strategy fuzzy matching chain,
per-file concurrency locks, BOM/line-ending preservation, and multi-file
unified diff patch application.
"""

from __future__ import annotations

import asyncio
import re
import textwrap
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


# Per-file locks to prevent concurrent edits
_file_locks: dict[str, asyncio.Lock] = {}


def _get_lock(path: str) -> asyncio.Lock:
    normalized = str(Path(path).resolve())
    if normalized not in _file_locks:
        _file_locks[normalized] = asyncio.Lock()
    return _file_locks[normalized]


# BOM detection
_BOM = "﻿"


def _detect_bom(content: str) -> str:
    return _BOM if content.startswith(_BOM) else ""


def _detect_newline(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    return "\n"


def _strip_bom(content: str) -> str:
    return content.lstrip(_BOM)


class EditPlugin(Plugin):
    """Advanced file editing with 9-replacer fuzzy matching.

    Tools:
        edit_file(path, old_string, new_string, replace_all?) — 9-replacer edit
        apply_patch(patch_text, base_dir?) — apply unified diff patch
    """

    @property
    def name(self) -> str:
        return "edit"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "9-replacer fuzzy edit chain and unified diff patch"

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(
            "edit_file",
            self._edit_file,
            description=(
                "Edit a file by replacing old_string with new_string. "
                "Uses a 9-strategy fuzzy matching chain: exact → trimmed → "
                "anchor → normalized → indent-flexible → escape-normalized → "
                "multi-occurrence → boundary-trimmed → context-aware."
            ),
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
            "apply_patch",
            self._apply_patch,
            description="Apply a multi-file unified diff patch",
            parameters={
                "type": "object",
                "properties": {
                    "patch_text": {"type": "string", "description": "Unified diff format patch text"},
                    "base_dir": {"type": "string", "description": "Base directory for relative paths (default: current dir)"},
                },
                "required": ["patch_text"],
            },
        )

    # --- edit_file ---

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

        lock = _get_lock(path)
        async with lock:
            try:
                raw = p.read_bytes()
            except (OSError, PermissionError) as e:
                return {"output": f"Cannot read file: {e}", "error": f"Cannot read file: {e}"}

            # Detect encoding features
            bom = _detect_bom(raw.decode("utf-8", errors="replace"))
            content = _strip_bom(raw.decode("utf-8", errors="replace"))
            newline = _detect_newline(content)

            # Normalize old/new to \n for matching
            old_norm = old_string.replace("\r\n", "\n")
            new_norm = new_string.replace("\r\n", "\n")

            # Run 9-replacer chain
            result = self._nine_replace(content, old_norm, new_norm, replace_all)

            if result is None:
                return {
                    "output": f"old_string not found in {path}. Ensure the text matches exactly.",
                    "error": f"old_string not found in {path}",
                }

            new_content, count = result
            if count == 0:
                return {"output": f"No changes made to {path}", "error": "No matches found"}

            # Restore original newline style
            if newline == "\r\n":
                new_content = new_content.replace("\n", "\r\n")

            # Restore BOM
            output = bom + new_content

            try:
                p.write_text(output, encoding="utf-8")
            except (OSError, PermissionError) as e:
                return {"output": f"Cannot write file: {e}", "error": f"Cannot write file: {e}"}

        return {"output": f"Edited {path}: {count} replacement(s)"}

    @staticmethod
    def _nine_replace(
        content: str, old: str, new: str, replace_all: bool,
    ) -> tuple[str, int] | None:
        """9-strategy fuzzy replacement chain."""

        # Strategy 1: Exact match
        if old in content:
            if replace_all:
                count = content.count(old)
                return content.replace(old, new), count
            return content.replace(old, new, 1), 1

        # Strategy 2: Line-trimmed (strip trailing whitespace per line)
        old_lines = old.splitlines()
        content_lines = content.splitlines()
        if len(old_lines) > 1:
            old_trimmed = "\n".join(l.rstrip() for l in old_lines)
            content_trimmed = "\n".join(l.rstrip() for l in content_lines)
            if old_trimmed in content_trimmed:
                idx = content_trimmed.find(old_trimmed)
                # Map back to original positions
                orig_start = EditPlugin._map_trimmed_to_orig(content, content_trimmed, idx)
                orig_end = EditPlugin._map_trimmed_to_orig(content, content_trimmed, idx + len(old_trimmed))
                if orig_start is not None and orig_end is not None:
                    return content[:orig_start] + new + content[orig_end:], 1

        # Strategy 3: Block anchor match (first and last non-empty lines)
        old_non_empty = [l for l in old_lines if l.strip()]
        if len(old_non_empty) >= 2:
            first_line = old_non_empty[0].strip()
            last_line = old_non_empty[-1].strip()
            # Find first line in content
            for ci, cline in enumerate(content_lines):
                if first_line in cline.strip():
                    # Check if last line matches nearby
                    for cj in range(ci + 1, min(ci + len(old_lines) + 3, len(content_lines))):
                        if last_line in content_lines[cj].strip():
                            block = "\n".join(content_lines[ci:cj + 1])
                            block_trimmed = "\n".join(l.rstrip() for l in block.splitlines())
                            old_trimmed_block = "\n".join(l.rstrip() for l in old_lines)
                            # Check if blocks are similar enough
                            if EditPlugin._similarity(block_trimmed, old_trimmed_block) > 0.7:
                                new_block = new
                                result_lines = content_lines[:ci] + new_block.splitlines() + content_lines[cj + 1:]
                                return "\n".join(result_lines), 1

        # Strategy 4: Normalized whitespace
        def norm_ws(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip()

        if norm_ws(old) in norm_ws(content):
            pattern = re.escape(old).replace(r"\ ", r"\s+").replace(r"\n", r"\s*\n\s*")
            try:
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    if replace_all:
                        return re.subn(pattern, new.replace("\\", "\\\\"), content, flags=re.DOTALL)
                    return content[:match.start()] + new + content[match.end():], 1
            except re.error:
                pass

        # Strategy 5: Indentation-flexible
        old_dedent = textwrap.dedent(old)
        content_dedent = textwrap.dedent(content)
        if old_dedent != old and old_dedent in content_dedent:
            idx = content_dedent.find(old_dedent)
            return content_dedent[:idx] + new + content_dedent[idx + len(old_dedent):], 1

        # Strategy 6: Escape-normalized (\r\n → \n, \t → spaces)
        old_esc = old.replace("\r\n", "\n").replace("\t", "    ")
        content_esc = content.replace("\r\n", "\n").replace("\t", "    ")
        if old_esc in content_esc:
            idx = content_esc.find(old_esc)
            # Find corresponding position in original
            orig_start = EditPlugin._map_normalized_to_orig(content, content_esc, idx)
            orig_end = EditPlugin._map_normalized_to_orig(content, content_esc, idx + len(old_esc))
            if orig_start is not None and orig_end is not None:
                if replace_all:
                    # For replace_all with escape normalization, use regex
                    pattern = re.escape(old_esc).replace(r"\n", r"[\r\n]").replace(r"\t", r"[\t ]")
                    try:
                        return re.subn(pattern, new.replace("\\", "\\\\"), content_esc)
                    except re.error:
                        pass
                return content[:orig_start] + new + content[orig_end:], 1

        # Strategy 7: Multi-occurrence handling
        # If old appears multiple times with minor variations, find closest match
        old_stripped = old.strip()
        if old_stripped:
            occurrences = []
            search_from = 0
            while True:
                idx = content.find(old_stripped[0], search_from)
                if idx < 0:
                    break
                # Check if enough context matches
                end = min(idx + len(old), len(content))
                candidate = content[idx:end]
                sim = EditPlugin._similarity(candidate, old)
                if sim > 0.8:
                    occurrences.append((idx, sim))
                search_from = idx + 1

            if len(occurrences) == 1:
                idx, _ = occurrences[0]
                return content[:idx] + new + content[idx + len(old):], 1
            elif len(occurrences) > 1 and replace_all:
                # Replace all close matches
                result = content
                offset = 0
                for idx, _ in occurrences:
                    adj_idx = idx + offset
                    result = result[:adj_idx] + new + result[adj_idx + len(old):]
                    offset += len(new) - len(old)
                return result, len(occurrences)

        # Strategy 8: Boundary trimming (try old with leading/trailing whitespace trimmed)
        old_bounded = old.strip()
        if old_bounded and old_bounded != old:
            # Try to find with flexible boundaries
            pattern = r"\s*" + re.escape(old_bounded) + r"\s*"
            try:
                match = re.search(pattern, content)
                if match:
                    return content[:match.start()] + new + content[match.end():], 1
            except re.error:
                pass

        # Strategy 9: Context-aware (use non-empty lines as anchors)
        context_lines = [l.strip() for l in old_lines if l.strip() and len(l.strip()) > 5]
        if len(context_lines) >= 2:
            first_ctx = context_lines[0]
            last_ctx = context_lines[-1]
            for ci, cline in enumerate(content_lines):
                if first_ctx in cline:
                    for cj in range(ci, min(ci + len(old_lines) + 5, len(content_lines))):
                        if last_ctx in content_lines[cj]:
                            block = "\n".join(content_lines[ci:cj + 1])
                            if EditPlugin._similarity(block, old) > 0.6:
                                result_lines = content_lines[:ci] + new.splitlines() + content_lines[cj + 1:]
                                return "\n".join(result_lines), 1

        return None

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Simple character-level similarity (0.0 to 1.0)."""
        if not a or not b:
            return 0.0
        # Use longest common subsequence ratio
        len_a, len_b = len(a), len(b)
        if len_a > len_b:
            a, b = b, a
            len_a, len_b = len_b, len_a

        # Quick check
        if a in b or b in a:
            return 0.9

        # Character set overlap
        set_a = set(a)
        set_b = set(b)
        if not set_a or not set_b:
            return 0.0
        overlap = len(set_a & set_b) / max(len(set_a), len(set_b))
        return overlap

    @staticmethod
    def _map_trimmed_to_orig(original: str, trimmed: str, trimmed_idx: int) -> int | None:
        """Map a position in trimmed text back to original text."""
        if trimmed_idx < 0 or trimmed_idx > len(trimmed):
            return None
        orig_idx = 0
        trim_idx = 0
        while trim_idx < trimmed_idx and orig_idx < len(original):
            if original[orig_idx] == trimmed[trim_idx]:
                trim_idx += 1
            orig_idx += 1
        return orig_idx if trim_idx == trimmed_idx else None

    @staticmethod
    def _map_normalized_to_orig(original: str, normalized: str, norm_idx: int) -> int | None:
        """Map position from normalized text back to original."""
        if norm_idx < 0 or norm_idx > len(normalized):
            return None
        orig_idx = 0
        norm_cur = 0
        while norm_cur < norm_idx and orig_idx < len(original):
            # Skip \r in original when matching \n in normalized
            if orig_idx < len(original) and original[orig_idx] == '\r' and norm_cur < len(normalized) and normalized[norm_cur] == '\n':
                orig_idx += 1
                continue
            orig_idx += 1
            norm_cur += 1
        return orig_idx if norm_cur == norm_idx else None

    # --- apply_patch ---

    async def _apply_patch(
        self, patch_text: str, base_dir: str = ".",
    ) -> dict[str, Any]:
        base = Path(base_dir).resolve()
        hunks = self._parse_unified_diff(patch_text)

        if not hunks:
            return {"output": "No valid hunks found in patch", "error": "Invalid patch format"}

        results = []
        for hunk in hunks:
            file_path = base / hunk["file"]
            if not file_path.exists():
                results.append(f"SKIP {hunk['file']}: file not found")
                continue

            lock = _get_lock(str(file_path))
            async with lock:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    results.append(f"ERROR {hunk['file']}: {e}")
                    continue

                new_content = self._apply_hunk(content, hunk)
                if new_content is not None:
                    file_path.write_text(new_content, encoding="utf-8")
                    results.append(f"OK {hunk['file']}")
                else:
                    results.append(f"FAIL {hunk['file']}: hunk did not apply")

        return {"output": "\n".join(results)}

    @staticmethod
    def _parse_unified_diff(text: str) -> list[dict[str, Any]]:
        """Parse unified diff format into hunks."""
        hunks = []
        current_file = None
        current_lines = []

        for line in text.splitlines():
            # File header: --- a/path or +++ b/path
            if line.startswith("--- "):
                if current_file and current_lines:
                    hunks.append({"file": current_file, "lines": current_lines})
                current_file = line[4:].strip()
                if current_file.startswith("a/"):
                    current_file = current_file[2:]
                current_lines = []
            elif line.startswith("+++ "):
                # Use +++ line as authoritative file path
                fp = line[4:].strip()
                if fp.startswith("b/"):
                    fp = fp[2:]
                current_file = fp
            elif line.startswith("@@"):
                current_lines.append(line)
            elif current_file:
                current_lines.append(line)

        if current_file and current_lines:
            hunks.append({"file": current_file, "lines": current_lines})

        return hunks

    @staticmethod
    def _apply_hunk(content: str, hunk: dict[str, Any]) -> str | None:
        """Apply a single file's hunks to content."""
        lines = content.splitlines()
        result = []
        line_idx = 0

        for hunk_line in hunk["lines"]:
            if hunk_line.startswith("@@"):
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hunk_line)
                if match:
                    old_start = int(match.group(1)) - 1  # 0-indexed
                    # Copy lines before hunk
                    while line_idx < old_start and line_idx < len(lines):
                        result.append(lines[line_idx])
                        line_idx += 1
                continue

            if hunk_line.startswith("-"):
                # Line removed — skip from original
                if line_idx < len(lines):
                    line_idx += 1
                continue

            if hunk_line.startswith("+"):
                # Line added
                result.append(hunk_line[1:])
                continue

            # Context line (space prefix) — must match
            if hunk_line.startswith(" "):
                hunk_line = hunk_line[1:]
            if line_idx < len(lines) and lines[line_idx] == hunk_line:
                result.append(lines[line_idx])
                line_idx += 1
            elif line_idx < len(lines):
                # Context mismatch — hunk doesn't apply cleanly
                return None

        # Append remaining lines
        while line_idx < len(lines):
            result.append(lines[line_idx])
            line_idx += 1

        return "\n".join(result)
