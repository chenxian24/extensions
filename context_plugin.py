"""Context Plugin — advanced context compression strategy."""

from __future__ import annotations

from typing import Any

from agentcore.context.engine import ContextStrategy, ContextEngine
from agentcore.hooks.types import HookContext, HookName
from agentcore.plugins.base import Plugin, PluginContext


class HermesCompressionStrategy(ContextStrategy):
    """Head/tail protection with middle compression.

    - First N messages (system/early context) are always kept
    - Last M messages (recent context) are always kept
    - Middle messages are compressed: keep every K-th message,
      mark others for summarization
    """

    @property
    def name(self) -> str:
        return "hermes_compression"

    def __init__(self, head_protect: int = 3, tail_protect: int = 10, sample_every: int = 3) -> None:
        self._head = head_protect
        self._tail = tail_protect
        self._sample = sample_every

    def compress(self, messages, max_tokens, system_prompt_tokens=0):
        from agentcore.core.message import Message

        if len(messages) <= self._head + self._tail:
            return messages

        head = messages[: self._head]
        tail = messages[-self._tail :]
        middle = messages[self._head : -self._tail]

        # Sample middle messages: keep every N-th
        sampled = middle[:: self._sample]

        # Estimate tokens
        all_msgs = head + sampled + tail
        estimated = sum(len(m.content) // 4 for m in all_msgs) + system_prompt_tokens

        if estimated <= max_tokens:
            return all_msgs

        # If still over budget, trim sampled further
        available = max_tokens - system_prompt_tokens - sum(len(m.content) // 4 for m in head + tail)
        result_middle: list[Any] = []
        used = 0
        for msg in reversed(sampled):
            cost = len(msg.content) // 4
            if used + cost > available:
                break
            result_middle.insert(0, msg)
            used += cost

        return head + result_middle + tail


class ContextPlugin(Plugin):
    """Registers hermes_compression strategy and optional POST_BUILD_MESSAGES hook."""

    @property
    def name(self) -> str:
        return "context"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Advanced context compression with head/tail protection"

    def __init__(self, head_protect: int = 3, tail_protect: int = 10, sample_every: int = 3) -> None:
        self._head = head_protect
        self._tail = tail_protect
        self._sample = sample_every

    async def setup(self, ctx: PluginContext) -> None:
        strategy = HermesCompressionStrategy(
            head_protect=self._head,
            tail_protect=self._tail,
            sample_every=self._sample,
        )
        ctx.context.register_strategy(strategy)

    async def on_engine_ready(self, ctx: PluginContext) -> None:
        pass
