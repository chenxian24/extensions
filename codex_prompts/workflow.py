"""Codex workflow instructions."""

CODEX_WORKFLOW = """\
## Workflow

### For Simple Tasks (single file change, bug fix)
1. Read the relevant file
2. Make the edit
3. Run tests to verify
4. Report what you did

### For Complex Tasks (multi-file, new feature, refactor)
1. **Explore** — Understand the codebase. Read related files, find patterns.
2. **Plan** — Outline the changes needed. Break into steps.
3. **Implement** — Make changes one step at a time. Test each step.
4. **Verify** — Run the full test suite. Check for regressions.
5. **Report** — Summarize what was changed and why.

### When Stuck
- Re-read the error message. The answer is often in the error.
- Check if the file path is correct.
- Look at similar code in the project for patterns.
- Try a simpler approach first.
- Ask the user for clarification if the task is ambiguous.
"""

CODEX_ENVIRONMENT_TEMPLATE = """\
<environment context>
## Working Directory
{cwd}

## Operating System
{os}

## Shell
{shell}

## Project
{project_info}
</environment context>
"""
