# Copyright (c) ModelScope Contributors. All rights reserved.
"""Cross-process-safe append to trajectory JSONL (for external hooks).

Claude Code / shell hooks spawn a new Python process per invocation; this module
uses file locking so the first writer can emit the header line safely.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trajectory_path(output_dir: str, run_id: str) -> str:
    traj_dir = os.path.join(output_dir, 'trajectories')
    os.makedirs(traj_dir, exist_ok=True)
    safe = run_id.replace('/', '_').replace('\\', '_')
    return os.path.join(traj_dir, f'{safe}.jsonl')


def _flock_lock(fp) -> None:
    try:
        import fcntl  # type: ignore

        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass


def _flock_unlock(fp) -> None:
    try:
        import fcntl  # type: ignore

        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


def append_trajectory_event(
    output_dir: str,
    run_id: str,
    *,
    kind: str,
    tool_name: Optional[str] = None,
    call_id: Optional[str] = None,
    agent_tag: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    header_agent_tag: Optional[str] = None,
) -> str:
    """Append one :class:`TrajectoryEvent` line; write header if file is new.

    Returns the path of the JSONL file.
    """
    path = _trajectory_path(output_dir, run_id)
    payload = {
        'id': uuid.uuid4().hex,
        'ts': _now_iso(),
        'kind': kind,
        'agent_tag': agent_tag,
        'call_id': call_id,
        'tool_name': tool_name,
        'data': dict(data or {}),
    }
    line = json.dumps(payload, ensure_ascii=False) + '\n'

    with open(path, 'a+', encoding='utf-8') as fp:
        _flock_lock(fp)
        try:
            fp.seek(0, os.SEEK_END)
            if fp.tell() == 0:
                header = {
                    'type': 'header',
                    'run_id': run_id,
                    'started_at': _now_iso(),
                    'agent_tag': header_agent_tag,
                }
                fp.write(json.dumps(header, ensure_ascii=False) + '\n')
            fp.write(line)
            fp.flush()
        finally:
            _flock_unlock(fp)
    return path
