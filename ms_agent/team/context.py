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


_CLARIFY_MARKERS = (
    '请问',
    '请告诉我',
    '请给出',
    '请确认',
    '请补充',
    '哪个文档',
    '哪一个文件',
    'could you',
    'can you provide',
    'please provide',
    'please tell me',
    'which file',
    'which document',
    'which path',
    'need the path',
    'need the file',
    'absolute path',
    'not enough information',
    '需要你提供',
)

_CLARIFY_STRONG = (
    '需要你提供',
    'not enough information',
    'need the path',
    'need the file',
)


def looks_like_clarification(
    text: str | None,
    *,
    audience: str = 'worker',
) -> bool:
    """True when the agent asked for a missing fact instead of finishing.

    Completion does not use this. Explicit questions use ``task_ask``.
    ``audience`` is accepted for leftover callers.
    """
    del audience
    value = sanitize_agent_text(text)
    if len(value) < 12:
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in _CLARIFY_STRONG):
        return True
    asked = ('?' in value) or ('？' in value)
    if not asked:
        return False
    return any(marker in lowered for marker in _CLARIFY_MARKERS)


def sanitize_agent_text(text: str | None) -> str:
    value = (text or '').strip()
    while True:
        nxt = _MODEL_META_WARNING.sub('', value, count=1).lstrip()
        if nxt == value:
            return nxt
        value = nxt


RESULT_PREVIEW_LIMIT = 1200


def final_text_from_dispatch_events(events: Sequence[Any]) -> str:
    """Teammate's final assistant reply from a dispatch log (C-05 index).

    Joins ``team.stream`` text deltas. ``dispatch_done.summary`` is a fallback
    only — cloud runners truncate that field (last 4k), so preferring it would
    drop the original. Tool-call JSON / thinking are skipped.
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
    joined = sanitize_agent_text(''.join(parts))
    if joined:
        return joined
    return sanitize_agent_text(done_summary)


def task_result_view(
    task: TeamTask,
    events: Sequence[Any] | None = None,
    *,
    preview_limit: int = RESULT_PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Human-facing original result for a board row (not an LLM rewrite).

    Prefer the worker's own ``result_summary`` when they wrote one; otherwise
    the dispatch-log assistant text — the same source as ``dispatch_result_read``.
    """
    summary = sanitize_agent_text(task.result_summary)
    if summary:
        text, source = summary, 'result_summary'
    else:
        text = final_text_from_dispatch_events(events or [])
        source = 'dispatch_log' if text else None
    truncated = bool(text) and len(text) > preview_limit
    if truncated:
        # Cards need the ending (the deliverable), not the opening plan.
        preview = '…' + text[-preview_limit:].lstrip()
    else:
        preview = text
    return {
        'result_text': preview,
        'result_source': source,
        'result_truncated': truncated,
    }


def truncate_summary(text: str | None, limit: int = SUMMARY_LIMIT) -> str:
    value = sanitize_agent_text(text)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + '…'


def session_mode_label(
    session_mode: str,
    session_resolution: str | None = None,
) -> str:
    """Human label for attach vs fresh. Never imply a successful attach."""
    resolution = (session_resolution or '').strip()
    mode = (session_mode or '').strip()
    if resolution == 'bound':
        return '续已有会话'
    if resolution == 'attach_fallback_fresh':
        return '续会话失败，已新开'
    if resolution == 'forced_fresh':
        return '未能续会话，已新开'
    if mode == 'fresh' or resolution == 'created':
        return '新开会话'
    if mode == 'attach':
        return '尝试续已有会话'
    if mode == 'auto':
        return '自动选择会话'
    return mode or '未知会话'


def format_dispatch_receipt(
    *,
    at_name: str,
    session_mode: str,
    session_resolution: str | None = None,
) -> str:
    label = session_mode_label(session_mode, session_resolution)
    return f'已派 @{at_name} · {label}'


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
        project_id: str | None = None,
        task_snapshots: Sequence[dict[str, Any]] | None = None,
        task_inbox: Sequence[dict[str, Any]] | None = None,
        roster_lines: Sequence[str] | None = None,
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
            project_id=project_id if audience == 'lead' else None,
            thread_messages=human_thread if audience == 'lead' else [],
            project_timeline=timeline,
            git_snapshot=git_snapshot,
            referenced_task_id=referenced_task_id,
            artifacts=list(artifacts or []),
            deployment_context=deployment_context,
            task_snapshots=snapshots if audience == 'lead' else [],
            task_inbox=inbox if audience == 'worker' else [],
            roster_lines=list(roster_lines or []) if audience == 'lead' else [],
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
        capabilities: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        want = (at_name or '').lstrip('@')
        by_id = {t.task_id: t for t in tasks}
        rows: list[dict[str, Any]] = []
        from ms_agent.team.board import can_self_claim
        for task in tasks:
            if task.status not in ('pending', 'in_progress', 'wait_human'):
                continue
            assigned = (task.target_at_name or '').lstrip('@')
            if assigned and assigned != want:
                continue
            if not assigned and not can_self_claim(task, capabilities):
                continue
            blockers_ok = True
            for bid in list(task.blocked_by or []):
                blocker = by_id.get(bid)
                if blocker is None or blocker.status != 'completed':
                    blockers_ok = False
                    break
            rows.append({
                'task_id': task.task_id,
                'status': task.status,
                'prompt_preview': truncate_summary(task.prompt, 120),
                'blocked_by': list(task.blocked_by or []),
                'claim_version': int(getattr(task, 'claim_version', 0) or 0),
                'parent_task_id': getattr(task, 'parent_task_id', None),
                'tags': list(getattr(task, 'tags', None) or []),
                'claimable': (
                    task.status == 'pending' and blockers_ok and (
                        assigned == want
                        or can_self_claim(task, capabilities))),
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
        if bundle.audience != 'lead':
            sections.append(
                'This turn is one-shot. If a required path or fact is missing, '
                'call task_ask (defaults to Lead) and stop. A question in '
                'the reply is treated as a finished answer, not a ticket.')
        if getattr(bundle, 'roster_lines', None):
            sections.append(
                '[roster]\n' + '\n'.join(bundle.roster_lines) + '\n'
                'Display only for splitting work. Do not auto-assign by accuracy.')
        if bundle.task_inbox:
            lines = []
            for row in bundle.task_inbox:
                lines.append(
                    f"- {row.get('status')} {row.get('task_id')}: "
                    f"{row.get('prompt_preview')}")
            sections.append('[task_inbox]\n' + '\n'.join(lines))
        project_id = str(getattr(bundle, 'project_id', None) or '') or next(
            (str(row.get('project_id') or '')
             for row in bundle.task_snapshots if row.get('project_id')),
            '',
        )
        show_board = (
            (bundle.audience == 'lead' and bool(project_id))
            or bool(bundle.task_snapshots)
        )
        if show_board:
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
                'Do not re-run completed tasks; report status only. '
                'wait_human rows with a result_summary are questions — fill the '
                'missing fact into that prompt and set status=pending. '
                'If YOU are missing a fact, call task_ask with '
                'audience=user and do not write teammate rows. Brace tokens '
                'in the user message may be the work itself — copy them if '
                'that is the job.')
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
