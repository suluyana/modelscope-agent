# Copyright (c) ModelScope Contributors. All rights reserved.
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TextIO

from ms_agent.trajectory.models import Trajectory, TrajectoryEvent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryStore:
    """Append-only JSONL: header, events, footer on close. Thread-safe."""

    def __init__(self, output_dir: str, run_id: str) -> None:
        self._output_dir = output_dir
        self._run_id = run_id
        self._lock = threading.Lock()
        self._file: Optional[TextIO] = None
        self._header_written = False
        self._event_count = 0
        self._started_at: Optional[str] = None
        self._agent_tag: Optional[str] = None
        self._closed = False

        traj_dir = os.path.join(output_dir, 'trajectories')
        os.makedirs(traj_dir, exist_ok=True)
        safe = run_id.replace('/', '_').replace('\\', '_')
        self._path = os.path.join(traj_dir, f'{safe}.jsonl')

    @property
    def path(self) -> str:
        return self._path

    def _ensure_open_append(self) -> TextIO:
        if self._file is None:
            self._file = open(self._path, 'a', encoding='utf-8')
        return self._file

    def append_event(
        self,
        event: TrajectoryEvent,
        *,
        started_at: str,
        agent_tag: Optional[str],
    ) -> None:
        if self._closed:
            return
        with self._lock:
            if self._closed:
                return
            self._started_at = self._started_at or started_at
            if self._agent_tag is None and agent_tag is not None:
                self._agent_tag = agent_tag
            f = self._ensure_open_append()
            if not self._header_written:
                header = {
                    'type': 'header',
                    'run_id': self._run_id,
                    'started_at': self._started_at,
                    'agent_tag': self._agent_tag,
                }
                f.write(json.dumps(header, ensure_ascii=False) + '\n')
                f.flush()
                self._header_written = True
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + '\n')
            f.flush()
            self._event_count += 1

    def load(self) -> Trajectory:
        events: List[TrajectoryEvent] = []
        started_at = ''
        agent_tag: Optional[str] = None
        rid = self._run_id

        if not os.path.isfile(self._path):
            return Trajectory(
                run_id=rid,
                started_at=started_at,
                agent_tag=agent_tag,
                events=[],
            )

        with self._lock:
            with open(self._path, 'r', encoding='utf-8') as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj: Dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = obj.get('type')
                    if t == 'header':
                        rid = str(obj.get('run_id', rid))
                        started_at = str(obj.get('started_at', ''))
                        at = obj.get('agent_tag')
                        agent_tag = at if at else None
                    elif t == 'footer':
                        continue
                    elif 'kind' in obj:
                        events.append(TrajectoryEvent.from_dict(obj))

        return Trajectory(
            run_id=rid,
            started_at=started_at,
            agent_tag=agent_tag,
            events=events,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._file is not None:
                try:
                    footer = {
                        'type': 'footer',
                        'total_events': self._event_count,
                        'ended_at': _now_iso(),
                    }
                    self._file.write(
                        json.dumps(footer, ensure_ascii=False) + '\n')
                    self._file.flush()
                except Exception:
                    pass
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
