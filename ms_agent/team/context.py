# Copyright (c) ModelScope Contributors. All rights reserved.
"""Assemble context_bundle before dispatch.

Timeline is for humans. Models get a role-specific projection:
workers get the user prompt (+ own brief); Lead gets a pointer to the
task board (not the rows) and pulls via tools.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Sequence

from ms_agent.team.models import ContextBundle, TimelineMessage, TeamTask

Audience = Literal['worker', 'lead']

SUMMARY_LIMIT = 500

# ACP / Codex often prefixes every reply with this; it must not eat the
# 500-char snapshot budget or show up as "the result" to Lead.
_MODEL_META_WARNING = re.compile(
    r'^Warning: Model metadata for `[^\n`]+` not found\.[^\n]*\n+',
    re.IGNORECASE,
)


def sanitize_agent_text(text: str | None) -> str:
    value = (text or '').strip()
    while True:
        nxt = _MODEL_META_WARNING.sub('', value, count=1).lstrip()
        if nxt == value:
            return nxt
        value = nxt


def final_text_from_dispatch_events(events: Sequence[Any]) -> str:
    """Teammate's final assistant reply from a dispatch log (C-05 index).

    Joins ``team.stream`` text deltas and prefers ``team.dispatch_done.summary``.
    Tool-call JSON / thinking are skipped so Lead can read the result without
    inheriting the private stream.
    """
    done_summary = ''
    parts: list[str] = []
    for ev in events or []:
        et = getattr(ev, 'type', None) or (
            ev.get('type') if isinstance(ev, dict) else '')
        payload = getattr(ev, 'payload', None)
        if payload is None and isinstance(ev, dict):
            payload = ev.get('payload')
        payload = payload or {}
        if et == 'team.dispatch_done':
            done_summary = str(payload.get('summary') or '')
            continue
        if et != 'team.stream':
            continue
        if str(payload.get('type') or '') != 'text':
            continue
        chunk = str(payload.get('content') or '')
        if chunk:
            parts.append(chunk)
    return sanitize_agent_text(done_summary or ''.join(parts))


def truncate_summary(text: str | None, limit: int = SUMMARY_LIMIT) -> str:
    value = sanitize_agent_text(text)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + '…'


def format_dispatch_receipt(
    *,
    at_name: str,
    session_mode: str,
    session_resolution: str | None = None,
) -> str:
    extra = f' · {session_resolution}' if session_resolution else ''
    return f'已派 @{at_name} · {session_mode}{extra}'


def format_done_receipt(
    *,
    at_name: str,
    ok: bool,
    error_code: str | None = None,
) -> str:
    """Idle-style completion notice. No worker transcript, no LLM summary."""
    if ok:
        return f'@{at_name} 已结束执行'
    code = (error_code or '').strip()
    if code:
        return f'@{at_name} 已结束执行（失败） · {code[:80]}'
    return f'@{at_name} 已结束执行（失败）'


class ContextBundleAssembler:
    """Build referential context for DispatchEnvelope.

    Caps (from requirements):
    - thread: last 10 messages (human-only, never other agents' full replies)
    - Lead prompt: board pointer only (rows stay behind task_board_read)
    """

    THREAD_LIMIT = 10
    SNAPSHOT_LIMIT = 20

    @classmethod
    def build(
        cls,
        *,
        audience: Audience = 'worker',
        thread_messages: Sequence[TimelineMessage] | Sequence[str] | None = None,
        project_timeline: Sequence[TimelineMessage] | Sequence[str] | None = None,
        git_snapshot: dict[str, Any] | None = None,
        referenced_task_id: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        deployment_context: dict[str, Any] | None = None,
        task_snapshots: Sequence[dict[str, Any]] | None = None,
        task_inbox: Sequence[dict[str, Any]] | None = None,
        include_project_timeline: bool = False,
    ) -> ContextBundle:
        timeline = (
            cls._as_text_list(project_timeline, cls.THREAD_LIMIT)
            if include_project_timeline else [])
        human_thread = cls._human_thread(thread_messages, cls.THREAD_LIMIT)
        snapshots = list(task_snapshots or [])[-cls.SNAPSHOT_LIMIT:]
        inbox = list(task_inbox or [])[-cls.SNAPSHOT_LIMIT:]
        return ContextBundle(
            audience=audience,
            thread_messages=human_thread if audience == 'lead' else [],
            project_timeline=timeline,
            git_snapshot=git_snapshot,
            referenced_task_id=referenced_task_id,
            artifacts=list(artifacts or []),
            deployment_context=deployment_context,
            task_snapshots=snapshots if audience == 'lead' else [],
            task_inbox=inbox if audience == 'worker' else [],
        )

    @staticmethod
    def snapshots_from_tasks(tasks: Sequence[TeamTask]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in list(tasks)[-ContextBundleAssembler.SNAPSHOT_LIMIT:]:
            rows.append({
                'task_id': task.task_id,
                'project_id': task.project_id,
                'at_name': task.target_at_name,
                'status': task.status,
                'last_dispatch_id': task.last_dispatch_id,
                # Idle board: never copy Worker transcript into Lead / other
                # agents. result_summary is opt-in via task_board_write (P1).
                'summary_truncated': '',
            })
        return rows

    @staticmethod
    def inbox_from_tasks(
        tasks: Sequence[TeamTask],
        *,
        at_name: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in tasks:
            if task.target_at_name and task.target_at_name != at_name:
                continue
            rows.append({
                'task_id': task.task_id,
                'status': task.status,
                'prompt_preview': truncate_summary(task.prompt, 120),
                'blocked_by': list(task.blocked_by or []),
            })
        return rows[-ContextBundleAssembler.SNAPSHOT_LIMIT:]

    @staticmethod
    def _human_thread(
        items: Sequence[TimelineMessage] | Sequence[str] | None,
        limit: int,
    ) -> list[str]:
        if not items:
            return []
        texts: list[str] = []
        for item in items:
            if isinstance(item, TimelineMessage):
                if item.sender_type != 'human':
                    continue
                texts.append(
                    f'[{item.created_at}] {item.sender_name}: {item.content}')
            else:
                texts.append(str(item))
        return texts[-limit:]

    @staticmethod
    def _as_text_list(
        items: Sequence[TimelineMessage] | Sequence[str] | None,
        limit: int,
    ) -> list[str]:
        if not items:
            return []
        texts: list[str] = []
        for item in items:
            if isinstance(item, TimelineMessage):
                texts.append(
                    f'[{item.created_at}] {item.sender_name}: {item.content}')
            else:
                texts.append(str(item))
        return texts[-limit:]

    @classmethod
    def merge_prompt(cls, prompt: str, bundle: ContextBundle) -> str:
        """Render a human-readable prompt prefix for local / cloud agents."""
        sections: list[str] = []
        if bundle.referenced_task_id:
            sections.append(
                f'[referenced_task_id] {bundle.referenced_task_id}')
        if bundle.task_inbox:
            lines = []
            for row in bundle.task_inbox:
                lines.append(
                    f"- {row.get('status')} {row.get('task_id')}: "
                    f"{row.get('prompt_preview')}")
            sections.append('[task_inbox]\n' + '\n'.join(lines))
        if bundle.task_snapshots:
            project_id = next(
                (str(row.get('project_id') or '')
                 for row in bundle.task_snapshots if row.get('project_id')),
                '',
            )
            header = (
                f'[task_board] project={project_id}'
                if project_id else '[task_board]')
            # Pointer only — do not inline status rows. A long board would
            # dominate every Lead turn, and the rows duplicate task_board_read.
            sections.append(
                header + '\n'
                'Other agents\' running status is not in this context. '
                'Call task_board_read with this project_id for the index '
                '(status, @at_name, last_dispatch_id). '
                'To read a teammate\'s final reply, call dispatch_result_read '
                'with last_dispatch_id (works even if they did not write a file). '
                'Do not search the workspace with find/ls/glob for their output. '
                'Do not re-run completed tasks; report status only.')
        if bundle.thread_messages:
            sections.append('[thread_messages]\n'
                            + '\n'.join(bundle.thread_messages))
        if bundle.project_timeline:
            sections.append('[project_timeline]\n'
                            + '\n'.join(bundle.project_timeline))
        if bundle.git_snapshot:
            sections.append(f'[git_snapshot] {bundle.git_snapshot}')
        if bundle.artifacts:
            sections.append(f'[artifacts] {bundle.artifacts}')
        if bundle.deployment_context:
            sections.append(
                f'[deployment_context] {bundle.deployment_context}')
        if not sections:
            return prompt
        header = (
            '# Context (assembled by platform)\n\n' + '\n\n'.join(sections))
        if not (prompt or '').strip():
            return header
        return header + f'\n\n# User prompt\n\n{prompt}'
