# Copyright (c) ModelScope Contributors. All rights reserved.
"""OpenCode adapter (Tier B stub).

Expected upstream events → EventKind (Tier C verify):

+----------------------+---------------------------+
| Event / stream       | EventKind                 |
+----------------------+---------------------------+
| tool_invocation      | TOOL_CALL                 |
| tool_output          | TOOL_RESULT               |
| message_delta        | LLM_TURN (optional)       |
| file_read/write      | FILE_READ / FILE_WRITE    |
+----------------------+---------------------------+

Transport may be JSONL, WebSocket, or stdio; confirm in OpenCode docs.
"""
from __future__ import annotations

from ms_agent.trajectory.adapters.base import BaseAdapter
from ms_agent.trajectory.collector import TrajectoryCollector


class OpenCodeAdapter(BaseAdapter):
    def attach(self, collector: TrajectoryCollector) -> None:
        raise NotImplementedError(
            'OpenCodeAdapter.attach is Tier C; subscribe to opencode event stream'
        )

    def detach(self) -> None:
        pass
