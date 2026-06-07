"""Core Toolkit MCP Server — provides file I/O and shell tools via MCP protocol.

Run: python core_toolkit_server.py [--port PORT]
Default: stdio mode. With --port: TCP socket mode.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def tool_read_file(path: str) -> dict[str, Any]:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        return {"content": [{"type": "text", "text": content}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


async def tool_write_file(path: str, content: str) -> dict[str, Any]:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"content": [{"type": "text", "text": f"Wrote {len(content)} chars to {path}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


def _get_system_encoding() -> str:
    """Get the real system codepage, not Python's UTF-8 mode override."""
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            return f"cp{ctypes.windll.kernel32.GetACP()}"
        except Exception:
            pass
    import locale
    return locale.getpreferredencoding(False) or "utf-8"


_SYSTEM_ENCODING = _get_system_encoding()


def _smart_decode(data: bytes) -> str:
    """Decode subprocess output: try UTF-8 first, fall back to system codepage.

    On Windows, cmd.exe and most CLI tools output in the system codepage (e.g. cp936)
    while some tools (curl, python in UTF-8 mode) output UTF-8.
    We detect which one by checking if UTF-8 decoding produces replacement characters.
    """
    try:
        result = data.decode("utf-8")
        if "�" not in result:
            return result
    except UnicodeDecodeError:
        pass
    return data.decode(_SYSTEM_ENCODING, errors="replace")


async def tool_execute_command(command: str, timeout: int = 30) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = _smart_decode(stdout)
        if stderr:
            output += "\n[stderr]\n" + _smart_decode(stderr)
        return {"content": [{"type": "text", "text": output.strip()}]}
    except asyncio.TimeoutError:
        return {"content": [{"type": "text", "text": f"Command timed out after {timeout}s"}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


async def tool_search_files(path: str, pattern: str) -> dict[str, Any]:
    try:
        matches: list[str] = []
        root = Path(path)
        if not root.exists():
            return {"content": [{"type": "text", "text": f"Path does not exist: {path}"}], "isError": True}
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern in line:
                        matches.append(f"{f}:{i}: {line.strip()}")
                        if len(matches) >= 100:
                            return {"content": [{"type": "text", "text": "\n".join(matches) + "\n[truncated at 100 matches]"}]}
            except Exception:
                continue
        if not matches:
            return {"content": [{"type": "text", "text": f"No matches for '{pattern}' in {path}"}]}
        return {"content": [{"type": "text", "text": "\n".join(matches)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating directories as needed",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "execute_command",
        "description": "Execute a shell command and return its output",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for a text pattern in files under a directory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to search in"},
                "pattern": {"type": "string", "description": "Text pattern to search for"},
            },
            "required": ["path", "pattern"],
        },
    },
]

TOOL_HANDLERS = {
    "read_file": lambda args: tool_read_file(**args),
    "write_file": lambda args: tool_write_file(**args),
    "execute_command": lambda args: tool_execute_command(**args),
    "search_files": lambda args: tool_search_files(**args),
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC handler
# ---------------------------------------------------------------------------

async def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "core-toolkit", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True},
            }
        try:
            result = await handler(arguments)
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"Tool execution error: {e}"}], "isError": True}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


# ---------------------------------------------------------------------------
# TCP server mode
# ---------------------------------------------------------------------------

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle a single MCP client connection over TCP."""
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                response = await handle_request(request)
            except Exception as e:
                # Catch any unhandled error in request processing
                req_id = request.get("id") if isinstance(request, dict) else None
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"Internal error: {e}"},
                }
            if response is not None:
                try:
                    payload = json.dumps(response) + "\n"
                    writer.write(payload.encode("utf-8"))
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


async def run_tcp_server(port: int) -> None:
    server = await asyncio.start_server(handle_client, "127.0.0.1", port)
    print(f"core-toolkit MCP server listening on 127.0.0.1:{port}", file=sys.stderr)
    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = 0
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    if port > 0:
        asyncio.run(run_tcp_server(port))
    else:
        # Fallback: stdin/stdout mode (may have buffering issues on Windows)
        import threading, os

        def _stdin_reader(queue):
            fd = 0
            buf = b""
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    queue.put_nowait(None)
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    queue.put_nowait(line.decode("utf-8", errors="replace"))

        async def run_stdio():
            queue = asyncio.Queue()
            t = threading.Thread(target=_stdin_reader, args=(queue,), daemon=True)
            t.start()
            while True:
                line = await queue.get()
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response = await handle_request(request)
                if response is not None:
                    os.write(1, (json.dumps(response) + "\n").encode("utf-8"))

        asyncio.run(run_stdio())
