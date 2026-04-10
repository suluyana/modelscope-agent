# Copyright (c) ModelScope Contributors. All rights reserved.
"""Claude Code adapter (Tier B stub).

Hook points to map in Tier C (verify against current CC docs):

+----------------------+---------------------------+--------------------------------+
| Upstream             | Suggested EventKind       | Notes                          |
+----------------------+---------------------------+--------------------------------+
| PostToolUse          | TOOL_CALL / TOOL_RESULT   | args + result payload          |
| Stop / session end   | AGENT_END or LLM_TURN     | session boundary               |
| PreToolUse           | TOOL_CALL                 | before execution               |
+----------------------+---------------------------+--------------------------------+

Set ``TrajectoryEvent.data["framework"]`` to ``"claude_code"`` when emitting.

**Shipped integration:** see ``contrib/claude-code-settings.trajectory.example.json``
and ``python -m ms_agent.trajectory cc-hook``.
"""
from __future__ import annotations

from ms_agent.trajectory.adapters.base import BaseAdapter
from ms_agent.trajectory.collector import TrajectoryCollector


class ClaudeCodeAdapter(BaseAdapter):
    def attach(self, collector: TrajectoryCollector) -> None:
        raise NotImplementedError(
            'ClaudeCodeAdapter.attach is Tier C; install hooks that call collector.emit_*'
        )

    def detach(self) -> None:
        pass
