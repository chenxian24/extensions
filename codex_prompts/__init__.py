"""Backward compatibility — redirects to extensions.prompts.codex."""

from extensions.prompts.codex import *  # noqa: F401,F403
from extensions.prompts.codex import (
    APPROVAL_NEVER,
    APPROVAL_ON_REQUEST,
    APPROVAL_POLICIES,
    APPROVAL_UNLESS_TRUSTED,
    CODEX_BASE_PROMPT,
    CODEX_CODING_GUIDELINES,
    CODEX_ENVIRONMENT_TEMPLATE,
    CODEX_IDENTITY,
    CODEX_TOOL_USAGE,
    CODEX_WORKFLOW,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_MODES,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
)
