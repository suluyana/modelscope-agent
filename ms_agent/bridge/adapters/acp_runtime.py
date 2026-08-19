# Copyright (c) ModelScope Contributors. All rights reserved.
"""Shared ACP execute helpers (session/load|new + prompt streaming)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    auto_allow: bool = True,
    prompt_timeout: float = 300.0,
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
            created = await acp.request(
                'session/new',
                {'cwd': workdir, 'mcpServers': []},
            )
            runtime_sid = (created or {}).get('sessionId') or session_id
        elif want_attach:
            try:
                loaded = await acp.request(
                    'session/load',
                    {
                        'sessionId': session_id,
                        'cwd': workdir,
                        'mcpServers': [],
                    },
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
                created = await acp.request(
                    'session/new',
                    {'cwd': workdir, 'mcpServers': []},
                )
                runtime_sid = (created or {}).get('sessionId') or session_id
        else:
            created = await acp.request(
                'session/new',
                {'cwd': workdir, 'mcpServers': []},
            )
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
        for ev in _acp_update_to_events(msg, runtime_sid):
            if ev.type == 'text' and ev.content:
                text_parts.append(ev.content)
            _put(ev)

    acp.on_notification = on_update

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
                timeout=prompt_timeout,
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
