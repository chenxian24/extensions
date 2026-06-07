"""LSP Plugin — Language Server Protocol integration for OpenCode.

Communicates with LSP servers via JSON-RPC 2.0 over stdio.
Supports Python (pyright), TypeScript (typescript-language-server), Go (gopls).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


# LSP server configurations per language
LSP_SERVERS: dict[str, dict[str, Any]] = {
    "python": {
        "command": ["pyright-langserver", "--stdio"],
        "languageId": "python",
        "extensions": [".py"],
    },
    "typescript": {
        "command": ["typescript-language-server", "--stdio"],
        "languageId": "typescript",
        "extensions": [".ts", ".tsx", ".js", ".jsx"],
    },
    "go": {
        "command": ["gopls"],
        "languageId": "go",
        "extensions": [".go"],
    },
}


class _LSPClient:
    """Minimal LSP client communicating over stdio."""

    def __init__(self, command: list[str], root_uri: str) -> None:
        self._command = command
        self._root_uri = root_uri
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._initialized = False
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> bool:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._read_loop())

            # Initialize with short timeout
            resp = await self._send("initialize", {
                "processId": os.getpid(),
                "rootUri": self._root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "hover": {"dynamicRegistration": False},
                        "documentSymbol": {"dynamicRegistration": False},
                        "publishDiagnostics": {"relatedInformation": True},
                    },
                },
            }, timeout=3)
            if resp:
                self._initialized = True
                await self._notify("initialized", {})
            return self._initialized
        except FileNotFoundError:
            return False
        except Exception:
            return False

    async def _read_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            while True:
                # Read Content-Length header
                header = await self._proc.stdout.readline()
                if not header:
                    break
                header_str = header.decode("utf-8").strip()
                if not header_str.startswith("Content-Length:"):
                    continue
                content_length = int(header_str.split(":")[1].strip())

                # Read empty line
                await self._proc.stdout.readline()

                # Read body
                body = await asyncio.wait_for(
                    self._proc.stdout.readexactly(content_length), timeout=30
                )
                msg = json.loads(body.decode("utf-8"))

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    self._pending[req_id].set_result(msg.get("result"))
                    del self._pending[req_id]
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass

    async def _send(self, method: str, params: dict[str, Any], timeout: float = 10) -> Any:
        if not self._proc or not self._proc.stdin:
            return None
        # Check if process is still alive
        if self._proc.returncode is not None:
            return None
        self._req_id += 1
        req_id = self._req_id
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        payload = json.dumps(msg)
        header = f"Content-Length: {len(payload)}\r\n\r\n"
        try:
            self._proc.stdin.write(header.encode("utf-8") + payload.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._pending.pop(req_id, None)
            return None

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return None

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        payload = json.dumps(msg)
        header = f"Content-Length: {len(payload)}\r\n\r\n"
        self._proc.stdin.write(header.encode("utf-8") + payload.encode("utf-8"))
        await self._proc.stdin.drain()

    async def open_file(self, path: str) -> None:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = ""
        uri = Path(path).as_uri()
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "python",
                "version": 1,
                "text": content,
            }
        })

    async def definition(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        return await self._send("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    async def references(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        return await self._send("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        })

    async def hover(self, path: str, line: int, character: int) -> Any:
        uri = Path(path).as_uri()
        return await self._send("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    async def symbols(self, path: str) -> Any:
        uri = Path(path).as_uri()
        return await self._send("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                if self._proc:
                    self._proc.kill()


class LSPPlugin(Plugin):
    """Language Server Protocol integration.

    Tools:
        lsp_go_to_definition(path, line, character) — jump to definition
        lsp_references(path, line, character) — find references
        lsp_hover(path, line, character) — hover information
        lsp_symbols(path) — document symbols
        lsp_diagnostics(path) — file diagnostics
    """

    @property
    def name(self) -> str:
        return "lsp"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Language Server Protocol integration (pyright, ts-server, gopls)"

    def __init__(self, root_dir: str = ".") -> None:
        self._root_dir = root_dir
        self._clients: dict[str, _LSPClient] = {}

    async def setup(self, ctx: PluginContext) -> None:
        root = Path(self._root_dir).resolve()
        root_uri = root.as_uri()

        # Try to start available LSP servers (skip if command not found)
        for lang, config in LSP_SERVERS.items():
            cmd = config["command"][0]
            if not shutil.which(cmd):
                continue  # Command not installed, skip silently
            client = _LSPClient(config["command"], root_uri)
            if await client.start():
                self._clients[lang] = client

        ctx.register_tool(
            "lsp_go_to_definition",
            self._tool_definition,
            description="Jump to the definition of a symbol at a position",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "line": {"type": "integer", "description": "Line number (0-indexed)"},
                    "character": {"type": "integer", "description": "Character position (0-indexed)"},
                },
                "required": ["path", "line", "character"],
            },
        )
        ctx.register_tool(
            "lsp_references",
            self._tool_references,
            description="Find all references to a symbol at a position",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "line": {"type": "integer", "description": "Line number (0-indexed)"},
                    "character": {"type": "integer", "description": "Character position (0-indexed)"},
                },
                "required": ["path", "line", "character"],
            },
        )
        ctx.register_tool(
            "lsp_hover",
            self._tool_hover,
            description="Get hover information (type, docs) for a symbol",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "line": {"type": "integer", "description": "Line number (0-indexed)"},
                    "character": {"type": "integer", "description": "Character position (0-indexed)"},
                },
                "required": ["path", "line", "character"],
            },
        )
        ctx.register_tool(
            "lsp_symbols",
            self._tool_symbols,
            description="List all symbols (functions, classes, variables) in a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        )

    def _get_client(self, path: str) -> _LSPClient | None:
        ext = Path(path).suffix.lower()
        for lang, config in LSP_SERVERS.items():
            if ext in config["extensions"]:
                return self._clients.get(lang)
        return None

    def _format_location(self, loc: dict[str, Any]) -> str:
        uri = loc.get("uri", "")
        range_info = loc.get("range", {})
        start = range_info.get("start", {})
        file_path = Path(uri.replace("file:///", "").replace("file://", ""))
        return f"{file_path}:{start.get('line', 0)}:{start.get('character', 0)}"

    async def _tool_definition(self, path: str, line: int, character: int) -> dict[str, Any]:
        client = self._get_client(path)
        if not client:
            return {"output": f"No LSP server available for {path}", "error": "No LSP server"}
        await client.open_file(path)
        result = await client.definition(path, line, character)
        if not result:
            return {"output": "No definition found"}
        if isinstance(result, list):
            locations = [self._format_location(loc) for loc in result]
            return {"output": "Definitions:\n" + "\n".join(f"  {loc}" for loc in locations)}
        return {"output": f"Definition: {self._format_location(result)}"}

    async def _tool_references(self, path: str, line: int, character: int) -> dict[str, Any]:
        client = self._get_client(path)
        if not client:
            return {"output": f"No LSP server available for {path}", "error": "No LSP server"}
        await client.open_file(path)
        result = await client.references(path, line, character)
        if not result:
            return {"output": "No references found"}
        locations = [self._format_location(loc) for loc in result]
        return {"output": f"References ({len(locations)}):\n" + "\n".join(f"  {loc}" for loc in locations[:20])}

    async def _tool_hover(self, path: str, line: int, character: int) -> dict[str, Any]:
        client = self._get_client(path)
        if not client:
            return {"output": f"No LSP server available for {path}", "error": "No LSP server"}
        await client.open_file(path)
        result = await client.hover(path, line, character)
        if not result:
            return {"output": "No hover information"}
        contents = result.get("contents", {})
        if isinstance(contents, dict):
            text = contents.get("value", str(contents))
        elif isinstance(contents, list):
            text = "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        else:
            text = str(contents)
        return {"output": text}

    async def _tool_symbols(self, path: str) -> dict[str, Any]:
        client = self._get_client(path)
        if not client:
            return {"output": f"No LSP server available for {path}", "error": "No LSP server"}
        await client.open_file(path)
        result = await client.symbols(path)
        if not result:
            return {"output": "No symbols found"}
        lines = []
        for sym in result:
            kind = sym.get("kind", 0)
            name = sym.get("name", "?")
            range_info = sym.get("range", {}).get("start", {})
            line_num = range_info.get("line", 0)
            kind_name = {1: "File", 2: "Module", 5: "Class", 6: "Method", 12: "Function", 13: "Variable", 14: "Constant"}.get(kind, str(kind))
            lines.append(f"  [{kind_name}] {name} (line {line_num})")
        return {"output": f"Symbols ({len(lines)}):\n" + "\n".join(lines[:50])}

    async def teardown(self, ctx: PluginContext) -> None:
        for client in self._clients.values():
            await client.stop()
        self._clients.clear()
