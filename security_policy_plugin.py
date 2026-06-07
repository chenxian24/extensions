"""Security Policy Plugin — 11-layer tool policy evaluation for OpenClaw.

Extends agentcore's PolicyPipeline with layered security policies:
profile → provider → global → agent → group → sender → sandbox →
subagent → inherited → runtime → tool-search

First non-None decision wins. Default: ALLOW.
"""

from __future__ import annotations

import json
from typing import Any

from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext
from agentcore.tools.policy import (
    ContextAwarePolicy,
    PatternPolicy,
    PolicyContext,
    PolicyDecision,
    PolicyPipeline,
    ToolPolicy,
)

# 11 layers with default priorities (lower = evaluated first)
LAYER_NAMES = [
    "profile",      # 1: User profile policy
    "provider",     # 2: Provider-level restrictions
    "global",       # 3: Global default policy
    "agent",        # 4: Agent-level policy
    "group",        # 5: Group policy
    "sender",       # 6: Sender policy (DM scenarios)
    "sandbox",      # 7: Sandbox environment policy
    "subagent",     # 8: Sub-agent policy
    "inherited",    # 9: Inherited policy
    "runtime",      # 10: Runtime dynamic policy
    "tool_search",  # 11: Tool search policy
]

LAYER_PRIORITIES = {name: (i + 1) * 10 for i, name in enumerate(LAYER_NAMES)}


class _SenderPolicy(ToolPolicy):
    """Dynamic per-sender policy that looks up rules from a dict."""

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, PolicyDecision]] = {}
        # sender_id -> {tool_pattern: decision}

    @property
    def name(self) -> str:
        return "sender"

    @property
    def priority(self) -> int:
        return LAYER_PRIORITIES["sender"]

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision | None:
        if not context:
            return None
        sender_id = context.get("sender_id", "")
        if not sender_id or sender_id not in self._rules:
            return None
        import fnmatch
        for pattern, decision in self._rules[sender_id].items():
            if fnmatch.fnmatch(tool_name, pattern):
                return decision
        return None

    def set_rules(self, sender_id: str, rules: dict[str, PolicyDecision]) -> None:
        self._rules[sender_id] = rules

    def add_rule(self, sender_id: str, tool_pattern: str, decision: PolicyDecision) -> None:
        if sender_id not in self._rules:
            self._rules[sender_id] = {}
        self._rules[sender_id][tool_pattern] = decision

    def remove_sender(self, sender_id: str) -> None:
        self._rules.pop(sender_id, None)

    def list_rules(self) -> dict[str, dict[str, str]]:
        return {
            sid: {pat: dec.value for pat, dec in rules.items()}
            for sid, rules in self._rules.items()
        }


class _SandboxPolicy(ContextAwarePolicy):
    """Context-aware policy that restricts tools based on sandbox mode.

    When running inside a sandbox, certain tools are denied to prevent
    sandbox escape or unauthorized operations.
    """

    # Tools that are denied when running inside a sandbox
    SANDBOX_DENIED = [
        "execute_command",      # Use execute_sandboxed instead
        "sandbox_status",       # Information leakage
        "add_policy_rule",      # Policy manipulation
        "remove_policy_rule",
        "set_sender_policy",
        "generate_pairing_code", # DM pairing in sandbox is suspicious
    ]

    @property
    def name(self) -> str:
        return "sandbox"

    @property
    def priority(self) -> int:
        return LAYER_PRIORITIES["sandbox"]

    def evaluate_with_context(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        # Only restrict when inside a sandbox
        if not ctx.sandbox_mode:
            return None

        import fnmatch
        for pattern in self.SANDBOX_DENIED:
            if fnmatch.fnmatch(tool_name, pattern):
                return PolicyDecision.DENY

        # Sub-agents in sandbox can't delegate to other agents
        if ctx.is_subagent and tool_name == "delegate_task":
            return PolicyDecision.DENY

        return None


class SecurityPolicyPlugin(Plugin):
    """11-layer security policy evaluation.

    Hooks:
        PRE_TOOL_CALL (priority=10) — evaluates policy pipeline before execution

    Tools:
        add_policy_rule(layer, tool_pattern, decision) — add a rule to a layer
        remove_policy_rule(layer, tool_pattern) — remove a rule from a layer
        list_policy_rules() — list all rules across all layers
        set_sender_policy(sender_id, tool_pattern, decision) — set per-sender rule
    """

    @property
    def name(self) -> str:
        return "security-policy"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "11-layer tool policy evaluation for OpenClaw security"

    def __init__(self) -> None:
        self._pipeline = PolicyPipeline(default=PolicyDecision.ALLOW)
        self._layers: dict[str, PatternPolicy] = {}
        self._sender_policy = _SenderPolicy()
        self._runtime_policy: PatternPolicy | None = None

    async def setup(self, ctx: PluginContext) -> None:
        # Create a PatternPolicy for each layer
        for name in LAYER_NAMES:
            if name == "sender":
                # Sender layer uses dynamic policy
                self._pipeline.add(self._sender_policy)
                self._layers[name] = self._sender_policy  # type: ignore
            elif name == "sandbox":
                # Sandbox layer uses context-aware policy
                sandbox_policy = _SandboxPolicy()
                self._pipeline.add(sandbox_policy)
                self._layers[name] = sandbox_policy  # type: ignore
            else:
                policy = PatternPolicy(
                    _name=name,
                    priority=LAYER_PRIORITIES[name],
                )
                self._pipeline.add(policy)
                self._layers[name] = policy
                if name == "runtime":
                    self._runtime_policy = policy

        # Default security rules: deny dangerous tools by default
        self._layers["global"].deny_patterns = [
            "rm_*",
            "drop_*",
            "truncate_*",
        ]
        self._layers["global"].ask_patterns = [
            "execute_command",
            "execute_sandboxed",
            "write_file",
            "edit_file",
        ]

        # Register PRE_TOOL_CALL hook (priority=10, before approval at 50)
        ctx.register_hook(HookName.PRE_TOOL_CALL, self._evaluate_policy, priority=10)

        # Register tools
        ctx.register_tool(
            "add_policy_rule",
            self._tool_add_rule,
            description="Add a security policy rule to a specific layer",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "description": f"Policy layer: {', '.join(LAYER_NAMES)}"},
                    "tool_pattern": {"type": "string", "description": "Tool name pattern (glob)"},
                    "decision": {"type": "string", "enum": ["allow", "deny", "ask"], "description": "Policy decision"},
                },
                "required": ["layer", "tool_pattern", "decision"],
            },
        )
        ctx.register_tool(
            "remove_policy_rule",
            self._tool_remove_rule,
            description="Remove a security policy rule from a specific layer",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "description": f"Policy layer: {', '.join(LAYER_NAMES)}"},
                    "tool_pattern": {"type": "string", "description": "Tool name pattern to remove"},
                },
                "required": ["layer", "tool_pattern"],
            },
        )
        ctx.register_tool(
            "list_policy_rules",
            self._tool_list_rules,
            description="List all security policy rules across all layers",
            parameters={"type": "object", "properties": {}},
        )
        ctx.register_tool(
            "set_sender_policy",
            self._tool_set_sender,
            description="Set a per-sender security policy rule",
            parameters={
                "type": "object",
                "properties": {
                    "sender_id": {"type": "string", "description": "Sender identifier"},
                    "tool_pattern": {"type": "string", "description": "Tool name pattern (glob)"},
                    "decision": {"type": "string", "enum": ["allow", "deny", "ask"], "description": "Policy decision"},
                },
                "required": ["sender_id", "tool_pattern", "decision"],
            },
        )

    async def _evaluate_policy(self, ctx: HookContext) -> None:
        """Evaluate the 11-layer policy pipeline before tool execution."""
        if not ctx.tool_call:
            return

        tool_name = ctx.tool_call.function.name
        try:
            args = json.loads(ctx.tool_call.function.arguments) if isinstance(
                ctx.tool_call.function.arguments, str
            ) else ctx.tool_call.function.arguments
        except (json.JSONDecodeError, TypeError):
            args = {}

        # Build evaluation context from HookContext
        eval_context = PolicyContext(
            sender_id=ctx.metadata.get("sender_id", ""),
            session_id=ctx.session.id if ctx.session else "",
            is_subagent=ctx.metadata.get("is_subagent", False),
            sandbox_mode=ctx.metadata.get("sandbox_mode", ""),
            provider_name=ctx.metadata.get("provider_name", ""),
            model_name=ctx.metadata.get("model_name", ""),
        )

        decision = self._pipeline.evaluate(tool_name, args, eval_context)

        # Store decision in metadata for downstream hooks (e.g., ApprovalPlugin)
        ctx.metadata["policy_decision"] = decision.value

        if decision == PolicyDecision.DENY:
            ctx.cancel = True
            ctx.metadata["cancel_reason"] = f"Security policy DENIED: {tool_name}"

    # --- Tool implementations ---

    async def _tool_add_rule(
        self, layer: str, tool_pattern: str, decision: str,
    ) -> dict[str, Any]:
        if layer not in self._layers:
            return {"output": f"Unknown layer: {layer}. Valid: {', '.join(LAYER_NAMES)}", "error": f"Unknown layer: {layer}"}
        if layer == "sender":
            return {"output": "Use set_sender_policy for sender layer", "error": "Use set_sender_policy"}

        try:
            dec = PolicyDecision(decision)
        except ValueError:
            return {"output": f"Invalid decision: {decision}. Use allow/deny/ask", "error": f"Invalid decision: {decision}"}

        policy = self._layers[layer]
        if dec == PolicyDecision.ALLOW:
            policy.allow_patterns.append(tool_pattern)
        elif dec == PolicyDecision.DENY:
            policy.deny_patterns.append(tool_pattern)
        elif dec == PolicyDecision.ASK:
            policy.ask_patterns.append(tool_pattern)

        return {"output": f"Added {dec.value} rule '{tool_pattern}' to {layer} layer"}

    async def _tool_remove_rule(self, layer: str, tool_pattern: str) -> dict[str, Any]:
        if layer not in self._layers:
            return {"output": f"Unknown layer: {layer}", "error": f"Unknown layer: {layer}"}
        if layer == "sender":
            return {"output": "Use set_sender_policy for sender layer", "error": "Use set_sender_policy"}

        policy = self._layers[layer]
        removed = False
        for lst in (policy.allow_patterns, policy.deny_patterns, policy.ask_patterns):
            if tool_pattern in lst:
                lst.remove(tool_pattern)
                removed = True

        if removed:
            return {"output": f"Removed '{tool_pattern}' from {layer} layer"}
        return {"output": f"Pattern '{tool_pattern}' not found in {layer} layer"}

    async def _tool_list_rules(self) -> dict[str, Any]:
        lines = []
        for name in LAYER_NAMES:
            policy = self._layers.get(name)
            if not policy:
                continue
            rules = []
            if hasattr(policy, "allow_patterns") and policy.allow_patterns:
                rules.append(f"  allow: {', '.join(policy.allow_patterns)}")
            if hasattr(policy, "deny_patterns") and policy.deny_patterns:
                rules.append(f"  deny: {', '.join(policy.deny_patterns)}")
            if hasattr(policy, "ask_patterns") and policy.ask_patterns:
                rules.append(f"  ask: {', '.join(policy.ask_patterns)}")
            if rules:
                lines.append(f"[{name}]")
                lines.extend(rules)
            elif name == "sender":
                sender_rules = self._sender_policy.list_rules()
                if sender_rules:
                    lines.append("[sender]")
                    for sid, r in sender_rules.items():
                        for pat, dec in r.items():
                            lines.append(f"  {sid}: {pat} → {dec}")
            elif name == "sandbox" and isinstance(policy, _SandboxPolicy):
                lines.append("[sandbox]")
                lines.append(f"  mode: context-aware (active when sandbox_mode is set)")
                lines.append(f"  denied: {', '.join(_SandboxPolicy.SANDBOX_DENIED)}")

        if not lines:
            return {"output": "No policy rules configured"}
        return {"output": "\n".join(lines)}

    async def _tool_set_sender(
        self, sender_id: str, tool_pattern: str, decision: str,
    ) -> dict[str, Any]:
        try:
            dec = PolicyDecision(decision)
        except ValueError:
            return {"output": f"Invalid decision: {decision}", "error": f"Invalid decision: {decision}"}
        self._sender_policy.add_rule(sender_id, tool_pattern, dec)
        return {"output": f"Set {sender_id}: {tool_pattern} → {dec.value}"}
