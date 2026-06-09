# Extensions

基于 agentcore 构建的应用层智能体系统。包含 4 个 CLI agent runner、20 个插件、4 套提示词模板。

## Runners

| Runner | 定位 | 特性 |
|--------|------|------|
| `hermes_runner.py` | 通用智能体 | 记忆、技能、会话管理、MCP 集成 |
| `opencode_runner.py` | 编码智能体 | 代码编辑、LSP 集成、任务管理、子 agent 委派 |
| `openclaw_runner.py` | 安全智能体 | 沙箱执行、安全策略、审批门控、轨迹跟踪 |
| `codex_runner.py` | Codex CLI | 沙箱 + 审批 + 编码规范 + `/undo` `/diff` `/review` 命令 |

所有 runner 共享相同的架构模式：

```python
config = build_config(ConfigPlugin())
engine = AgentEngine(config)
pm = PluginManager(...)
pm.register(...)  # 按需注册插件
runtime = AgentRuntime(engine=engine, plugins=pm, session_store=JsonlSessionStore(...))
await runtime.initialize()
# 构建系统提示词（DynamicPromptBuilder）
# CLI 循环：runtime.run() → StreamEvent
```

## Plugins

### 核心插件

| Plugin | 功能 |
|--------|------|
| `config_plugin.py` | 加载 `~/.hermes/config.yaml` 或项目级配置 |
| `core_toolkit.py` | 启动 MCP server，提供 `read_file` / `write_file` / `execute_command` / `search_files` |
| `tools_plugin.py` | 文件操作工具：`glob` / `grep` / `edit` / `list_directory` |
| `edit_plugin.py` | 高级文件编辑（diff-based） |
| `mcp_plugin.py` | MCP 客户端，连接外部 MCP server 并注册其工具 |

### 智能体插件

| Plugin | 功能 |
|--------|------|
| `agents_plugin.py` | 子 agent 管理（plan / explore / build / general 等 8 种 agent 类型） |
| `memory_plugin.py` | 持久记忆系统 |
| `skills_plugin.py` | 技能注入 |
| `context_plugin.py` | 上下文压缩 |
| `instruction_plugin.py` | 指令管理 |

### 安全与审批

| Plugin | 功能 |
|--------|------|
| `sandbox_plugin.py` | 沙箱执行（local / docker / ssh / openshell） |
| `security_policy_plugin.py` | 11 层安全策略管线（allow / deny / ask） |
| `approval_plugin.py` | 人工审批门控 |

### Codex 专用

| Plugin | 功能 |
|--------|------|
| `codex_config_plugin.py` | 加载 `codex.toml` 配置 |
| `codex_commands_plugin.py` | Codex 命令：`/undo` `/diff` `/test` `/plan` `/review` `/approve` `/reject` `/sandbox` |

### 其他

| Plugin | 功能 |
|--------|------|
| `commands_plugin.py` | 斜杠命令基础设施 |
| `session_dag_plugin.py` | 会话 DAG 分支管理 |
| `trajectory_plugin.py` | 执行轨迹跟踪 |
| `lsp_plugin.py` | Language Server Protocol 集成 |
| `dm_pairing_plugin.py` | DM 配对 |
| `tool_search_plugin.py` | 工具搜索 |

## Prompts

4 套提示词模板，每套支持 Python 常量 + `.md` 文件双源加载：

```
prompts/
├── loader.py              # load_prompt_sections() — 从 .md 文件加载
├── codex/                 # Codex: 沙箱 + 审批 + 编码规范
├── hermes/                # Hermes: 通用智能体行为准则
├── openclaw/              # OpenClaw: 安全策略 + 沙箱指令
└── opencode/              # OpenCode: 编码规范 + 任务管理
```

## MCP Servers

| Server | 功能 |
|--------|------|
| `mcp_servers/core_toolkit_server.py` | 文件 I/O、shell 命令、文件搜索（支持 stdio / TCP 模式） |

## Installation

```bash
pip install -e .
```

## Dependencies

- `agentcore` — 核心引擎

## Quick Start

```bash
# Hermes
OPENAI_API_KEY=sk-xxx python -m extensions.hermes_runner

# OpenCode
OPENAI_API_KEY=sk-xxx python -m extensions.opencode_runner

# Codex
OPENAI_API_KEY=sk-xxx python -m extensions.codex_runner
```

## License

MIT
