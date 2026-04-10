# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ms_agent.trajectory.models import EventKind, Trajectory, TrajectoryEvent
from ms_agent.trajectory.store import TrajectoryStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryCollector:
    """Builds TrajectoryEvent instances and persists via TrajectoryStore."""

    def __init__(
        self,
        run_id: str,
        output_dir: str,
        agent_tag: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        self._agent_tag = agent_tag
        self.started_at = _now_iso()
        self.store = TrajectoryStore(output_dir, run_id)
        self.trajectory = Trajectory(
            run_id=run_id,
            started_at=self.started_at,
            agent_tag=agent_tag,
            events=[],
        )

    def _emit(
        self,
        kind: EventKind,
        data: Dict[str, Any],
        *,
        agent_tag: Optional[str] = None,
        call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        ev = TrajectoryEvent(
            id=uuid.uuid4().hex,
            ts=_now_iso(),
            kind=kind.value,
            agent_tag=agent_tag if agent_tag is not None else self._agent_tag,
            call_id=call_id,
            tool_name=tool_name,
            data=data,
        )
        self.trajectory.append(ev)
        self.store.append_event(
            ev,
            started_at=self.started_at,
            agent_tag=self._agent_tag,
        )

    def emit_llm_turn(
        self,
        messages: List[Dict[str, Any]],
        call_id: Optional[str] = None,
    ) -> None:
        self._emit(
            EventKind.LLM_TURN,
            {'messages': messages},
            call_id=call_id,
        )

    def emit_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        call_id: Optional[str] = None,
        agent_tag: Optional[str] = None,
    ) -> None:
        self._emit(
            EventKind.TOOL_CALL,
            {'arguments': args},
            agent_tag=agent_tag,
            call_id=call_id,
            tool_name=tool_name,
        )

    def emit_tool_result(
        self,
        tool_name: str,
        result: str,
        call_id: Optional[str] = None,
        agent_tag: Optional[str] = None,
    ) -> None:
        self._emit(
            EventKind.TOOL_RESULT,
            {'result': result},
            agent_tag=agent_tag,
            call_id=call_id,
            tool_name=tool_name,
        )

    def emit_file_op(
        self,
        kind: EventKind,
        path: str,
        agent_tag: Optional[str] = None,
    ) -> None:
        if kind not in (EventKind.FILE_READ, EventKind.FILE_WRITE):
            raise ValueError('emit_file_op expects FILE_READ or FILE_WRITE')
        self._emit(kind, {'path': path}, agent_tag=agent_tag)

    def emit_agent_start(
        self,
        sub_agent_tag: Optional[str],
        tool_name: str,
        call_id: Optional[str] = None,
    ) -> None:
        self._emit(
            EventKind.AGENT_START,
            {'sub_agent_tag': sub_agent_tag},
            agent_tag=sub_agent_tag,
            call_id=call_id,
            tool_name=tool_name,
        )

    def emit_agent_end(
        self,
        sub_agent_tag: Optional[str],
        tool_name: str,
        status: str,
        call_id: Optional[str] = None,
    ) -> None:
        self._emit(
            EventKind.AGENT_END,
            {'sub_agent_tag': sub_agent_tag, 'status': status},
            agent_tag=sub_agent_tag,
            call_id=call_id,
            tool_name=tool_name,
        )

    def emit_task_state(
        self,
        task_id: str,
        tool_name: str,
        status: str,
    ) -> None:
        self._emit(
            EventKind.TASK_STATE,
            {'task_id': task_id, 'status': status},
            tool_name=tool_name,
        )

    def close(self) -> None:
        self.store.close()
