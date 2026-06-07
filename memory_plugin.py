"""Memory Plugin — conversation memory with SQLite + optional file-based memory.

Supports two modes:
- SQLite: persistent searchable memory via FTS5 (default)
- File-based: MEMORY.md + USER.md files (Hermes-compatible)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class MemoryPlugin(Plugin):
    """Persistent conversation memory.

    Hooks:
        POST_BUILD_MESSAGES — inject recent memories into context
    Tools:
        save_memory(content, tags?) — persist a memory
        search_memory(query) — search memories by text
        recall_memory(query) — alias for search_memory
    """

    @property
    def name(self) -> str:
        return "memory"

    @property
    def version(self) -> str:
        return "0.2.0"

    @property
    def description(self) -> str:
        return "Persistent conversation memory (SQLite + optional file-based)"

    def __init__(
        self,
        db_path: str = "memory.db",
        max_inject: int = 5,
        file_memory: bool = False,
        memory_dir: str = "",
    ) -> None:
        self._db_path = db_path
        self._max_inject = max_inject
        self._file_memory = file_memory
        self._memory_dir = Path(memory_dir) if memory_dir else Path.home() / ".hermes"
        self._conn: sqlite3.Connection | None = None

    async def setup(self, ctx: PluginContext) -> None:
        # SQLite setup
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, tags, content=memories, content_rowid=id)
        """)
        self._conn.commit()

        # Ensure file memory directory exists
        if self._file_memory:
            self._memory_dir.mkdir(parents=True, exist_ok=True)

        # Register tools
        ctx.register_tool("save_memory", self._save_memory,
                          description="Save a memory for future recall. Use tags='file' to save to MEMORY.md",
                          parameters={"type": "object", "properties": {"content": {"type": "string"}, "tags": {"type": "string"}}, "required": ["content"]})
        ctx.register_tool("search_memory", self._search_memory,
                          description="Search saved memories by query",
                          parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})
        ctx.register_tool("recall_memory", self._search_memory,
                          description="Recall memories matching a query",
                          parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})

        # Register hook to inject recent memories
        ctx.register_hook(HookName.POST_BUILD_MESSAGES, self._inject_memories, priority=150)

    def _read_file_memory(self) -> str:
        """Read MEMORY.md and USER.md content."""
        parts = []
        for name in ("MEMORY.md", "USER.md"):
            path = self._memory_dir / name
            if path.exists() and path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        parts.append(f"## {name}\n\n{content}")
                except (OSError, PermissionError):
                    pass
        return "\n\n---\n\n".join(parts)

    def _append_to_memory_file(self, content: str) -> None:
        """Append a line to MEMORY.md."""
        memory_path = self._memory_dir / "MEMORY.md"
        try:
            existing = ""
            if memory_path.exists():
                existing = memory_path.read_text(encoding="utf-8", errors="replace")
            if existing and not existing.endswith("\n"):
                existing += "\n"
            memory_path.write_text(existing + f"- {content}\n", encoding="utf-8")
        except (OSError, PermissionError):
            pass

    async def _inject_memories(self, ctx: HookContext) -> None:
        """Inject recent memories as a system message after building messages."""
        if not ctx.messages:
            return

        parts = []

        # SQLite memories
        if self._conn:
            cursor = self._conn.execute(
                "SELECT content, tags, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
                (self._max_inject,),
            )
            rows = cursor.fetchall()
            if rows:
                memories_text = "\n".join(f"- {r[0]}" for r in reversed(rows))
                parts.append(f"[Recalled memories]\n{memories_text}")

        # File-based memories
        if self._file_memory:
            file_content = self._read_file_memory()
            if file_content:
                parts.append(f"[File memories]\n{file_content}")

        if not parts:
            return

        from agentcore.models.base import LLMMessage
        memory_msg = LLMMessage(role="system", content="\n\n".join(parts))
        ctx.messages.insert(len(ctx.messages) - 1, memory_msg)

    async def _save_memory(self, content: str, tags: str = "") -> dict[str, Any]:
        if not self._conn:
            return {"error": "Memory not initialized"}

        # If tags contain "file", also save to MEMORY.md
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if "file" in tag_list and self._file_memory:
            self._append_to_memory_file(content)
            tag_list.remove("file")

        self._conn.execute(
            "INSERT INTO memories (content, tags, created_at) VALUES (?, ?, ?)",
            (content, ",".join(tag_list), time.time()),
        )
        self._conn.execute(
            "INSERT INTO memories_fts (rowid, content, tags) VALUES (last_insert_rowid(), ?, ?)",
            (content, ",".join(tag_list)),
        )
        self._conn.commit()
        return {"output": f"Memory saved: {content[:80]}..."}

    async def _search_memory(self, query: str) -> dict[str, Any]:
        if not self._conn:
            return {"error": "Memory not initialized"}
        cursor = self._conn.execute(
            "SELECT m.content, m.tags, m.created_at FROM memories_fts fts "
            "JOIN memories m ON m.id = fts.rowid "
            "WHERE memories_fts MATCH ? ORDER BY m.created_at DESC LIMIT 10",
            (query,),
        )
        rows = cursor.fetchall()
        if not rows:
            return {"output": f"No memories found for: {query}"}
        results = "\n".join(f"- [{r[2]:.0f}] {r[0]}" for r in rows)
        return {"output": results}

    async def teardown(self, ctx: PluginContext) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
