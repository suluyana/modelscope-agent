# Copyright (c) ModelScope Contributors. All rights reserved.
"""Dispatch / timeline / task board / project message APIs."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ms_agent.team.models import (
    InboundMessage,
    TeamProjectMeta,
    TeamTask,
    ThreadBinding,
    new_id,
)
from team.cloud_runner import CloudAgentRuntime
from team.state import get_team_state
from team.wait import wait_for_dispatches
from team.ws_bridge_hub import get_bridge_hub

router = APIRouter(tags=['team-dispatch'])


class ProjectUpsert(BaseModel):
    project_id: Optional[str] = None
    name: str
    workspace_path: str = ''
    default_lead_at: Optional[str] = None
    plan_gate: Optional[bool] = None
    release_config: dict[str, Any] = Field(default_factory=dict)
    members: list[dict[str, str]] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    content: str
    sender_user_id: str
    channel: str = 'web'
    thread_id: Optional[str] = None
    chat_id: Optional[str] = None
    operation_kind: str = 'write'
    referenced_task_id: Optional[str] = None
    mentions: list[str] = Field(default_factory=list)
    session_mode: str = 'auto'
    target_at_name: Optional[str] = None


class BindThreadRequest(BaseModel):
    chat_id: str
    thread_id: str
    project_id: str


class TaskUpsert(BaseModel):
    task_id: Optional[str] = None
    status: str
    prompt: str
    trigger_user_id: str = ''
    target_endpoint_id: Optional[str] = None
    target_at_name: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    deployment_context: Optional[dict[str, Any]] = None
    result_summary: Optional[str] = None
    parent_task_id: Optional[str] = None
    tags: Optional[list[str]] = None
    plan_status: Optional[str] = None


@router.post('/projects')
def upsert_project(body: ProjectUpsert):
    state = get_team_state()
    existing = state.projects.get(body.project_id) if body.project_id else None
    project = TeamProjectMeta(
        project_id=body.project_id or new_id('proj_'),
        name=body.name,
        workspace_path=body.workspace_path,
        default_lead_at=body.default_lead_at,
        plan_gate=(
            body.plan_gate if body.plan_gate is not None
            else (existing.plan_gate if existing else False)),
        release_config=body.release_config
        or (existing.release_config if existing else {}),
        members=body.members
        or (existing.members if existing else []),
    )
    if existing:
        project.created_at = existing.created_at
    return state.projects.upsert(project).to_dict()


@router.get('/projects')
def list_projects():
    return {
        'projects': [p.to_dict() for p in get_team_state().projects.list()]
    }


@router.post('/projects/{project_id}/messages')
async def send_project_message(
    project_id: str,
    body: SendMessageRequest,
    wait: bool = Query(
        False,
        description='Wait for dispatch done/error and include replies',
    ),
    wait_timeout: float = Query(90.0, ge=1.0, le=300.0),
):
    state = get_team_state()
    await _ensure_handlers(state)

    msg = InboundMessage(
        message_id=new_id('msg_'),
        sender_user_id=body.sender_user_id,
        content=body.content,
        channel=body.channel,  # type: ignore[arg-type]
        project_id=project_id,
        thread_id=body.thread_id,
        chat_id=body.chat_id,
        mentions=body.mentions,
        operation_kind=body.operation_kind,  # type: ignore[arg-type]
        referenced_task_id=body.referenced_task_id,
        session_mode=body.session_mode,  # type: ignore[arg-type]
        target_at_name=body.target_at_name,
    )
    result = await state.ingress.handle(msg)
    if result.error:
        raise HTTPException(
            _status_from_error(result.error),
            detail=result.error,
        )
    payload: dict[str, Any] = {
        'project_id': result.project_id,
        'needs_card': result.needs_card,
        'candidates': result.candidates,
        'dispatches': [d.to_dict() for d in result.dispatches],
        'events': [e.to_dict() for e in result.events],
        'receipts': result.receipts,
    }
    if wait and result.dispatches:
        outcomes = await wait_for_dispatches(
            state,
            [d.dispatch_id for d in result.dispatches],
            timeout=wait_timeout,
        )
        replies = []
        for d in result.dispatches:
            out = outcomes.get(d.dispatch_id) or {}
            text = '\n'.join(out.get('texts') or []).strip()
            replies.append({
                'dispatch_id': d.dispatch_id,
                'at_name': d.target_at_name,
                'ok': bool(out.get('ok')),
                'content': text,
                'error': out.get('error'),
            })
            for ev in out.get('events') or []:
                payload['events'].append(ev.to_dict())
        payload['replies'] = replies
    return payload


def _status_from_error(err: dict) -> int:
    code = err.get('error', '')
    mapping = {
        'AGENT_OWNER_ONLY': 403,
        'ENDPOINT_OFFLINE': 503,
        'ENDPOINT_RECONNECTING': 503,
        'ENDPOINT_DEGRADED': 503,
        'ENDPOINT_NOT_FOUND': 404,
        'PROJECT_REQUIRED': 400,
        'NEEDS_PROJECT_CARD': 409,
        'NEEDS_DISAMBIGUATION': 409,
        'CIRCUIT_OPEN': 409,
        'SESSION_ATTACH_FAILED': 409,
        'BRIDGE_UNREACHABLE': 503,
    }
    return mapping.get(code, 400)


@router.get('/projects/{project_id}/timeline')
def get_timeline(project_id: str, thread_id: Optional[str] = None,
                 limit: int = 50):
    state = get_team_state()
    msgs = state.timeline.list(
        project_id, thread_id=thread_id, limit=limit)
    return {'messages': [m.to_dict() for m in msgs]}


@router.post('/projects/{project_id}/tasks')
def upsert_task(project_id: str, body: TaskUpsert):
    state = get_team_state()
    existing = state.tasks.get(body.task_id) if body.task_id else None
    task = TeamTask(
        task_id=body.task_id or new_id('task_'),
        project_id=project_id,
        status=body.status,  # type: ignore[arg-type]
        prompt=body.prompt or (existing.prompt if existing else ''),
        trigger_user_id=body.trigger_user_id
        or (existing.trigger_user_id if existing else ''),
        target_endpoint_id=(
            body.target_endpoint_id if body.target_endpoint_id is not None
            else (existing.target_endpoint_id if existing else None)),
        target_at_name=(
            body.target_at_name if body.target_at_name is not None
            else (existing.target_at_name if existing else None)),
        blocked_by=body.blocked_by if body.blocked_by is not None else (
            list(existing.blocked_by) if existing else []),
        output_artifacts=body.output_artifacts if body.output_artifacts
        else (list(existing.output_artifacts) if existing else []),
        deployment_context=(
            body.deployment_context if body.deployment_context is not None
            else (existing.deployment_context if existing else None)),
        result_summary=(
            body.result_summary if body.result_summary is not None
            else (existing.result_summary if existing else None)),
        last_dispatch_id=existing.last_dispatch_id if existing else None,
        thread_id=existing.thread_id if existing else None,
        parent_task_id=(
            body.parent_task_id if body.parent_task_id is not None
            else (existing.parent_task_id if existing else None)),
        claim_version=existing.claim_version if existing else 0,
        tags=(
            [str(x).strip() for x in body.tags if str(x).strip()]
            if body.tags is not None
            else (list(existing.tags) if existing else [])),
        plan_status=(
            body.plan_status if body.plan_status is not None
            else (existing.plan_status if existing else '')),
    )
    if existing is not None:
        task.created_at = existing.created_at
    from ms_agent.team.board import parent_completion_blocked
    from ms_agent.team.errors import PARENT_INCOMPLETE
    board = [
        t for t in state.tasks.list(project_id)
        if t.task_id != task.task_id
    ] + [task]
    blocked = parent_completion_blocked(board, task)
    if blocked:
        raise HTTPException(
            409,
            detail={'error': PARENT_INCOMPLETE, 'message': blocked},
        )
    return state.tasks.upsert(task).to_dict()


class ClaimBody(BaseModel):
    at_name: str
    expected_version: int = 0


class AskBody(BaseModel):
    question: str
    audience: str = 'lead'


class TaskPatch(BaseModel):
    prompt: Optional[str] = None
    target_at_name: Optional[str] = None
    blocked_by: Optional[list[str]] = None


@router.patch('/projects/{project_id}/tasks/{task_id}')
async def patch_task(project_id: str, task_id: str, body: TaskPatch):
    """Human board edit: brief, assignee, dependencies. Does not invent work."""
    state = get_team_state()
    await _ensure_handlers(state)
    task = state.tasks.get(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, detail={'error': 'TASK_NOT_FOUND'})
    if body.prompt is not None:
        task.prompt = body.prompt
    if body.blocked_by is not None:
        task.blocked_by = [
            str(x).strip() for x in body.blocked_by if str(x).strip()
        ]
    if body.target_at_name is not None:
        name = body.target_at_name.lstrip('@').strip()
        task.target_at_name = name or None
        if name:
            ep = state.endpoints.get_by_at_name(name)
            task.target_endpoint_id = ep.endpoint_id if ep else None
        else:
            task.target_endpoint_id = None
    from datetime import datetime, timezone
    task.updated_at = datetime.now(timezone.utc).isoformat()
    saved = state.tasks.upsert(task)
    if (
        saved.status == 'pending'
        and saved.target_at_name
        and getattr(state.coordinator, 'enabled', False)
    ):
        from ms_agent.team.coordinator import select_ready_tasks
        ready, _skipped = select_ready_tasks(state.tasks.list(project_id))
        if any(row.task_id == saved.task_id for row in ready):
            await state.coordinator.run(project_id)
    return saved.to_dict()


@router.post('/projects/{project_id}/tasks/{task_id}/claim')
def claim_task(project_id: str, task_id: str, body: ClaimBody):
    from ms_agent.team.errors import TeamError
    state = get_team_state()
    existing = state.tasks.get(task_id)
    if existing is None or existing.project_id != project_id:
        raise HTTPException(404, detail={'error': 'TASK_NOT_FOUND'})
    ep = state.endpoints.get_by_at_name(body.at_name.lstrip('@'))
    try:
        return state.tasks.claim(
            task_id,
            claimant_at_name=body.at_name,
            expected_version=body.expected_version,
            claimant_capabilities=list(
                (ep.capabilities if ep else [])
                or []),
        ).to_dict()
    except TeamError as exc:
        raise HTTPException(exc.http_status, detail=exc.to_dict()) from exc


@router.post('/projects/{project_id}/coordinator/run')
async def run_coordinator(project_id: str):
    state = get_team_state()
    await _ensure_handlers(state)
    return await state.coordinator.run(project_id, force=True)


@router.post('/projects/{project_id}/tasks/{task_id}/ask')
async def ask_task(project_id: str, task_id: str, body: AskBody):
    """Park an in-flight or waiting row as wait_human (explicit ticket)."""
    state = get_team_state()
    await _ensure_handlers(state)
    task = state.tasks.get(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, detail={'error': 'task_not_found'})
    if task.status in ('completed', 'failed', 'cancelled'):
        raise HTTPException(
            409,
            detail={'error': 'not_askable', 'status': task.status},
        )
    audience = (body.audience or 'lead').strip()
    if audience not in ('user', 'lead'):
        audience = 'lead'
    if not (body.question or '').strip():
        raise HTTPException(400, detail={'error': 'question_required'})
    parked = state.ingress.park_question(
        task, body.question.strip(), audience=audience)
    return {'ok': True, 'task': parked.to_dict()}


@router.post('/projects/{project_id}/tasks/{task_id}/ask-lead')
async def ask_lead(project_id: str, task_id: str):
    """Human button: escalate a wait_human row to Lead without dumping the stream."""
    state = get_team_state()
    await _ensure_handlers(state)
    task = state.tasks.get(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, detail={'error': 'task_not_found'})
    if task.status != 'wait_human':
        raise HTTPException(
            409,
            detail={'error': 'not_waiting', 'status': task.status},
        )
    state.ingress.escalate_to_lead(task)
    return {'ok': True, 'task': task.to_dict()}


@router.post('/projects/{project_id}/tasks/{task_id}/approve-plan')
async def approve_plan(project_id: str, task_id: str):
    """C-15: human approves the read-only plan so the worker may edit code."""
    from datetime import datetime, timezone

    state = get_team_state()
    await _ensure_handlers(state)
    task = state.tasks.get(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, detail={'error': 'task_not_found'})
    if (task.plan_status or '') not in ('ready', 'required'):
        raise HTTPException(
            409,
            detail={
                'error': 'plan_not_ready',
                'plan_status': task.plan_status,
            },
        )
    task.plan_status = 'approved'
    task.status = 'pending'
    ctx = dict(task.deployment_context or {})
    ctx.pop('needs_clarification', None)
    task.deployment_context = ctx
    task.updated_at = datetime.now(timezone.utc).isoformat()
    state.tasks.upsert(task)
    return {'ok': True, 'task': task.to_dict()}


@router.get('/projects/{project_id}/tasks')
def list_tasks(project_id: str):
    from ms_agent.team.board import is_unclaimed, is_unclaimed_expired
    from ms_agent.team.context import task_result_view

    state = get_team_state()
    timeout = getattr(state.coordinator, 'claim_timeout_s', 600.0)
    pending_asks = []
    if hasattr(state, 'permission_inbox'):
        pending_asks = state.permission_inbox.list_pending(
            project_id=project_id)
    asks_by_dispatch: dict[str, list[dict[str, Any]]] = {}
    for ask in pending_asks:
        did = str(ask.get('dispatch_id') or '')
        if did:
            asks_by_dispatch.setdefault(did, []).append(ask)
    rows = []
    for task in state.tasks.list(project_id):
        row = task.to_dict()
        events = []
        if task.last_dispatch_id:
            try:
                events = state.dispatch_log.list(task.last_dispatch_id)
            except Exception:  # noqa: BLE001
                events = []
        row.update(task_result_view(task, events))
        row['unclaimed'] = is_unclaimed(task)
        row['unclaimed_expired'] = is_unclaimed_expired(
            task, timeout_s=timeout)
        asks = asks_by_dispatch.get(str(task.last_dispatch_id or ''), [])
        inflight = task.status in ('in_progress', 'waiting_approval')
        row['waiting_approval'] = inflight and (
            bool(asks) or task.status == 'waiting_approval')
        row['pending_permission_asks'] = asks if inflight else []
        rows.append(row)
    return {'tasks': rows}


@router.get('/projects/{project_id}/local-file')
def read_project_local_file(project_id: str, path: str = Query(..., min_length=1)):
    """Open a workspace-local artifact path. Rejects traversal."""
    from pathlib import Path

    state = get_team_state()
    project = state.projects.get(project_id)
    if project is None:
        raise HTTPException(404, detail={'error': 'project_not_found'})
    root_raw = str(project.workspace_path or '').strip()
    if not root_raw:
        raise HTTPException(400, detail={'error': 'no_workspace'})
    root = Path(root_raw).expanduser().resolve()
    candidate = Path(path).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            403, detail={'error': 'path_outside_workspace'}) from exc
    if not target.is_file():
        raise HTTPException(404, detail={'error': 'file_not_found'})
    raw = target.read_bytes()
    limit = 200_000
    truncated = len(raw) > limit
    blob = raw[:limit]
    text: str | None = None
    binary = False
    try:
        text = blob.decode('utf-8')
    except UnicodeDecodeError:
        binary = True
        text = None
    return {
        'path': str(target),
        'name': target.name,
        'size': len(raw),
        'truncated': truncated,
        'binary': binary,
        'text': text,
    }


@router.get('/projects/{project_id}/roster')
def get_roster(project_id: str):
    del project_id
    state = get_team_state()
    roster = getattr(state, 'roster', None)
    if roster is None:
        return {'roster': []}
    return {'roster': roster.summarize()}


@router.post('/threads/bind')
def bind_thread(body: BindThreadRequest):
    state = get_team_state()
    binding = ThreadBinding(
        chat_id=body.chat_id,
        thread_id=body.thread_id,
        project_id=body.project_id,
    )
    return state.bindings.bind(binding).to_dict()


@router.post('/dispatches/{dispatch_id}/cancel')
async def cancel_dispatch(dispatch_id: str):
    """Cancel a queued or in-flight dispatch (idempotent)."""
    state = get_team_state()
    await _ensure_handlers(state)
    envelope = state.ingress.get_envelope(dispatch_id)
    where = await state.ingress.queue.cancel(dispatch_id)

    from ms_agent.team.events import TeamEvent

    if where == 'queued':
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.dispatch_cancelled',
                project_id=envelope.project_id if envelope else None,
                dispatch_id=dispatch_id,
                endpoint_id=envelope.target_endpoint_id if envelope else None,
                payload={'where': 'queued'},
            ))
        return {'ok': True, 'dispatch_id': dispatch_id, 'where': 'queued'}

    hub = get_bridge_hub()
    result = await hub.cancel(
        dispatch_id,
        endpoint_id=envelope.target_endpoint_id if envelope else None,
        runtime_session_id=(envelope.runtime_session_id
                            if envelope else None),
    )
    await state._fanout_event(  # noqa: SLF001
        TeamEvent(
            type='team.dispatch_cancelled',
            project_id=envelope.project_id if envelope else None,
            dispatch_id=dispatch_id,
            endpoint_id=envelope.target_endpoint_id if envelope else None,
            payload={
                'where': where,
                'bridge': result,
            },
        ))
    return {
        'ok': True,
        'dispatch_id': dispatch_id,
        'where': where,
        'bridge': result,
    }


def _dispatch_detail(state, dispatch_id: str) -> dict[str, Any] | None:
    """Envelope + log-derived status for C-05 private stream."""
    envelope = state.ingress.get_envelope(dispatch_id)
    events = []
    try:
        events = state.dispatch_log.list(dispatch_id)
    except Exception:  # noqa: BLE001
        events = []
    if envelope is None and not events:
        return None
    status = 'running'
    ok = None
    error_code = None
    at_name = envelope.target_at_name if envelope else None
    project_id = envelope.project_id if envelope else None
    endpoint_id = envelope.target_endpoint_id if envelope else None
    runtime_session_id = envelope.runtime_session_id if envelope else None
    session_mode = envelope.session_mode if envelope else None
    session_resolution = envelope.session_resolution if envelope else None
    prompt = envelope.prompt if envelope else None
    referenced_task_id = envelope.referenced_task_id if envelope else None
    for ev in events:
        et = getattr(ev, 'type', '')
        payload = getattr(ev, 'payload', None) or {}
        if not at_name:
            at_name = getattr(ev, 'at_name', None)
        if not project_id:
            project_id = getattr(ev, 'project_id', None)
        if not endpoint_id:
            endpoint_id = getattr(ev, 'endpoint_id', None)
        if et == 'team.session' or et == 'team.dispatch_start':
            runtime_session_id = (
                payload.get('runtime_session_id') or runtime_session_id)
            session_mode = payload.get('session_mode') or session_mode
            session_resolution = (
                payload.get('session_resolution') or session_resolution)
            prompt = payload.get('prompt') or prompt
        elif et == 'team.dispatch_done':
            status = 'done'
            ok = bool(payload.get('ok', True))
        elif et == 'team.dispatch_error':
            status = 'error'
            ok = False
            error_code = payload.get('code') or payload.get('error')
        elif et == 'team.dispatch_cancelled':
            status = 'cancelled'
            ok = False
        elif et == 'team.dispatch_waiting_approval' or et == 'team.permission_requested':
            if status == 'running':
                status = 'waiting_approval'
        elif et == 'team.permission_resolved':
            if status == 'waiting_approval':
                status = 'running'
    from ms_agent.team.context import final_text_from_dispatch_events
    final_text = final_text_from_dispatch_events(events)
    waiting = False
    if referenced_task_id:
        task = state.tasks.get(referenced_task_id)
        waiting = bool(
            task is not None and (
                task.status == 'wait_human' or (
                    task.deployment_context or {}
                ).get('needs_clarification')))
    pending_asks = []
    if hasattr(state, 'permission_inbox'):
        pending_asks = state.permission_inbox.list_pending(
            dispatch_id=dispatch_id)
    if status == 'running' and pending_asks:
        status = 'waiting_approval'
    return {
        'dispatch_id': dispatch_id,
        'project_id': project_id,
        'at_name': at_name,
        'endpoint_id': endpoint_id,
        'runtime_session_id': runtime_session_id,
        'session_mode': session_mode,
        'session_resolution': session_resolution,
        'referenced_task_id': referenced_task_id,
        'status': status,
        'ok': ok,
        'error_code': error_code,
        'prompt': prompt,
        'event_count': len(events),
        'final_text': final_text,
        'needs_clarification': waiting,
        'waiting_approval': status == 'waiting_approval',
        'pending_permission_asks': pending_asks,
    }


@router.get('/dispatches/{dispatch_id}')
def get_dispatch(dispatch_id: str):
    detail = _dispatch_detail(get_team_state(), dispatch_id)
    if detail is None:
        raise HTTPException(404, detail={'error': 'dispatch_not_found'})
    return detail


class PermissionResolveBody(BaseModel):
    action: str = 'allow_once'
    option_id: Optional[str] = None
    pattern: Optional[str] = None
    call_id: Optional[str] = None
    dispatch_id: Optional[str] = None


@router.get('/projects/{project_id}/permission_asks')
def list_project_permission_asks(project_id: str):
    """Central inbox: pending Worker permission asks for a project."""
    state = get_team_state()
    return {
        'project_id': project_id,
        'asks': state.permission_inbox.list_pending(project_id=project_id),
    }


@router.post('/permission_asks/{request_id}/resolve')
async def resolve_permission_ask(request_id: str, body: PermissionResolveBody):
    """Human decision for a live ask, keyed by request_id (or call_id)."""
    state = get_team_state()
    await _ensure_handlers(state)
    row = state.permission_inbox.get(request_id)
    if row is None and body.dispatch_id:
        row = state.permission_inbox.get(f'{body.dispatch_id}:{request_id}')
    if row is None and body.call_id:
        matches = [
            item for item in state.permission_inbox.list_pending(
                dispatch_id=body.dispatch_id)
            if str(item.get('call_id') or '') == body.call_id
        ]
        if len(matches) > 1:
            raise HTTPException(
                409, detail={'error': 'permission_ask_ambiguous'})
        if matches:
            row = matches[0]
    if row is None:
        raise HTTPException(404, detail={'error': 'permission_ask_not_found'})

    action = body.action or 'allow_once'
    rid = str(row.get('request_id') or request_id)
    call_id = str(body.call_id or row.get('call_id') or rid)
    source = row.get('source') or 'cloud'

    from ms_agent.team.events import TeamEvent

    ok = False
    if source == 'cloud':
        cloud = getattr(state, 'cloud_runtime', None)
        if cloud is None:
            raise HTTPException(409, detail={'error': 'cloud_runtime_missing'})
        ok = bool(cloud.resolve_permission(
            row.get('dispatch_id') or '',
            rid,
            action=action,
            pattern=body.pattern,
        ))
    else:
        hub = get_bridge_hub()
        result = await hub.resolve_permission(
            dispatch_id=row.get('dispatch_id') or '',
            endpoint_id=row.get('endpoint_id'),
            call_id=call_id,
            action=action,
            option_id=body.option_id,
            request_id=rid,
            acp_id=row.get('acp_id'),
        )
        ok = bool(result.get('ok'))

    if ok:
        state.permission_inbox.decide(
            rid, action, pattern=str(body.pattern or ''))
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_resolved',
                project_id=row.get('project_id'),
                dispatch_id=row.get('dispatch_id'),
                endpoint_id=row.get('endpoint_id'),
                at_name=row.get('at_name'),
                payload={
                    'request_id': rid,
                    'call_id': call_id,
                    'action': action,
                    'pattern': body.pattern,
                    'ok': True,
                },
            ))
    return {
        'ok': ok,
        'request_id': rid,
        'call_id': call_id,
        'dispatch_id': row.get('dispatch_id'),
        'action': action,
    }


@router.get('/dispatches/{dispatch_id}/events')
def list_dispatch_events(
    dispatch_id: str,
    limit: int = Query(2000, ge=1, le=5000),
):
    """JSON snapshot of the private stream so the UI can replay after done."""
    state = get_team_state()
    if _dispatch_detail(state, dispatch_id) is None:
        raise HTTPException(404, detail={'error': 'dispatch_not_found'})
    events = state.dispatch_log.list(dispatch_id)
    return {
        'dispatch_id': dispatch_id,
        'events': [ev.to_dict() for ev in events[-limit:]],
    }


@router.get('/dispatches/{dispatch_id}/stream')
async def dispatch_stream(
    dispatch_id: str,
    request: Request,
    replay: int = Query(500, ge=0, le=5000),
):
    """SSE private stream for one dispatch: replay stored events, then live."""
    state = get_team_state()
    state.ensure_health_loop()
    if _dispatch_detail(state, dispatch_id) is None:
        raise HTTPException(404, detail={'error': 'dispatch_not_found'})

    from team.api_events import _sse

    queue: asyncio.Queue = asyncio.Queue(maxsize=512)

    def _match(event) -> bool:
        return getattr(event, 'dispatch_id', None) == dispatch_id

    async def _subscriber(event):
        if not _match(event):
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    state.event_subscribers.append(_subscriber)

    async def event_generator():
        try:
            stored = state.dispatch_log.list(dispatch_id)
            seen: set[int] = set()
            chunk = stored[-replay:] if replay > 0 else []
            for ev in chunk:
                seen.add(id(ev))
                yield _sse(ev)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if id(ev) in seen:
                        continue
                    seen.add(id(ev))
                    yield _sse(ev)
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            if _subscriber in state.event_subscribers:
                state.event_subscribers.remove(_subscriber)

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


async def _ensure_handlers(state) -> None:
    """Wire cloud + bridge dispatch handlers once."""
    if state.ingress._default_handler is not None:  # noqa: SLF001
        return

    import os

    from ms_agent.team.events import TeamEvent

    hub = get_bridge_hub()
    # Real LLMAgent by default. Opt into echo with MS_AGENT_TEAM_CLOUD_DRY_RUN=1.
    dry = os.environ.get('MS_AGENT_TEAM_CLOUD_DRY_RUN', '0').lower() in (
        '1', 'true', 'yes', 'on')
    cloud = CloudAgentRuntime(
        endpoint_store=state.endpoints,
        artifact_store=state.artifacts,
        task_store=state.tasks,
        dispatch_log=state.dispatch_log,
        endpoint_token_store=state.endpoint_tokens,
        project_store=state.projects,
        coordinator=state.coordinator,
        event_sink=state._fanout_event,  # noqa: SLF001 — SSE / Event 面板
        dry_run=dry,
        permission_store=state.permission_inbox.store,
    )
    state.cloud_runtime = cloud

    async def cloud_handler(envelope):
        from ms_agent.team.context import final_text_from_dispatch_events
        result = await cloud.run(envelope)
        ok = bool(result.get('ok', True))
        events = []
        try:
            events = state.dispatch_log.list(envelope.dispatch_id)
        except Exception:  # noqa: BLE001
            events = []
        summary = (
            final_text_from_dispatch_events(events)
            or result.get('summary')
            or result.get('error')
            or '')
        error_code = None if ok else (
            result.get('code') or result.get('error') or 'internal')
        if error_code and len(str(error_code)) > 80:
            error_code = 'internal'
        state.ingress.record_dispatch_outcome(
            envelope, ok=ok, summary=summary, error_code=error_code)
        return result

    async def bridge_handler(envelope):
        from ms_agent.team.context import final_text_from_dispatch_events
        result = await hub.dispatch(envelope)
        ok = bool(result.get('ok'))
        events = []
        try:
            events = state.dispatch_log.list(envelope.dispatch_id)
        except Exception:  # noqa: BLE001
            events = []
        summary = (
            final_text_from_dispatch_events(events)
            or result.get('summary')
            or result.get('error')
            or '')
        if not ok:
            code = result.get('code') or result.get('error') or 'internal'
            if len(str(code)) > 80:
                code = 'internal'
            await state._fanout_event(  # noqa: SLF001
                TeamEvent(
                    type='team.dispatch_error',
                    project_id=envelope.project_id,
                    dispatch_id=envelope.dispatch_id,
                    endpoint_id=envelope.target_endpoint_id,
                    at_name=envelope.target_at_name,
                    payload={
                        'code': code,
                    },
                ))
        else:
            code = None
        state.ingress.record_dispatch_outcome(
            envelope, ok=ok, summary=summary, error_code=code)
        return result

    state.ingress.set_dispatch_handler(cloud_handler, adapter_kind='cloud')
    state.ingress.set_dispatch_handler(bridge_handler, adapter_kind='acp')
    state.ingress.set_dispatch_handler(bridge_handler, adapter_kind='hermes')
    state.ingress.set_dispatch_handler(bridge_handler, adapter_kind='openclaw')
    state.ingress.set_dispatch_handler(cloud_handler)  # default


async def ensure_handlers(state=None) -> None:
    """Public alias for chat / other callers in the same process."""
    await _ensure_handlers(state or get_team_state())
