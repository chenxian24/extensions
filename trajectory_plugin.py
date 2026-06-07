"""Trajectory Plugin — full interaction recording for OpenClaw.

Records all agent interactions: user input, LLM calls, tool calls/results.
Stores in SQLite with export and search capabilities.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class TrajectoryPlugin(Plugin):
    """Full trajectory recording.

    Hooks:
        PRE_BUILD_MESSAGES (priority=200) — record user input
        PRE_LLM_CALL (priority=200) — record LLM request
        POST_LLM_CALL (priority=200) — record LLM response
        PRE_TOOL_CALL (priority=200) — record tool call
        POST_TOOL_CALL (priority=200) — record tool result

    Tools:
        export_trajectory(session_id?) — export trajectory as JSON
        search_trajectory(query) — search trajectory records
        trajectory_stats() — show trajectory statistics
    """

    @property
    def name(self) -> str:
        return "trajectory"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Full interaction trajectory recording"

    def __init__(self, db_path: str = "openclaw_trajectory.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._current_session_id: str = ""

    async def setup(self, ctx: PluginContext) -> None:
        hermes = ctx.config.metadata.get("hermes", {})
        if "trajectory_db" in hermes:
            self._db_path = hermes["trajectory_db"]

        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session ON trajectories(session_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_type ON trajectories(event_type)
        """)
        self._conn.commit()

        # Register hooks (all at low priority=200, after other hooks)
        ctx.register_hook(HookName.PRE_BUILD_MESSAGES, self._record_user_input, priority=200)
        ctx.register_hook(HookName.PRE_LLM_CALL, self._record_llm_request, priority=200)
        ctx.register_hook(HookName.POST_LLM_CALL, self._record_llm_response, priority=200)
        ctx.register_hook(HookName.PRE_TOOL_CALL, self._record_tool_call, priority=200)
        ctx.register_hook(HookName.POST_TOOL_CALL, self._record_tool_result, priority=200)

        # Register tools
        ctx.register_tool(
            "export_trajectory",
            self._tool_export,
            description="Export interaction trajectory as JSON",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID (default: current)"},
                    "limit": {"type": "integer", "description": "Max events to export (default: 100)"},
                },
            },
        )
        ctx.register_tool(
            "search_trajectory",
            self._tool_search,
            description="Search trajectory records by keyword",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword"},
                    "event_type": {"type": "string", "description": "Filter by event type"},
                    "limit": {"type": "integer", "description": "Max results (default: 20)"},
                },
                "required": ["query"],
            },
        )
        ctx.register_tool(
            "trajectory_stats",
            self._tool_stats,
            description="Show trajectory recording statistics",
            parameters={"type": "object", "properties": {}},
        )

    def _record(self, session_id: str, event_type: str, data: dict[str, Any]) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "INSERT INTO trajectories (session_id, event_type, data, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, event_type, json.dumps(data, ensure_ascii=False, default=str), time.time()),
        )
        self._conn.commit()

    async def _record_user_input(self, ctx: HookContext) -> None:
        session_id = ctx.session.id if ctx.session else "unknown"
        self._current_session_id = session_id
        self._record(session_id, "user_input", {
            "input": ctx.user_input,
        })

    async def _record_llm_request(self, ctx: HookContext) -> None:
        session_id = self._current_session_id or "unknown"
        msg_count = len(ctx.messages) if ctx.messages else 0
        self._record(session_id, "llm_request", {
            "message_count": msg_count,
            "model": ctx.params.model if ctx.params else "",
        })

    async def _record_llm_response(self, ctx: HookContext) -> None:
        session_id = self._current_session_id or "unknown"
        if ctx.response:
            self._record(session_id, "llm_response", {
                "content_length": len(ctx.response.content),
                "model": ctx.response.model,
                "finish_reason": ctx.response.finish_reason,
                "usage": ctx.response.usage,
                "tool_calls_count": len(ctx.response.tool_calls),
            })

    async def _record_tool_call(self, ctx: HookContext) -> None:
        if not ctx.tool_call:
            return
        session_id = self._current_session_id or "unknown"
        self._record(session_id, "tool_call", {
            "tool_name": ctx.tool_call.function.name,
            "arguments": ctx.tool_call.function.arguments[:500],  # Truncate
        })

    async def _record_tool_result(self, ctx: HookContext) -> None:
        if not ctx.tool_call:
            return
        session_id = self._current_session_id or "unknown"
        output = ctx.tool_result.get("output", "")
        self._record(session_id, "tool_result", {
            "tool_name": ctx.tool_call.function.name,
            "success": ctx.tool_result.get("success", True),
            "output_length": len(str(output)),
            "output_preview": str(output)[:300],
        })

    # --- Tool implementations ---

    async def _tool_export(self, session_id: str = "", limit: int = 100) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Trajectory not initialized", "error": "Not initialized"}

        sid = session_id or self._current_session_id
        if not sid:
            return {"output": "No session ID specified and no active session", "error": "No session"}

        cursor = self._conn.execute(
            "SELECT event_type, data, timestamp FROM trajectories "
            "WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
            (sid, limit),
        )
        rows = cursor.fetchall()
        if not rows:
            return {"output": f"No trajectory data for session: {sid}"}

        events = []
        for r in rows:
            events.append({
                "event_type": r[0],
                "data": json.loads(r[1]),
                "timestamp": r[2],
            })

        output = json.dumps(events, ensure_ascii=False, indent=2, default=str)
        if len(output) > 5000:
            output = output[:5000] + "\n... [truncated]"
        return {"output": output}

    async def _tool_search(
        self, query: str, event_type: str = "", limit: int = 20,
    ) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Trajectory not initialized", "error": "Not initialized"}

        sql = "SELECT session_id, event_type, data, timestamp FROM trajectories WHERE data LIKE ?"
        params: list[Any] = [f"%{query}%"]

        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        if not rows:
            return {"output": f"No trajectory records matching: {query}"}

        lines = []
        for r in rows:
            data = json.loads(r[2])
            preview = str(data)[:150]
            lines.append(f"  [{r[1]}] session={r[0][:8]}... {preview}")
        return {"output": f"Found {len(rows)} records:\n" + "\n".join(lines)}

    async def _tool_stats(self) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Trajectory not initialized", "error": "Not initialized"}

        cursor = self._conn.execute("SELECT COUNT(*) FROM trajectories")
        total = cursor.fetchone()[0]

        cursor = self._conn.execute(
            "SELECT event_type, COUNT(*) FROM trajectories GROUP BY event_type"
        )
        by_type = cursor.fetchall()

        cursor = self._conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM trajectories"
        )
        session_count = cursor.fetchone()[0]

        lines = [
            f"Total events: {total}",
            f"Sessions: {session_count}",
            "By type:",
        ]
        for r in by_type:
            lines.append(f"  {r[0]}: {r[1]}")
        return {"output": "\n".join(lines)}

    async def teardown(self, ctx: PluginContext) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
