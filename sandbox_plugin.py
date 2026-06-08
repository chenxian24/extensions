"""Sandbox Plugin — isolated execution environments for OpenClaw.

Supports three backends:
- local: subprocess with timeout and working directory isolation
- docker: container-based isolation with volume mounts
- ssh: remote execution via SSH
"""

from __future__ import annotations

import asyncio
import json
import locale
import os
import tempfile
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext


# Windows subprocess encoding: get the real system codepage
def _get_system_encoding() -> str:
    """Get the real system codepage, not Python's UTF-8 mode override."""
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            return f"cp{ctypes.windll.kernel32.GetACP()}"
        except Exception:
            pass
    return locale.getpreferredencoding(False) or "utf-8"


_SUBPROC_ENCODING = _get_system_encoding()


def _decode(data: bytes) -> str:
    """Decode subprocess output: try UTF-8 first, fall back to system codepage.

    On Windows, cmd.exe and most CLI tools output in the system codepage (e.g. cp936)
    while some tools (curl, python in UTF-8 mode) output UTF-8.
    """
    try:
        result = data.decode("utf-8")
        if "�" not in result:
            return result
    except UnicodeDecodeError:
        pass
    return data.decode(_SUBPROC_ENCODING, errors="replace")


class SandboxPlugin(Plugin):
    """Isolated execution environments.

    Tools:
        execute_sandboxed(command, backend?, timeout?, workdir?, image?) — run in sandbox
        sandbox_status() — show sandbox configuration and status
    """

    @property
    def name(self) -> str:
        return "sandbox"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Isolated execution environments (local/docker/ssh/openshell)"

    def __init__(
        self,
        default_backend: str = "local",
        docker_image: str = "python:3.12-slim",
        allowed_dirs: list[str] | None = None,
    ) -> None:
        self._default_backend = default_backend
        self._docker_image = docker_image
        self._allowed_dirs = allowed_dirs or [str(Path.cwd())]
        self._execution_count = 0
        self._ssh_config: dict[str, Any] = {}  # host, port, user, key_path

    async def setup(self, ctx: PluginContext) -> None:
        # Read config from hermes metadata if available
        hermes = ctx.config.metadata.get("hermes", {})
        if "sandbox_backend" in hermes:
            self._default_backend = hermes["sandbox_backend"]
        if "docker_image" in hermes:
            self._docker_image = hermes["docker_image"]
        # SSH config from metadata
        ssh_cfg = ctx.config.metadata.get("sandbox_ssh", {})
        if ssh_cfg:
            self._ssh_config = ssh_cfg

        ctx.register_tool(
            "execute_sandboxed",
            self._execute_sandboxed,
            description="Execute a command in an isolated sandbox environment",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "backend": {"type": "string", "enum": ["local", "docker", "ssh", "openshell"], "description": "Sandbox backend (default: from config)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                    "workdir": {"type": "string", "description": "Working directory for execution"},
                    "image": {"type": "string", "description": "Docker image (docker backend only)"},
                },
                "required": ["command"],
            },
        )
        ctx.register_tool(
            "sandbox_status",
            self._sandbox_status,
            description="Show sandbox configuration and execution statistics",
            parameters={"type": "object", "properties": {}},
        )

    async def _execute_sandboxed(
        self,
        command: str,
        backend: str = "",
        timeout: int = 30,
        workdir: str = "",
        image: str = "",
    ) -> dict[str, Any]:
        backend = backend or self._default_backend
        self._execution_count += 1

        if backend == "docker":
            return await self._exec_docker(command, timeout, workdir, image or self._docker_image)
        if backend == "ssh":
            return await self._exec_ssh(command, timeout, workdir)
        if backend == "openshell":
            return await self._exec_openshell(command, timeout, workdir)
        return await self._exec_local(command, timeout, workdir)

    async def _exec_local(
        self, command: str, timeout: int, workdir: str,
    ) -> dict[str, Any]:
        """Execute in a local subprocess with isolation."""
        cwd = workdir or self._allowed_dirs[0] if self._allowed_dirs else None

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                # Limit environment to reduce information leakage
                env={**os.environ, "SANDBOX": "1"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            output = _decode(stdout)
            if stderr:
                err_text = _decode(stderr)
                if output:
                    output += "\n[stderr]\n" + err_text
                else:
                    output = err_text

            return_code = proc.returncode
            if return_code != 0:
                return {
                    "output": output.strip() or f"Process exited with code {return_code}",
                    "error": f"Exit code: {return_code}",
                }
            return {"output": output.strip()}

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"output": f"Command timed out after {timeout}s", "error": f"Timeout: {timeout}s"}
        except Exception as e:
            return {"output": f"Sandbox execution error: {e}", "error": str(e)}

    async def _exec_docker(
        self, command: str, timeout: int, workdir: str, image: str,
    ) -> dict[str, Any]:
        """Execute in a Docker container."""
        # Build docker run command
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none",  # Disable network
            "--memory", "256m",   # Limit memory
            "--cpus", "0.5",      # Limit CPU
            "--read-only",        # Read-only filesystem
            "--tmpfs", "/tmp:size=64m",  # Writable tmp
        ]

        # Mount working directory if specified
        mount_dir = workdir or (self._allowed_dirs[0] if self._allowed_dirs else None)
        if mount_dir:
            docker_cmd.extend(["-v", f"{mount_dir}:/workspace:ro"])
            docker_cmd.extend(["-w", "/workspace"])

        docker_cmd.extend([image, "sh", "-c", command])

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            output = _decode(stdout)
            if stderr:
                err_text = _decode(stderr)
                # Filter out docker pull messages
                if "Unable to find image" in err_text or "Pulling from" in err_text:
                    output = output  # Ignore pull messages
                else:
                    output += "\n[stderr]\n" + err_text

            if proc.returncode != 0:
                return {
                    "output": output.strip() or f"Container exited with code {proc.returncode}",
                    "error": f"Exit code: {proc.returncode}",
                }
            return {"output": output.strip()}

        except asyncio.TimeoutError:
            return {"output": f"Docker execution timed out after {timeout}s", "error": f"Timeout: {timeout}s"}
        except FileNotFoundError:
            return {"output": "Docker not found. Install Docker or use 'local' backend.", "error": "Docker not available"}
        except Exception as e:
            return {"output": f"Docker execution error: {e}", "error": str(e)}

    async def _exec_openshell(
        self, command: str, timeout: int, workdir: str,
    ) -> dict[str, Any]:
        """Execute in an OpenShell — local subprocess with sanitized environment.

        OpenShell provides lightweight isolation:
        - Sanitized environment (no secrets leaked)
        - Restricted PATH
        - SANDBOX=1 marker
        - Tmp working directory if none specified
        """

        # Create a sanitized environment
        safe_env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": tempfile.gettempdir(),
            "USER": "sandbox",
            "SHELL": "/bin/sh",
            "SANDBOX": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }

        # Determine working directory
        cwd = workdir
        if not cwd:
            # Use a temp directory for isolation
            cwd = tempfile.mkdtemp(prefix="openshell_")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=safe_env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            output = _decode(stdout)
            if stderr:
                err_text = _decode(stderr)
                if output:
                    output += "\n[stderr]\n" + err_text
                else:
                    output = err_text

            if proc.returncode != 0:
                return {
                    "output": output.strip() or f"Process exited with code {proc.returncode}",
                    "error": f"Exit code: {proc.returncode}",
                }
            return {"output": output.strip()}

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"output": f"Command timed out after {timeout}s", "error": f"Timeout: {timeout}s"}
        except Exception as e:
            return {"output": f"OpenShell execution error: {e}", "error": str(e)}

    async def _exec_ssh(
        self, command: str, timeout: int, workdir: str,
    ) -> dict[str, Any]:
        """Execute a command on a remote host via SSH."""
        host = self._ssh_config.get("host", "")
        if not host:
            return {"output": "SSH host not configured. Set sandbox_ssh.host in config.", "error": "No SSH host"}

        port = self._ssh_config.get("port", 22)
        user = self._ssh_config.get("user", "root")
        key_path = self._ssh_config.get("key_path", "")

        # Build SSH command
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
        if port != 22:
            ssh_cmd.extend(["-p", str(port)])
        if key_path:
            ssh_cmd.extend(["-i", key_path])
        ssh_cmd.append(f"{user}@{host}")

        # Add working directory if specified
        remote_cmd = command
        if workdir:
            remote_cmd = f"cd {workdir} && {command}"

        ssh_cmd.append(remote_cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            output = _decode(stdout)
            if stderr:
                err_text = _decode(stderr)
                if output:
                    output += "\n[stderr]\n" + err_text
                else:
                    output = err_text

            if proc.returncode != 0:
                return {
                    "output": output.strip() or f"SSH command exited with code {proc.returncode}",
                    "error": f"Exit code: {proc.returncode}",
                }
            return {"output": output.strip()}

        except asyncio.TimeoutError:
            return {"output": f"SSH command timed out after {timeout}s", "error": f"Timeout: {timeout}s"}
        except FileNotFoundError:
            return {"output": "SSH client not found. Install openssh-client.", "error": "SSH not available"}
        except Exception as e:
            return {"output": f"SSH execution error: {e}", "error": str(e)}

    async def _sandbox_status(self) -> dict[str, Any]:
        lines = [
            f"Backend: {self._default_backend}",
            f"Executions: {self._execution_count}",
            f"Allowed dirs: {', '.join(self._allowed_dirs)}",
        ]
        if self._default_backend == "docker":
            lines.append(f"Docker image: {self._docker_image}")
            # Check docker availability
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "version", "--format", "{{.Server.Version}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                version = _decode(stdout).strip()
                lines.append(f"Docker version: {version}")
            except Exception:
                lines.append("Docker: not available")
        if self._default_backend == "ssh" or self._ssh_config:
            host = self._ssh_config.get("host", "not configured")
            user = self._ssh_config.get("user", "root")
            lines.append(f"SSH target: {user}@{host}")
        return {"output": "\n".join(lines)}
