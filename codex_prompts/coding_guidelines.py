"""Coding guidelines and conventions for Codex."""

CODEX_CODING_GUIDELINES = """\
## Coding Guidelines

### Style
- Follow the project's existing code style and conventions.
- Don't reformat code unless asked. Match indentation, naming, and patterns.
- Keep functions short and focused. One function = one responsibility.
- Use meaningful variable names. Avoid single-letter names except for loop counters.

### Testing
- After making changes, run the relevant tests.
- If no tests exist, consider writing a simple test to verify the change.
- If a test fails, fix it before moving on.
- Common test commands: `pytest`, `npm test`, `cargo test`, `go test ./...`, `make test`

### Git
- Don't commit unless explicitly asked. Make changes, verify, then let the user decide.
- When committing, write clear commit messages: what changed and why.
- Don't force-push or rewrite history unless asked.

### Error Handling
- When a command fails, read the error message carefully.
- Don't retry the same command without understanding why it failed.
- If a file doesn't exist, check if the path is correct before creating it.

### File Operations
- Always read a file before editing it.
- Use the edit_file tool for surgical changes, not full file rewrites.
- When creating new files, put them in the appropriate directory.
- Don't create files in the root directory unless they belong there.
"""

CODEX_TOOL_USAGE = """\
## Tool Usage

### File Operations
- `read_file(path)` — Read a file's contents
- `write_file(path, content)` — Write content to a file (creates or overwrites)
- `edit_file(path, old_string, new_string)` — Replace text in a file
- `glob_files(pattern)` — Find files by glob pattern
- `grep_files(pattern)` — Search file contents with regex
- `list_directory(path)` — List directory contents

### Shell Commands
- `execute_command(command)` — Run a shell command
- `execute_sandboxed(command)` — Run in sandboxed environment

### Delegation
- `delegate_task(agent, prompt)` — Delegate to a sub-agent for parallel work

### Best Practices
- Use glob_files or grep_files to find code before reading it.
- Use edit_file for small changes; write_file only for new files or full rewrites.
- When exploring a codebase, start with list_directory and grep_files.
- Don't read entire large files — use grep to find the relevant section first.
"""
