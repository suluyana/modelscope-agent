# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EventKind(str, Enum):
    LLM_TURN = 'llm_turn'
    TOOL_CALL = 'tool_call'
    TOOL_RESULT = 'tool_result'
    FILE_READ = 'file_read'
    FILE_WRITE = 'file_write'
    AGENT_START = 'agent_start'
    AGENT_END = 'agent_end'
    TASK_STATE = 'task_state'


@dataclass(frozen=True)
class TrajectoryEvent:
    id: str
    ts: str
    kind: str
    agent_tag: Optional[str]
    call_id: Optional[str]
    tool_name: Optional[str]
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'ts': self.ts,
            'kind': self.kind,
            'agent_tag': self.agent_tag,
            'call_id': self.call_id,
            'tool_name': self.tool_name,
            'data': self.data,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TrajectoryEvent:
        return cls(
            id=str(d['id']),
            ts=str(d['ts']),
            kind=str(d['kind']),
            agent_tag=d.get('agent_tag'),
            call_id=d.get('call_id'),
            tool_name=d.get('tool_name'),
            data=dict(d.get('data') or {}),
        )


@dataclass
class Trajectory:
    run_id: str
    started_at: str
    agent_tag: Optional[str]
    events: List[TrajectoryEvent] = field(default_factory=list)

    def append(self, event: TrajectoryEvent) -> None:
        self.events.append(event)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'run_id': self.run_id,
            'started_at': self.started_at,
            'agent_tag': self.agent_tag,
            'events': [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Trajectory:
        evs = d.get('events') or []
        return cls(
            run_id=str(d['run_id']),
            started_at=str(d['started_at']),
            agent_tag=d.get('agent_tag'),
            events=[TrajectoryEvent.from_dict(x) for x in evs],
        )
