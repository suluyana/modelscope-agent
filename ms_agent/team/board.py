# Copyright (c) ModelScope Contributors. All rights reserved.
"""Deterministic board helpers: unclaimed pool, parent coverage, tags.

Coordinator never guesses an assignee. Self-claim is tags ∩ capabilities
only; a row with no tags is not in the self-claim pool.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Sequence

from ms_agent.team.models import TeamTask

_DONE = 'completed'


def claim_timeout_s() -> float:
    raw = os.environ.get('MS_AGENT_TEAM_CLAIM_TIMEOUT_S', '600')
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 600.0


def _norm_tags(values: Sequence[str] | None) -> set[str]:
    return {
        str(v).strip().lower()
        for v in (values or [])
        if str(v).strip()
    }


def is_unclaimed(task: TeamTask) -> bool:
    if task.status != 'pending':
        return False
    return not (task.target_at_name or '').lstrip('@').strip()


def can_self_claim(
    task: TeamTask,
    capabilities: Sequence[str] | None,
) -> bool:
    """Unclaimed rows enter the pool only when tags intersect capabilities."""
    if not is_unclaimed(task):
        assigned = (task.target_at_name or '').lstrip('@')
        return bool(assigned)
    tags = _norm_tags(getattr(task, 'tags', None))
    if not tags:
        return False
    return bool(tags & _norm_tags(capabilities))


def parse_ts(value: str | None) -> datetime | None:
    text = (value or '').strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_unclaimed_expired(
    task: TeamTask,
    *,
    now: datetime | None = None,
    timeout_s: float | None = None,
) -> bool:
    if not is_unclaimed(task):
        return False
    limit = claim_timeout_s() if timeout_s is None else timeout_s
    created = parse_ts(task.created_at)
    if created is None:
        return False
    stamp = now or datetime.now(timezone.utc)
    return (stamp - created).total_seconds() >= limit


def parent_completion_blocked(
    tasks: Sequence[TeamTask],
    candidate: TeamTask,
) -> str | None:
    """Why this row cannot be marked completed (children still open)."""
    if candidate.status != _DONE:
        return None
    kids = [
        t for t in tasks
        if t.parent_task_id == candidate.task_id and t.task_id != candidate.task_id
    ]
    if not kids:
        return None
    open_kids = [t for t in kids if t.status != _DONE]
    if not open_kids:
        return None
    ids = ', '.join(t.task_id for t in open_kids[:8])
    return f'parent has unfinished children: {ids}'
