"""Sandbox mode instructions for Codex — modeled after codex-rust permission instructions."""

SANDBOX_READ_ONLY = """\
<permissions instructions>
## Sandbox Mode: Read-Only

The sandbox only permits reading files. You cannot:
- Write or edit any files
- Create new files or directories
- Delete or rename files
- Execute commands that modify the filesystem

You CAN:
- Read files (cat, head, tail, type)
- Search files (grep, rg, find, glob)
- List directories (ls, dir)
- Run read-only git commands (status, log, diff, show, branch)
- Analyze code and provide suggestions

To make changes, describe what needs to change and let the user apply the edits.
</permissions instructions>
"""

SANDBOX_WORKSPACE_WRITE = """\
<permissions instructions>
## Sandbox Mode: Workspace-Write

The sandbox permits reading all files, and writing files only within the working directory
and configured writable roots.

You CAN:
- Read any file on the system
- Write/edit files in the working directory
- Execute commands in the working directory
- Run git commands

You CANNOT:
- Write files outside the working directory (unless in writable_roots)
- Modify system files or configuration
- Install packages globally

Protected paths (even within writable roots):
- `.git/` — never modify git internals directly
- `.codex/` — configuration directory

Before writing files or running commands with side effects, briefly explain what you're about to do.
</permissions instructions>
"""

SANDBOX_DANGER_FULL_ACCESS = """\
<permissions instructions>
## Sandbox Mode: Full Access

No filesystem sandboxing is active. All commands are permitted.

You have full read and write access to the entire filesystem.
You can execute any command without restrictions.

Exercise caution:
- Prefer working within the project directory
- Avoid destructive commands (rm -rf, format, etc.) unless explicitly asked
- Don't modify system files unless the task requires it
- Always explain what you're about to do before running potentially dangerous commands
</permissions instructions>
"""

SANDBOX_MODES = {
    "read_only": SANDBOX_READ_ONLY,
    "workspace_write": SANDBOX_WORKSPACE_WRITE,
    "danger_full_access": SANDBOX_DANGER_FULL_ACCESS,
}
