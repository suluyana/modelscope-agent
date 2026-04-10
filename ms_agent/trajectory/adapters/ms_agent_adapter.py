# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

from ms_agent.trajectory.adapters.base import BaseAdapter
from ms_agent.trajectory.collector import TrajectoryCollector


class MsAgentAdapter(BaseAdapter):
    """Wires AgentTool trajectory forwarding and TaskManager task events."""

    def __init__(
        self,
        agent_tools: Sequence[Any],
        task_manager: Any,
    ) -> None:
        self._agent_tools: List[Any] = list(agent_tools)
        self._task_manager = task_manager
        self._collector: Optional[TrajectoryCollector] = None
        self._orig_complete: Optional[Callable[..., Any]] = None
        self._orig_fail: Optional[Callable[..., Any]] = None

    @classmethod
    def wire(
        cls,
        agent_tools: Sequence[Any],
        task_manager: Any,
        collector: TrajectoryCollector,
    ) -> 'MsAgentAdapter':
        adapter = cls(agent_tools, task_manager)
        adapter.attach(collector)
        return adapter

    def attach(self, collector: TrajectoryCollector) -> None:
        self.detach()
        self._collector = collector
        for tool in self._agent_tools:
            if hasattr(tool, 'set_trajectory_collector'):
                tool.set_trajectory_collector(collector)

        tm = self._task_manager
        orig_c = tm.complete
        orig_f = tm.fail
        self._orig_complete = orig_c
        self._orig_fail = orig_f
        coll = collector

        async def complete(task_id: str, result: str) -> None:
            await orig_c(task_id, result)
            task = tm.get_task(task_id)
            if task and coll:
                coll.emit_task_state(task_id, task.tool_name, task.status)

        async def fail(task_id: str, error: str) -> None:
            await orig_f(task_id, error)
            task = tm.get_task(task_id)
            if task and coll:
                coll.emit_task_state(task_id, task.tool_name, task.status)

        tm.complete = complete  # type: ignore[method-assign]
        tm.fail = fail  # type: ignore[method-assign]

    def detach(self) -> None:
        if self._orig_complete is not None:
            self._task_manager.complete = self._orig_complete  # type: ignore[method-assign]
            self._task_manager.fail = self._orig_fail  # type: ignore[method-assign]
            self._orig_complete = None
            self._orig_fail = None
        for tool in self._agent_tools:
            if hasattr(tool, 'set_trajectory_collector'):
                tool.set_trajectory_collector(None)
        self._collector = None
