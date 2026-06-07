"""Config Plugin — loads Hermes configuration from ~/.hermes/config.yaml.

Merge priority: defaults → config file → environment variables.
Config is stored in AgentConfig.metadata["hermes"] for access by other plugins.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentcore.plugins.base import Plugin, PluginContext

DEFAULT_CONFIG_DIR = Path.home() / ".hermes"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "model": "gpt-4o",
    "provider": "openai",
    "temperature": 0.7,
    "max_tokens": 16384,
    "timeout": 120.0,
    "max_tool_rounds": 20,
    "context_max_tokens": 128000,
    "session_dir": str(DEFAULT_CONFIG_DIR / "sessions"),
    "skills_dir": str(DEFAULT_CONFIG_DIR / "skills"),
    "memory_db": str(DEFAULT_CONFIG_DIR / "memory.db"),
    "theme": "default",
    "streaming": True,
    "mcp_servers": {},
}


class ConfigPlugin(Plugin):
    """Loads and merges Hermes configuration.

    Config sources (in priority order):
        1. Defaults (hardcoded)
        2. ~/.hermes/config.yaml (if pyyaml available)
        3. Environment variables (HERMES_MODEL, HERMES_PROVIDER, etc.)

    After setup, config is available at engine.config.metadata["hermes"].
    """

    @property
    def name(self) -> str:
        return "config"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Hermes configuration loader (YAML + env vars)"

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_FILE
        self._config: dict[str, Any] = {}

    async def setup(self, ctx: PluginContext) -> None:
        # Start with defaults
        merged = dict(DEFAULTS)

        # Layer 2: YAML config file
        file_config = self._load_yaml(self._config_path)
        if file_config:
            merged.update(file_config)

        # Layer 3: Environment variables (HERMES_*)
        env_map = {
            "HERMES_MODEL": "model",
            "HERMES_PROVIDER": "provider",
            "HERMES_TEMPERATURE": ("temperature", float),
            "HERMES_MAX_TOKENS": ("max_tokens", int),
            "HERMES_TIMEOUT": ("timeout", float),
            "HERMES_MAX_TOOL_ROUNDS": ("max_tool_rounds", int),
            "HERMES_CONTEXT_MAX_TOKENS": ("context_max_tokens", int),
            "HERMES_SESSION_DIR": "session_dir",
            "HERMES_SKILLS_DIR": "skills_dir",
            "HERMES_MEMORY_DB": "memory_db",
            "HERMES_THEME": "theme",
            "HERMES_STREAMING": ("streaming", lambda v: v.lower() in ("1", "true", "yes")),
        }
        for env_key, spec in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                if isinstance(spec, tuple):
                    key, converter = spec
                    try:
                        merged[key] = converter(val)
                    except (ValueError, TypeError):
                        pass
                else:
                    merged[spec] = val

        self._config = merged

        # Ensure directories exist
        Path(merged["session_dir"]).mkdir(parents=True, exist_ok=True)
        Path(merged["skills_dir"]).mkdir(parents=True, exist_ok=True)

        # Store in agent config metadata
        ctx.config.metadata["hermes"] = self._config

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any] | None:
        """Load YAML config file. Returns None if file doesn't exist or yaml unavailable."""
        if not path.exists():
            return None

        try:
            import yaml
        except ImportError:
            # Fallback: try simple key=value parsing
            return ConfigPlugin._load_simple(path)

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def _load_simple(path: Path) -> dict[str, Any]:
        """Simple key=value parser as YAML fallback."""
        config: dict[str, Any] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # Try type coercion
                    if value.lower() in ("true", "yes"):
                        config[key] = True
                    elif value.lower() in ("false", "no"):
                        config[key] = False
                    elif value.isdigit():
                        config[key] = int(value)
                    else:
                        try:
                            config[key] = float(value)
                        except ValueError:
                            config[key] = value
        except Exception:
            pass
        return config
