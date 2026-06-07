"""Skills Plugin — markdown-based skill injection for Hermes.

Loads skill definitions from ~/.hermes/skills/*.md files.
Each skill is a markdown file with optional YAML frontmatter:

    ---
    name: my-skill
    description: What this skill does
    version: 1.0.0
    tags: [coding, tools]
    ---

    Skill content here. This text is injected into the system prompt
    so the agent knows how to use this skill.

Skills are injected as extra sections in the DynamicPromptBuilder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


class SkillsPlugin(Plugin):
    """Loads markdown skills and injects them into the system prompt.

    After setup, skills are available at config.metadata["hermes"]["skills"].
    The hermes_runner should pass them to DynamicPromptBuilder.extra_sections.
    """

    @property
    def name(self) -> str:
        return "skills"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Markdown-based skill loading and injection"

    def __init__(self, skills_dir: str | None = None) -> None:
        self._skills_dir = skills_dir
        self._skills: list[dict[str, Any]] = []

    async def setup(self, ctx: PluginContext) -> None:
        hermes_config = ctx.config.metadata.get("hermes", {})
        skills_dir = self._skills_dir or hermes_config.get("skills_dir", str(Path.home() / ".hermes" / "skills"))
        skills_path = Path(skills_dir)

        if not skills_path.exists():
            skills_path.mkdir(parents=True, exist_ok=True)
            self._skills = []
            ctx.config.metadata.setdefault("hermes", {})["skills"] = []
            return

        self._skills = []
        for md_file in sorted(skills_path.glob("*.md")):
            skill = self._parse_skill(md_file)
            if skill:
                self._skills.append(skill)

        # Store skills in hermes config for runner to access
        ctx.config.metadata.setdefault("hermes", {})["skills"] = self._skills

    @property
    def skills(self) -> list[dict[str, Any]]:
        return self._skills

    def get_skill_sections(self) -> dict[str, str]:
        """Return skills as extra_sections dict for DynamicPromptBuilder."""
        sections: dict[str, str] = {}
        for skill in self._skills:
            name = skill.get("name", "unnamed")
            description = skill.get("description", "")
            content = skill.get("content", "")
            title = f"Skill: {name}"
            if description:
                title += f" — {description}"
            sections[title] = content
        return sections

    @staticmethod
    def _parse_skill(path: Path) -> dict[str, Any] | None:
        """Parse a markdown skill file with optional YAML frontmatter."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, PermissionError):
            return None

        if not text.strip():
            return None

        metadata: dict[str, Any] = {"name": path.stem}
        content = text

        # Parse YAML frontmatter (--- delimited)
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if frontmatter_match:
            fm_text = frontmatter_match.group(1)
            content = frontmatter_match.group(2)
            metadata.update(SkillsPlugin._parse_frontmatter(fm_text))

        # Ensure name is set
        if "name" not in metadata:
            metadata["name"] = path.stem

        metadata["content"] = content.strip()
        metadata["file"] = str(path)
        return metadata

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, Any]:
        """Parse simple YAML frontmatter without requiring pyyaml."""
        result: dict[str, Any] = {}
        current_key: str | None = None
        current_list: list[str] | None = None

        for line in text.splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue

            # List continuation
            if line.startswith("  - ") or line.startswith("- "):
                if current_key and current_list is not None:
                    value = line.lstrip(" -").strip().strip('"').strip("'")
                    current_list.append(value)
                continue

            # Key-value pair
            if ":" in line:
                # Flush previous list
                if current_key and current_list is not None:
                    result[current_key] = current_list
                    current_list = None

                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()

                if not value:
                    # Could be start of a list
                    current_key = key
                    current_list = []
                elif value.startswith("[") and value.endswith("]"):
                    # Inline list
                    items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
                    result[key] = items
                    current_key = None
                else:
                    value = value.strip('"').strip("'")
                    # Type coercion
                    if value.lower() in ("true", "yes"):
                        result[key] = True
                    elif value.lower() in ("false", "no"):
                        result[key] = False
                    elif value.isdigit():
                        result[key] = int(value)
                    else:
                        try:
                            result[key] = float(value)
                        except ValueError:
                            result[key] = value
                    current_key = None

        # Flush remaining list
        if current_key and current_list is not None:
            result[current_key] = current_list

        return result
