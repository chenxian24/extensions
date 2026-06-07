"""Prompt Loader — loads prompt sections from .md files.

Scans a directory for .md files and returns a dict[str, str] mapping
section titles to content. Supports optional YAML frontmatter for
custom titles.

Usage:
    from extensions.prompts.loader import load_prompt_sections

    sections = load_prompt_sections("extensions/prompts/hermes")
    # {"Identity": "...", "Tool Use Enforcement": "...", ...}

File naming convention:
    tool_use_enforcement.md -> "Tool Use Enforcement"
    identity.md -> "Identity"

Override title via frontmatter:
    ---
    title: Custom Section Title
    ---
    Content here...
"""

from __future__ import annotations

import re
from pathlib import Path


def _filename_to_title(stem: str) -> str:
    """Convert a filename stem to a section title.

    tool_use_enforcement -> "Tool Use Enforcement"
    base_prompt -> "Base Prompt"
    identity -> "Identity"
    """
    return stem.replace("_", " ").replace("-", " ").title()


def load_prompt_file(path: Path) -> tuple[str, str] | None:
    """Load a single .md prompt file.

    Returns (title, content) or None if file is empty/unreadable.
    Supports optional YAML frontmatter with a 'title' field.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return None

    text = text.strip()
    if not text:
        return None

    title: str | None = None
    content = text

    # Parse optional YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        content = fm_match.group(2).strip()
        # Extract title field
        for line in fm_text.splitlines():
            if line.strip().startswith("title:"):
                title = line.split(":", 1)[1].strip().strip("\"'")
                break

    if not title:
        title = _filename_to_title(path.stem)

    if not content:
        return None

    return (title, content)


def load_prompt_sections(directory: str | Path) -> dict[str, str]:
    """Load all .md files from a directory into a {title: content} dict.

    Files are loaded in alphabetical order. If a file has YAML frontmatter
    with a 'title' field, that title is used; otherwise the filename stem
    is converted to a title (snake_case -> Title Case).

    Returns an empty dict if the directory doesn't exist.
    """
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        return {}

    sections: dict[str, str] = {}
    for md_file in sorted(dir_path.glob("*.md")):
        result = load_prompt_file(md_file)
        if result:
            title, content = result
            sections[title] = content

    return sections
