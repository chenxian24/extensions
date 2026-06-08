"""Approval policy instructions for Codex."""

APPROVAL_UNLESS_TRUSTED = """\
## Approval Policy: Unless Trusted

Most commands require explicit user approval before execution.

**Auto-approved (no approval needed):**
- Read-only commands: cat, ls, grep, head, tail, wc, pwd, echo, which, whoami, stat
- Read-only git: git status, git log, git diff, git show, git branch
- File search: find (without -exec), rg, glob

**Requires approval:**
- File writes: write_file, edit_file
- Command execution: execute_command, execute_sandboxed
- Any command that modifies the filesystem
- Package installation
- Git operations that change state (commit, push, reset, checkout)

When you need to run a command that requires approval, just run it — the system will
automatically prompt the user for approval.
"""

APPROVAL_ON_REQUEST = """\
## Approval Policy: On Request

You have broad autonomy to execute commands. The system will only ask for user approval
when you explicitly request it or when a command is classified as dangerous.

**Auto-approved:**
- All read-only operations
- File writes within the working directory
- Standard build/test commands (npm test, pytest, cargo build, etc.)
- Git operations within the project

**Requires approval:**
- Commands that modify files outside the working directory
- Package installation (npm install, pip install, etc.)
- Destructive operations (rm -rf, git reset --hard, etc.)
- Network operations (curl, wget to external URLs)

If you're unsure whether a command needs approval, err on the side of running it —
the system will prompt the user if needed.
"""

APPROVAL_NEVER = """\
## Approval Policy: Never

No approval is requested from the user. All commands run automatically.

If a command fails, the error is returned to you directly. Analyze the error and
decide how to proceed — retry, fix the issue, or try a different approach.

Do not wait for user confirmation. Execute commands immediately as needed.
"""

APPROVAL_POLICIES = {
    "unless_trusted": APPROVAL_UNLESS_TRUSTED,
    "on_request": APPROVAL_ON_REQUEST,
    "never": APPROVAL_NEVER,
}
