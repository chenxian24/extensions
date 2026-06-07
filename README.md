# Extensions

Agent runners and MCP servers for the agent ecosystem.

## Runners

- **hermes_runner** — General-purpose agent with memory and skills
- **opencode_runner** — Code-focused agent with task management
- **openclaw_runner** — Security-oriented agent with sandbox support

## Plugins

- `memory_plugin` — Memory management
- `skills_plugin` — Skill loading and execution
- `tools_plugin` — Tool registration
- `context_plugin` — Context compression
- `config_plugin` — Configuration management
- `mcp_plugin` — MCP server integration
- `commands_plugin` — Slash commands
- `approval_plugin` — Tool approval workflow
- `sandbox_plugin` — Sandboxed execution
- `security_policy_plugin` — Security policies
- `trajectory_plugin` — Execution trajectory tracking
- `session_dag_plugin` — Session DAG management
- `lsp_plugin` — Language Server Protocol integration
- `edit_plugin` — File editing tools
- `instruction_plugin` — Instruction management
- `agents_plugin` — Agent management
- `dm_pairing_plugin` — DM pairing
- `tool_search_plugin` — Tool search

## MCP Servers

- **core_toolkit** — File I/O, shell commands, file search

## Installation

```bash
pip install -e .
```

## Dependencies

- `agentcore` — Core framework
- `agentsys` — System utilities

## Quick Start

```bash
python -m extensions.hermes_runner
```

## License

MIT
