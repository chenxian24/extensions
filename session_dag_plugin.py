"""Session DAG Plugin — branching and rewriting for OpenClaw.

Supports creating branches from any point in the conversation,
switching between branches, merging, and deleting.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


class SessionDAGPlugin(Plugin):
    """Session branching and DAG management.

    Tools:
        create_branch(fork_point?, name?) — create a branch from current session
        list_branches() — list all branches
        switch_branch(branch_id) — switch to a different branch
        merge_branch(branch_id) — merge a branch into the current session
        delete_branch(branch_id) — delete a branch
    """

    @property
    def name(self) -> str:
        return "session-dag"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Session branching and DAG management"

    def __init__(self, db_path: str = "openclaw_dag.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._active_session: Any = None  # Set by runner via metadata

    async def setup(self, ctx: PluginContext) -> None:
        hermes = ctx.config.metadata.get("hermes", {})
        if "dag_db" in hermes:
            self._db_path = hermes["dag_db"]

        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                branch_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_session_id TEXT NOT NULL,
                fork_point INTEGER NOT NULL,
                messages TEXT NOT NULL,
                created_at REAL NOT NULL,
                is_active INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

        # Store session reference for tools
        self._active_session = ctx.config.metadata

        ctx.register_tool(
            "create_branch",
            self._tool_create,
            description="Create a new branch from the current session at a given message index",
            parameters={
                "type": "object",
                "properties": {
                    "fork_point": {"type": "integer", "description": "Message index to fork from (default: last message)"},
                    "name": {"type": "string", "description": "Branch name (default: auto-generated)"},
                },
            },
        )
        ctx.register_tool(
            "list_branches",
            self._tool_list,
            description="List all branches for the current session",
            parameters={"type": "object", "properties": {}},
        )
        ctx.register_tool(
            "switch_branch",
            self._tool_switch,
            description="Switch to a different branch",
            parameters={
                "type": "object",
                "properties": {
                    "branch_id": {"type": "string", "description": "Branch ID to switch to"},
                },
                "required": ["branch_id"],
            },
        )
        ctx.register_tool(
            "merge_branch",
            self._tool_merge,
            description="Merge a branch's messages into the current session",
            parameters={
                "type": "object",
                "properties": {
                    "branch_id": {"type": "string", "description": "Branch ID to merge"},
                },
                "required": ["branch_id"],
            },
        )
        ctx.register_tool(
            "delete_branch",
            self._tool_delete,
            description="Delete a branch",
            parameters={
                "type": "object",
                "properties": {
                    "branch_id": {"type": "string", "description": "Branch ID to delete"},
                },
                "required": ["branch_id"],
            },
        )

    def _get_session_id(self) -> str:
        session = self._active_session.get("_current_session")
        if session and hasattr(session, "id"):
            return session.id
        return "default"

    def _get_session_messages(self) -> list[dict[str, Any]]:
        session = self._active_session.get("_current_session")
        if session and hasattr(session, "messages"):
            return [
                {
                    "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                    "content": m.content,
                    "name": m.name,
                    "tool_call_id": m.tool_call_id,
                }
                for m in session.messages
            ]
        return []

    def _set_session_messages(self, messages: list[dict[str, Any]]) -> None:
        session = self._active_session.get("_current_session")
        if session and hasattr(session, "clear") and hasattr(session, "add_message"):
            from agentcore.core.message import Message, MessageRole
            session.clear()
            for m in messages:
                msg = Message(
                    role=MessageRole(m["role"]),
                    content=m.get("content", ""),
                    name=m.get("name", ""),
                    tool_call_id=m.get("tool_call_id", ""),
                )
                session.add_message(msg)

    # --- Tool implementations ---

    async def _tool_create(self, fork_point: int = -1, name: str = "") -> dict[str, Any]:
        if not self._conn:
            return {"output": "Session DAG not initialized", "error": "Not initialized"}

        session_id = self._get_session_id()
        messages = self._get_session_messages()

        if fork_point < 0:
            fork_point = max(0, len(messages) - 1)
        if fork_point >= len(messages):
            return {"output": f"fork_point {fork_point} out of range (0-{len(messages)-1})", "error": "Out of range"}

        # Store messages up to fork_point
        branch_messages = messages[:fork_point + 1]
        branch_id = f"branch-{int(time.time())}-{fork_point}"
        if not name:
            name = f"Branch at message {fork_point}"

        self._conn.execute(
            "INSERT INTO branches (branch_id, name, parent_session_id, fork_point, messages, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (branch_id, name, session_id, fork_point, json.dumps(branch_messages, ensure_ascii=False), time.time()),
        )
        self._conn.commit()

        return {"output": f"Created branch '{name}' ({branch_id}) at message {fork_point}\n"
                f"Messages preserved: {len(branch_messages)}"}

    async def _tool_list(self) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Session DAG not initialized", "error": "Not initialized"}

        session_id = self._get_session_id()
        cursor = self._conn.execute(
            "SELECT branch_id, name, fork_point, created_at, is_active FROM branches "
            "WHERE parent_session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            return {"output": "No branches for current session"}

        lines = []
        for r in rows:
            active = " [ACTIVE]" if r[4] else ""
            lines.append(f"  {r[0]}: {r[1]} (fork@{r[2]}){active}")
        return {"output": "Branches:\n" + "\n".join(lines)}

    async def _tool_switch(self, branch_id: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Session DAG not initialized", "error": "Not initialized"}

        # Save current messages as a branch first
        session_id = self._get_session_id()
        current_messages = self._get_session_messages()

        cursor = self._conn.execute(
            "SELECT messages, name FROM branches WHERE branch_id = ?", (branch_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"output": f"Branch not found: {branch_id}", "error": "Not found"}

        branch_messages = json.loads(row[0])

        # Mark all branches as inactive
        self._conn.execute(
            "UPDATE branches SET is_active = 0 WHERE parent_session_id = ?", (session_id,)
        )
        # Mark this branch as active
        self._conn.execute(
            "UPDATE branches SET is_active = 1 WHERE branch_id = ?", (branch_id,)
        )
        self._conn.commit()

        # Switch session messages
        self._set_session_messages(branch_messages)

        return {"output": f"Switched to branch '{row[1]}' ({len(branch_messages)} messages)"}

    async def _tool_merge(self, branch_id: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Session DAG not initialized", "error": "Not initialized"}

        cursor = self._conn.execute(
            "SELECT messages, fork_point, name FROM branches WHERE branch_id = ?", (branch_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"output": f"Branch not found: {branch_id}", "error": "Not found"}

        branch_messages = json.loads(row[0])
        fork_point = row[1]
        current_messages = self._get_session_messages()

        # Merge: keep messages before fork_point from current, add branch messages after
        merged = current_messages[:fork_point] + branch_messages
        self._set_session_messages(merged)

        return {"output": f"Merged branch '{row[2]}': {len(merged)} total messages"}

    async def _tool_delete(self, branch_id: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Session DAG not initialized", "error": "Not initialized"}

        cursor = self._conn.execute(
            "SELECT name FROM branches WHERE branch_id = ?", (branch_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"output": f"Branch not found: {branch_id}", "error": "Not found"}

        self._conn.execute("DELETE FROM branches WHERE branch_id = ?", (branch_id,))
        self._conn.commit()
        return {"output": f"Deleted branch '{row[0]}'"}

    async def teardown(self, ctx: PluginContext) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
