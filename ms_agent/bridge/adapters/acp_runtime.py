# Copyright (c) ModelScope Contributors. All rights reserved.
"""Shared ACP execute helpers (session/load|new + prompt streaming)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, AsyncIterator

from ms_agent.bridge.adapters.acp_client import (
    AuthRequired,
    attach_fallback_allowed,
    get_acp_pool,
)
from ms_agent.bridge.adapters.base import BridgeEvent

logger = logging.getLogger(__name__)

# Platform SessionDirectory mints ids like sess_*; ACP runtimes use their own
# (Codex: UUID-like). Never session/load a platform id into codex-acp.
_PLATFORM_SESSION_PREFIXES = ('sess_', 'd_', 'sb_', 'msg_', 'ep_', 'proj_')
_ACP_SESSION_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.I,
)


def looks_like_acp_session_id(session_id: str | None) -> bool:
    """True when ``session_id`` is a runtime ACP session, not a Team binding id."""
    if not session_id or not isinstance(session_id, str):
        return False
    sid = session_id.strip()
    if not sid:
        return False
    if sid.startswith(_PLATFORM_SESSION_PREFIXES):
        return False
    if _ACP_SESSION_RE.match(sid):
        return True
    # Codex sometimes uses non-RFC UUIDs still hyphenated and long enough.
    if len(sid) >= 16 and '-' in sid and not sid.startswith('sess'):
        return True
    return False


def team_ask_mcp_servers(
    *,
    api_base: str | None,
    project_id: str | None,
    task_id: str | None,
) -> list[dict[str, Any]]:
    """Stdio MCP so ACP workers can file ``task_ask`` on the Team API."""
    if not api_base or not project_id or not task_id:
        return []
    try:
        import ms_agent as _ms_agent
        root = str(Path(_ms_agent.__file__).resolve().parents[1])
    except Exception:  # noqa: BLE001
        root = os.getcwd()
    pythonpath = os.pathsep.join(
        p for p in (root, os.environ.get('PYTHONPATH', '')) if p)
    return [{
        'name': 'team-ask',
        'command': sys.executable,
        'args': ['-m', 'ms_agent.team.mcp_ask'],
        'env': {
            'MS_AGENT_TEAM_API': str(api_base).rstrip('/'),
            'MS_AGENT_TEAM_PROJECT_ID': project_id,
            'MS_AGENT_TEAM_TASK_ID': task_id,
            'PYTHONPATH': pythonpath,
            'PYTHONUNBUFFERED': '1',
        },
    }]


def _session_mcp_params(
    workdir: str,
    mcp_servers: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {'cwd': workdir, 'mcpServers': list(mcp_servers or [])}


def _chunk_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get('text') or '')
    if isinstance(content, str):
        return content
    return str(content) if content else ''


def _acp_update_to_events(
    msg: dict[str, Any],
    runtime_sid: str,
) -> list[BridgeEvent]:
    """Map ACP session/update notifications into BridgeEvents."""
    update = (msg.get('params') or {}).get('update') or {}
    if not isinstance(update, dict):
        return []
    kind = update.get('sessionUpdate') or ''
    extra = {'runtime_session_id': runtime_sid}
    if kind == 'agent_message_chunk':
        chunk = _chunk_text(update.get('content'))
        if not chunk:
            return []
        return [BridgeEvent(type='text', content=chunk, payload=extra)]
    if kind == 'agent_thought_chunk':
        chunk = _chunk_text(update.get('content'))
        if not chunk:
            return []
        return [
            BridgeEvent(
                type='status',
                content=chunk,
                payload={**extra, 'kind': 'thought'},
            )
        ]
    if kind in ('tool_call', 'tool_call_update'):
        call_id = (
            update.get('toolCallId') or update.get('tool_call_id') or '')
        title = update.get('title') or update.get('kind') or 'tool'
        status = str(update.get('status') or 'in_progress')
        raw_input = update.get('rawInput') or update.get('raw_input')
        done = status in ('completed', 'failed', 'cancelled')
        ev_type = 'tool_result' if (kind == 'tool_call_update' and done
                                    ) or status in ('completed', 'failed') else 'tool_call'
        content_bits = update.get('content')
        result = ''
        if isinstance(content_bits, list):
            parts = []
            for block in content_bits:
                if isinstance(block, dict):
                    parts.append(block.get('text') or json.dumps(
                        block, ensure_ascii=False)[:500])
                else:
                    parts.append(str(block))
            result = '\n'.join(p for p in parts if p)
        elif isinstance(content_bits, str):
            result = content_bits
        return [
            BridgeEvent(
                type=ev_type,
                content=result or str(title),
                payload={
                    **extra,
                    'call_id': call_id,
                    'name': title,
                    'kind': update.get('kind') or '',
                    'arguments': raw_input,
                    'status': 'error' if status == 'failed' else (
                        'done' if done else 'running'),
                    'locations': update.get('locations') or [],
                },
            )
        ]
    return []


def permission_msg_to_event(
    msg: dict[str, Any],
    runtime_sid: str,
) -> BridgeEvent | None:
    """Map ACP ``session/request_permission`` into a host-visible event."""
    if msg.get('method') != 'session/request_permission':
        return None
    params = msg.get('params') or {}
    if not isinstance(params, dict):
        params = {}
    tool_call = params.get('toolCall') or params.get('tool_call') or {}
    if not isinstance(tool_call, dict):
        tool_call = {}
    call_id = str(
        tool_call.get('toolCallId')
        or tool_call.get('tool_call_id')
        or tool_call.get('id')
        or '')
    title = (
        tool_call.get('title')
        or tool_call.get('kind')
        or params.get('title')
        or 'permission')
    options = params.get('options') or []
    return BridgeEvent(
        type='permission_request',
        content=str(title),
        payload={
            'runtime_session_id': runtime_sid,
            'acp_id': msg.get('id'),
            'call_id': call_id,
            'tool_name': str(title),
            'tool_args': tool_call.get('rawInput') or tool_call.get('raw_input')
            or {},
            'options': options,
        },
    )


async def run_acp_turn(
    *,
    runtime: str,
    command: list[str],
    prompt: str,
    session_id: str,
    cwd: str | None,
    session_mode: str,
    auth_method_id: str | None = None,
    env: dict[str, str] | None = None,
    auto_allow: bool = False,
    prompt_timeout: float | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> AsyncIterator[BridgeEvent]:
    """Reuse pooled ACP process; attach via session/load or create fresh."""
    pool = get_acp_pool()
    acp = await pool.get(
        runtime,
        command,
        cwd=cwd,
        env=env,
        auto_allow=auto_allow,
        auth_method_id=auth_method_id,
    )
    yield BridgeEvent(
        type='status',
        content=f'{runtime}_acp_connected',
        payload={'session_mode': session_mode, 'pooled': True},
    )
    try:
        await acp.ensure_ready()
    except AuthRequired as exc:
        yield BridgeEvent(
            type='error',
            content=str(exc),
            payload={'code': 'need_reauth', 'runtime': runtime},
        )
        yield BridgeEvent(type='done', content='need_reauth')
        return

    workdir = cwd or os.getcwd()
    runtime_sid = session_id
    session_params = _session_mcp_params(workdir, mcp_servers)
    try:
        want_attach = (
            session_mode == 'attach' and looks_like_acp_session_id(session_id))
        if session_mode == 'attach' and session_id and not want_attach:
            # Control-plane minted sess_* — open a real ACP session instead of
            # failing session/load with Internal error.
            yield BridgeEvent(
                type='status',
                content='attach_skipped_platform_session_id',
                payload={
                    'platform_session_id': session_id,
                    'note':
                    'Team sess_* is not an ACP sessionId; using session/new',
                },
            )
            created = await acp.request('session/new', session_params)
            runtime_sid = (created or {}).get('sessionId') or session_id
        elif want_attach:
            try:
                loaded = await acp.request(
                    'session/load',
                    {'sessionId': session_id, **session_params},
                )
                runtime_sid = (loaded or {}).get('sessionId') or session_id
                yield BridgeEvent(
                    type='status',
                    content='attached',
                    payload={'runtime_session_id': runtime_sid},
                )
            except Exception as exc:  # noqa: BLE001
                if not attach_fallback_allowed():
                    yield BridgeEvent(
                        type='error',
                        content=f'session_attach_failed: {exc}',
                        payload={
                            'code': 'session_attach_failed',
                            'runtime': runtime,
                        },
                    )
                    yield BridgeEvent(type='done', content='error')
                    return
                yield BridgeEvent(
                    type='status',
                    content='attach_fallback_fresh',
                    payload={'error': str(exc)},
                )
                created = await acp.request('session/new', session_params)
                runtime_sid = (created or {}).get('sessionId') or session_id
        else:
            created = await acp.request('session/new', session_params)
            runtime_sid = (created or {}).get('sessionId') or session_id
    except AuthRequired as exc:
        yield BridgeEvent(
            type='error',
            content=str(exc),
            payload={'code': 'need_reauth', 'runtime': runtime},
        )
        yield BridgeEvent(type='done', content='need_reauth')
        return

    text_parts: list[str] = []
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    def _put(ev: BridgeEvent) -> None:
        try:
            queue.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    async def on_update(msg: dict[str, Any]) -> None:
        perm = permission_msg_to_event(msg, runtime_sid)
        if perm is not None:
            _put(perm)
            return
        for ev in _acp_update_to_events(msg, runtime_sid):
            if ev.type == 'text' and ev.content:
                text_parts.append(ev.content)
            _put(ev)

    acp.on_notification = on_update

    turn_timeout = prompt_timeout
    if turn_timeout is None and auto_allow:
        turn_timeout = 300.0

    async def _prompt() -> None:
        try:
            result = await acp.request(
                'session/prompt',
                {
                    'sessionId': runtime_sid,
                    'prompt': [{
                        'type': 'text',
                        'text': prompt
                    }],
                },
                timeout=turn_timeout,
            )
            if not text_parts and result is not None:
                _put(
                    BridgeEvent(
                        type='text',
                        content=json.dumps(result, ensure_ascii=False),
                        payload={'runtime_session_id': runtime_sid},
                    ))
            _put(
                BridgeEvent(
                    type='done',
                    content='ok',
                    payload={
                        'runtime_session_id': runtime_sid,
                        'stopReason': (result or {}).get('stopReason'),
                    },
                ))
        except AuthRequired as exc:
            _put(
                BridgeEvent(
                    type='error',
                    content=str(exc),
                    payload={'code': 'need_reauth', 'runtime': runtime},
                ))
            _put(BridgeEvent(type='done', content='need_reauth'))
        except Exception as exc:  # noqa: BLE001
            logger.exception('%s ACP prompt failed', runtime)
            _put(BridgeEvent(type='error', content=str(exc)))
            _put(BridgeEvent(type='done', content='error'))
        finally:
            queue.put_nowait(sentinel)

    task = asyncio.create_task(_prompt())
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            yield item
    finally:
        acp.on_notification = None
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def list_acp_sessions(
    *,
    runtime: str,
    command: list[str],
    cwd: str | None = None,
    auth_method_id: str | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Best-effort session/list via pooled ACP agent."""
    pool = get_acp_pool()
    try:
        acp = await pool.get(
            runtime,
            command,
            cwd=cwd,
            env=env,
            auth_method_id=auth_method_id,
        )
        await acp.ensure_ready()
        result = await acp.request('session/list', {})
        sessions = (result or {}).get('sessions') or []
        return list(sessions) if isinstance(sessions, list) else []
    except AuthRequired:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info('%s session/list failed: %s', runtime, exc)
        return []


async def cancel_acp_session(
    *,
    runtime: str,
    session_id: str,
    cwd: str | None = None,
) -> None:
    pool = get_acp_pool()
    key_cwd = cwd
    # Best-effort: cancel on any pooled session for this runtime.
    for (rt, path), sess in list(pool._sessions.items()):  # noqa: SLF001
        if rt != runtime:
            continue
        if key_cwd is not None and path and path != os.path.abspath(key_cwd):
            continue
        if sess.alive:
            await sess.session_cancel(session_id)
