# Copyright (c) ModelScope Contributors. All rights reserved.
"""Deterministic Coordinator: board DAG → dispatch. Not an LLM."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Sequence

from ms_agent.team.board import (
    claim_timeout_s as default_claim_timeout_s,
    is_unclaimed_expired,
)
from ms_agent.team.errors import TeamError
from ms_agent.team.models import TeamTask

_DONE = 'completed'
_INFLIGHT = frozenset({'in_progress', 'waiting_approval'})
_PENDING = 'pending'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_ready_tasks(
    tasks: Sequence[TeamTask],
    *,
    max_global: int | None = None,
) -> tuple[list[TeamTask], list[tuple[TeamTask, str]]]:
    """Pick pending tasks whose blockers are completed.

    Same endpoint is serial; different endpoints may run in parallel.
    A failed blocker keeps dependents blocked (does not rerun neighbors).
    """
    by_id = {t.task_id: t for t in tasks}
    inflight = [t for t in tasks if t.status in _INFLIGHT]
    busy_eps = {
        t.target_endpoint_id for t in inflight if t.target_endpoint_id}
    busy_ats = {
        (t.target_at_name or '').lstrip('@')
        for t in inflight if t.target_at_name}
    inflight_n = len(inflight)
    ready: list[TeamTask] = []
    skipped: list[tuple[TeamTask, str]] = []

    for task in tasks:
        if task.status == 'wait_human':
            skipped.append((task, 'wait_human'))
            continue
        if task.status != _PENDING:
            continue
        if max_global is not None and inflight_n + len(ready) >= max_global:
            skipped.append((task, 'queued_slot'))
            continue
        at = (task.target_at_name or '').lstrip('@')
        if not at:
            skipped.append((task, 'unclaimed'))
            continue
        unresolved = False
        for bid in list(task.blocked_by or []):
            blocker = by_id.get(bid)
            if blocker is None or blocker.status != _DONE:
                unresolved = True
                break
        if unresolved:
            skipped.append((task, 'blocked'))
            continue
        ep = task.target_endpoint_id
        if (ep and ep in busy_eps) or (at and at in busy_ats):
            skipped.append((task, 'endpoint_busy'))
            continue
        if ep and any(r.target_endpoint_id == ep for r in ready):
            skipped.append((task, 'endpoint_busy'))
            continue
        if at and any(
                (r.target_at_name or '').lstrip('@') == at for r in ready):
            skipped.append((task, 'endpoint_busy'))
            continue
        ready.append(task)
    return ready, skipped


class Coordinator:
    """Scan the board and enqueue. Never writes into the Lead user prompt."""

    def __init__(
        self,
        ingress,
        task_store=None,
        *,
        enabled: bool = True,
        max_inflight_global: int = 8,
        claim_timeout_s: float | None = None,
    ) -> None:
        self.ingress = ingress
        self.tasks = task_store if task_store is not None else getattr(
            ingress, 'tasks', None)
        self.enabled = enabled
        self.max_inflight_global = max_inflight_global
        self.claim_timeout_s = (
            claim_timeout_s if claim_timeout_s is not None
            else default_claim_timeout_s())
        self._run_lock = asyncio.Lock()
        ingress.coordinator = self

    async def on_dispatch_done(self, project_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {
                'dispatched': [],
                'skipped': [],
                'skipped_reason': 'disabled',
            }
        return await self.run(project_id)

    async def run(
        self,
        project_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if not force and not self.enabled:
            return {
                'dispatched': [],
                'skipped': [],
                'skipped_reason': 'disabled',
            }
        if self.tasks is None:
            return {
                'dispatched': [],
                'skipped': [],
                'skipped_reason': 'no_store',
            }
        async with self._run_lock:
            result = await self._run_locked(project_id)
        pending_ids = list(result.get('unclaimed_escalated') or [])
        floated: list[str] = []
        escalate = getattr(self.ingress, 'escalate_unclaimed', None)
        if callable(escalate):
            for tid in pending_ids:
                task = self.tasks.get(tid) if self.tasks is not None else None
                if task is None:
                    continue
                msg = escalate(task)
                if msg is not None:
                    await self.ingress.handle(msg)
                floated.append(tid)
        result['unclaimed_escalated'] = floated
        return result

    async def _run_locked(self, project_id: str) -> dict[str, Any]:
        board = self.tasks.list(project_id)
        ready, skipped = select_ready_tasks(
            board, max_global=self.max_inflight_global)
        dispatched: list[dict[str, Any]] = []
        extra_skipped: list[tuple[TeamTask, str]] = []
        for task in ready:
            try:
                claimed = self.tasks.claim(
                    task.task_id,
                    claimant_at_name=str(task.target_at_name or ''),
                    expected_version=int(task.claim_version or 0),
                )
            except TeamError:
                extra_skipped.append((task, 'claim_conflict'))
                continue
            result = await self.ingress.dispatch_board_task(claimed)
            if getattr(result, 'error', None):
                claimed.status = _PENDING
                claimed.updated_at = _now_iso()
                self.tasks.upsert(claimed)
                extra_skipped.append((task, 'dispatch_error'))
                continue
            dispatched.append({
                'task_id': claimed.task_id,
                'dispatch_ids': [
                    d.dispatch_id for d in (result.dispatches or [])
                ],
            })
        all_skipped = skipped + extra_skipped
        return {
            'dispatched': dispatched,
            'skipped': [
                {'task_id': t.task_id, 'reason': reason}
                for t, reason in all_skipped
            ],
            'unclaimed_escalated': self._expired_unclaimed_ids(board),
        }

    def _expired_unclaimed_ids(self, board: Sequence[TeamTask]) -> list[str]:
        ids: list[str] = []
        for task in board:
            if not is_unclaimed_expired(
                    task, timeout_s=self.claim_timeout_s):
                continue
            ctx = dict(task.deployment_context or {})
            if ctx.get('unclaimed_escalated'):
                continue
            ids.append(task.task_id)
        return ids
