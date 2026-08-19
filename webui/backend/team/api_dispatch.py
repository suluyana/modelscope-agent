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


@router.post('/projects')
def upsert_project(body: ProjectUpsert):
    state = get_team_state()
    project = TeamProjectMeta(
        project_id=body.project_id or new_id('proj_'),
        name=body.name,
        workspace_path=body.workspace_path,
        default_lead_at=body.default_lead_at,
        release_config=body.release_config,
        members=body.members,
    )
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
    task = TeamTask(
        task_id=body.task_id or new_id('task_'),
        project_id=project_id,
        status=body.status,  # type: ignore[arg-type]
        prompt=body.prompt,
        trigger_user_id=body.trigger_user_id,
        target_endpoint_id=body.target_endpoint_id,
        target_at_name=body.target_at_name,
        blocked_by=body.blocked_by,
        output_artifacts=body.output_artifacts,
        deployment_context=body.deployment_context,
        result_summary=body.result_summary,
    )
    return state.tasks.upsert(task).to_dict()


@router.get('/projects/{project_id}/tasks')
def list_tasks(project_id: str):
    return {
        'tasks':
        [t.to_dict() for t in get_team_state().tasks.list(project_id)]
    }


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
    return {
        'dispatch_id': dispatch_id,
        'project_id': project_id,
        'at_name': at_name,
        'endpoint_id': endpoint_id,
        'runtime_session_id': runtime_session_id,
        'session_mode': session_mode,
        'session_resolution': session_resolution,
        'status': status,
        'ok': ok,
        'error_code': error_code,
        'prompt': prompt,
        'event_count': len(events),
    }


@router.get('/dispatches/{dispatch_id}')
def get_dispatch(dispatch_id: str):
    detail = _dispatch_detail(get_team_state(), dispatch_id)
    if detail is None:
        raise HTTPException(404, detail={'error': 'dispatch_not_found'})
    return detail


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
            chunk = stored[-replay:] if replay else stored
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
        endpoint_token_store=state.endpoint_tokens,
        project_store=state.projects,
        event_sink=state._fanout_event,  # noqa: SLF001 — SSE / Event 面板
        dry_run=dry,
    )

    async def cloud_handler(envelope):
        result = await cloud.run(envelope)
        ok = bool(result.get('ok', True))
        summary = result.get('summary') or result.get('error') or ''
        error_code = None if ok else (
            result.get('code') or result.get('error') or 'internal')
        if error_code and len(str(error_code)) > 80:
            error_code = 'internal'
        state.ingress.record_dispatch_outcome(
            envelope, ok=ok, summary=summary, error_code=error_code)
        return result

    async def bridge_handler(envelope):
        result = await hub.dispatch(envelope)
        ok = bool(result.get('ok'))
        summary = result.get('summary') or result.get('error') or ''
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
