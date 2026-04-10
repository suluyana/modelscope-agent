# Copyright (c) ModelScope Contributors. All rights reserved.
"""Codex CLI adapter (Tier B stub).

Tier C: confirm whether the Codex CLI exposes tool-level callbacks or only
aggregated logs. If no hooks, trajectory may be limited to LLM_TURN from
parsed transcripts (lower confidence).
"""
from __future__ import annotations

from ms_agent.trajectory.adapters.base import BaseAdapter
from ms_agent.trajectory.collector import TrajectoryCollector


class CodexAdapter(BaseAdapter):
    def attach(self, collector: TrajectoryCollector) -> None:
        raise NotImplementedError('CodexAdapter.attach is Tier C')

    def detach(self) -> None:
        pass
