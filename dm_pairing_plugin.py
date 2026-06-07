"""DM Pairing Plugin — sender authorization for OpenClaw.

Unknown senders must be approved via pairing code before interacting.
Known senders are automatically allowed.
"""

from __future__ import annotations

import random
import sqlite3
import string
import time
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class DMPairingPlugin(Plugin):
    """DM pairing system for sender authorization.

    Hooks:
        PRE_BUILD_MESSAGES (priority=5) — check sender authorization

    Tools:
        generate_pairing_code(sender_id) — generate a pairing code for a sender
        approve_pairing(code) — approve a pending pairing request
        list_pairings() — list all pairing records
        revoke_pairing(sender_id) — revoke a sender's access
    """

    @property
    def name(self) -> str:
        return "dm-pairing"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "DM sender authorization via pairing codes"

    def __init__(self, db_path: str = "openclaw_pairing.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def setup(self, ctx: PluginContext) -> None:
        # Read db path from config
        hermes = ctx.config.metadata.get("hermes", {})
        if "pairing_db" in hermes:
            self._db_path = hermes["pairing_db"]

        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pairings (
                sender_id TEXT PRIMARY KEY,
                pairing_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                approved_at REAL
            )
        """)
        self._conn.commit()

        # Register hook
        ctx.register_hook(HookName.PRE_BUILD_MESSAGES, self._check_sender, priority=5)

        # Register tools
        ctx.register_tool(
            "generate_pairing_code",
            self._tool_generate_code,
            description="Generate a pairing code for an unknown sender",
            parameters={
                "type": "object",
                "properties": {
                    "sender_id": {"type": "string", "description": "Sender identifier"},
                },
                "required": ["sender_id"],
            },
        )
        ctx.register_tool(
            "approve_pairing",
            self._tool_approve,
            description="Approve a pending pairing request by code",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Pairing code to approve"},
                },
                "required": ["code"],
            },
        )
        ctx.register_tool(
            "list_pairings",
            self._tool_list,
            description="List all pairing records",
            parameters={"type": "object", "properties": {}},
        )
        ctx.register_tool(
            "revoke_pairing",
            self._tool_revoke,
            description="Revoke a sender's access",
            parameters={
                "type": "object",
                "properties": {
                    "sender_id": {"type": "string", "description": "Sender to revoke"},
                },
                "required": ["sender_id"],
            },
        )

    async def _check_sender(self, ctx: HookContext) -> None:
        """Check if the sender is authorized."""
        sender_id = ctx.metadata.get("sender_id", "")
        if not sender_id:
            return  # No sender context, skip check (CLI mode)

        if not self._conn:
            return

        cursor = self._conn.execute(
            "SELECT status FROM pairings WHERE sender_id = ?", (sender_id,)
        )
        row = cursor.fetchone()

        if row and row[0] == "approved":
            return  # Sender is approved

        if row and row[0] == "pending":
            ctx.cancel = True
            ctx.metadata["cancel_reason"] = (
                f"Sender '{sender_id}' has a pending pairing request. "
                f"An admin must approve the pairing code."
            )
            return

        # Unknown sender — generate code automatically
        code = self._generate_code()
        self._conn.execute(
            "INSERT OR REPLACE INTO pairings (sender_id, pairing_code, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (sender_id, code, time.time()),
        )
        self._conn.commit()

        ctx.cancel = True
        ctx.metadata["cancel_reason"] = (
            f"Unknown sender '{sender_id}'. Pairing code generated: {code}\n"
            f"An admin must run: /approve {code}"
        )

    @staticmethod
    def _generate_code(length: int = 6) -> str:
        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=length))

    # --- Tool implementations ---

    async def _tool_generate_code(self, sender_id: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Pairing system not initialized", "error": "Not initialized"}

        code = self._generate_code()
        self._conn.execute(
            "INSERT OR REPLACE INTO pairings (sender_id, pairing_code, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (sender_id, code, time.time()),
        )
        self._conn.commit()
        return {"output": f"Pairing code for '{sender_id}': {code}"}

    async def _tool_approve(self, code: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Pairing system not initialized", "error": "Not initialized"}

        cursor = self._conn.execute(
            "SELECT sender_id FROM pairings WHERE pairing_code = ? AND status = 'pending'",
            (code,),
        )
        row = cursor.fetchone()
        if not row:
            return {"output": f"No pending pairing found for code: {code}", "error": "Not found"}

        sender_id = row[0]
        self._conn.execute(
            "UPDATE pairings SET status = 'approved', approved_at = ? WHERE sender_id = ?",
            (time.time(), sender_id),
        )
        self._conn.commit()
        return {"output": f"Approved pairing for '{sender_id}'"}

    async def _tool_list(self) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Pairing system not initialized", "error": "Not initialized"}

        cursor = self._conn.execute(
            "SELECT sender_id, status, pairing_code, created_at, approved_at FROM pairings ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        if not rows:
            return {"output": "No pairing records"}

        lines = []
        for r in rows:
            status_icon = "✓" if r[1] == "approved" else "⏳"
            line = f"  {status_icon} {r[0]} — {r[1]} (code: {r[2]})"
            if r[4]:
                line += f" [approved at {r[4]:.0f}]"
            lines.append(line)
        return {"output": "Pairings:\n" + "\n".join(lines)}

    async def _tool_revoke(self, sender_id: str) -> dict[str, Any]:
        if not self._conn:
            return {"output": "Pairing system not initialized", "error": "Not initialized"}

        cursor = self._conn.execute(
            "SELECT status FROM pairings WHERE sender_id = ?", (sender_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"output": f"No pairing found for: {sender_id}", "error": "Not found"}

        self._conn.execute("DELETE FROM pairings WHERE sender_id = ?", (sender_id,))
        self._conn.commit()
        return {"output": f"Revoked access for '{sender_id}'"}

    async def teardown(self, ctx: PluginContext) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
