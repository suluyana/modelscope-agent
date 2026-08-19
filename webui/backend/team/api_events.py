# Copyright (c) ModelScope Contributors. All rights reserved.
"""SSE event stream for Agent Team orchestration UI."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from team.state import get_team_state

router = APIRouter(tags=['team-events'])


@router.get('/projects/{project_id}/events')
async def project_events(
    project_id: str,
    request: Request,
    endpoint_id: Optional[str] = None,
    replay: int = 20,
):
    """Server-Sent Events stream of TeamEvent for a project.

    Independent of UI focus — subscribers receive fanout from EventBus.
    """
    state = get_team_state()
    state.ensure_health_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    def _match(event) -> bool:
        if endpoint_id and getattr(event, 'endpoint_id', None) != endpoint_id:
            return False
        pid = getattr(event, 'project_id', None)
        # endpoint.status may have no project_id — still useful for orchestration.
        if event.type == 'endpoint.status':
            return True
        return pid in (None, project_id)

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
            for ev in state.recent_events(project_id=project_id, limit=replay):
                if _match(ev):
                    yield _sse(ev)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
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


def _sse(event) -> str:
    data = json.dumps(event.to_dict(), ensure_ascii=False)
    eid = getattr(event, 'dispatch_id', None) or getattr(
        event, 'created_at', '')
    return f'id: {eid}\nevent: {event.type}\ndata: {data}\n\n'
