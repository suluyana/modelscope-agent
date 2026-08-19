# Copyright (c) ModelScope Contributors. All rights reserved.
"""Bridge WebSocket hub: bridge_id → live connection (multi-Agent demux)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ms_agent.team.events import TeamEvent
from ms_agent.team.models import DispatchEnvelope, RuntimeCandidate, new_id
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from team.state import get_team_state

logger = logging.getLogger(__name__)
router = APIRouter()


class BridgeHub:
    """Transport hub keyed by MachineBridge.bridge_id."""

    def __init__(self) -> None:
        self._conns: Dict[str, WebSocket] = {}
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def register(self, bridge_id: str, ws: WebSocket) -> None:
        async with self._lock:
            prev = self._conns.get(bridge_id)
            self._conns[bridge_id] = ws
        if prev is not None and prev is not ws:
            try:
                await prev.close(
                    code=4000, reason='replaced by new bridge connection')
            except Exception:  # noqa: BLE001
                logger.debug(
                    'failed closing previous WS for %s',
                    bridge_id,
                    exc_info=True)

    async def unregister(self, bridge_id: str, ws: WebSocket | None = None) -> None:
        async with self._lock:
            current = self._conns.get(bridge_id)
            # Avoid a replaced connection's finally wiping the newer socket.
            if ws is not None and current is not None and current is not ws:
                return
            self._conns.pop(bridge_id, None)

    async def force_disconnect(
        self,
        bridge_id: str,
        *,
        reason: str = 'retired',
    ) -> None:
        async with self._lock:
            ws = self._conns.pop(bridge_id, None)
        if ws is None:
            return
        try:
            await ws.close(code=4000, reason=(reason or 'retired')[:123])
        except Exception:  # noqa: BLE001
            logger.debug(
                'force_disconnect close failed for %s',
                bridge_id,
                exc_info=True)

    def get(self, bridge_id: str) -> Optional[WebSocket]:
        return self._conns.get(bridge_id)

    def _resolve_bridge_id(self, endpoint_id: str | None) -> str | None:
        if not endpoint_id:
            return None
        state = get_team_state()
        ep = state.endpoints.get(endpoint_id)
        if ep is None:
            return None
        return ep.bridge_id

    async def dispatch(self,
                       envelope: DispatchEnvelope,
                       timeout: float = 600.0) -> dict[str, Any]:
        bridge_id = self._resolve_bridge_id(envelope.target_endpoint_id)
        ws = self._conns.get(bridge_id) if bridge_id else None
        if ws is None:
            return {
                'ok': False,
                'error': 'BRIDGE_UNREACHABLE',
                'code': 'bridge_unreachable',
                'dispatch_id': envelope.dispatch_id,
            }
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[envelope.dispatch_id] = fut
        await ws.send_json({
            'type': 'dispatch',
            'envelope': envelope.to_dict(),
        })
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(envelope.dispatch_id, None)
            return {
                'ok': False,
                'error': 'TIMEOUT',
                'code': 'internal',
                'dispatch_id': envelope.dispatch_id,
            }

    async def cancel(
        self,
        dispatch_id: str,
        *,
        endpoint_id: str | None = None,
        runtime_session_id: str | None = None,
    ) -> dict[str, Any]:
        bridge_id = self._resolve_bridge_id(endpoint_id)
        ws = self._conns.get(bridge_id) if bridge_id else None
        if ws is None and endpoint_id is None:
            for conn in self._conns.values():
                ws = conn
                break
        if ws is None:
            return {'ok': False, 'error': 'BRIDGE_UNREACHABLE'}
        await ws.send_json({
            'type': 'cancel',
            'dispatch_id': dispatch_id,
            'runtime_session_id': runtime_session_id,
        })
        self.complete_dispatch(dispatch_id, {
            'ok': False,
            'code': 'cancelled',
            'summary': 'cancelled',
            'dispatch_id': dispatch_id,
        })
        return {'ok': True, 'dispatch_id': dispatch_id}

    async def notify_agents_updated(self, bridge_id: str) -> bool:
        """Push current Agent rows so the daemon can sync slots immediately."""
        ws = self._conns.get(bridge_id)
        if ws is None:
            return False
        state = get_team_state()
        agents = [
            e.to_dict() for e in state.endpoints.list_by_bridge(bridge_id)
        ]
        try:
            await ws.send_json({
                'type': 'agents_updated',
                'bridge_id': bridge_id,
                'agents': agents,
            })
            return True
        except Exception:  # noqa: BLE001
            logger.debug(
                'notify_agents_updated failed for %s', bridge_id, exc_info=True)
            return False

    def complete_dispatch(self, dispatch_id: str, result: dict) -> None:
        fut = self._pending.pop(dispatch_id, None)
        if fut and not fut.done():
            fut.set_result(result)


def get_bridge_hub() -> BridgeHub:
    state = get_team_state()
    if state.bridge_hub is None:
        state.bridge_hub = BridgeHub()
    return state.bridge_hub


def _extract_bearer(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get('authorization') or websocket.headers.get(
        'Authorization')
    if auth and auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    return websocket.query_params.get('token')


def _heartbeat_bridge(
    state,
    bridge_id: str,
    *,
    instance_id: str | None,
    status: str,
    agents: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> None:
    bridge = state.bridges.get(bridge_id)
    if bridge is None:
        return
    bridge.status = status  # type: ignore[assignment]
    bridge.last_heartbeat = _now_iso()
    if instance_id:
        bridge.current_instance_id = instance_id
    bridge.updated_at = _now_iso()
    state.bridges.upsert(bridge)

    # Propagate bridge offline to local agents; otherwise apply per-agent rows.
    if status in ('offline', 'degraded', 'need_reauth') and not agents:
        for ep in state.endpoints.list_by_bridge(bridge_id):
            ep.status = status  # type: ignore[assignment]
            # Do NOT refresh last_heartbeat — that stamp is only for real
            # bridge heartbeats. updated_at marks when we observed offline.
            ep.updated_at = _now_iso()
            state.endpoints.upsert(ep)
    elif agents:
        by_id = {e.endpoint_id: e for e in state.endpoints.list_by_bridge(bridge_id)}
        for row in agents:
            eid = row.get('endpoint_id')
            ep = by_id.get(eid) if eid else None
            if ep is None:
                continue
            ep.status = row.get('status', 'online')  # type: ignore[assignment]
            ep.last_heartbeat = bridge.last_heartbeat
            if row.get('instance_id'):
                ep.current_instance_id = row['instance_id']
            ep.updated_at = _now_iso()
            state.endpoints.upsert(ep)
    else:
        for ep in state.endpoints.list_by_bridge(bridge_id):
            if ep.status == 'offline':
                ep.status = 'online'
            ep.last_heartbeat = bridge.last_heartbeat
            ep.updated_at = _now_iso()
            state.endpoints.upsert(ep)

    if candidates is not None:
        parsed = []
        for row in candidates:
            cid = row.get('candidate_id') or new_id('cand_')
            parsed.append(
                RuntimeCandidate(
                    candidate_id=cid,
                    bridge_id=bridge_id,
                    runtime=row.get('runtime', 'claude_code'),
                    adapter_kind=row.get('adapter_kind', 'acp'),
                    label=row.get('label', ''),
                    cwd=row.get('cwd'),
                    runtime_session_id=row.get('runtime_session_id'),
                    attachable=bool(row.get('attachable', True)),
                    bound_endpoint_id=row.get('bound_endpoint_id'),
                    meta=dict(row.get('meta') or {}),
                ))
        state.candidates.replace_for_bridge(bridge_id, parsed)


@router.websocket('/bridge')
async def bridge_ws(websocket: WebSocket):
    """Authenticated Host Bridge socket (BridgeToken → bridge_id)."""
    state = get_team_state()
    token_str = _extract_bearer(websocket)
    if not token_str:
        await websocket.close(code=4401, reason='missing bridge token')
        return
    btok = state.bridge_tokens.get(token_str)
    if btok is None:
        await websocket.close(code=4401, reason='invalid or expired token')
        return
    bridge_id = btok.bridge_id
    bridge = state.bridges.get(bridge_id)
    if bridge is None:
        await websocket.close(code=4404, reason='bridge not registered')
        return

    await websocket.accept()
    hub = get_bridge_hub()
    await hub.register(bridge_id, websocket)
    _heartbeat_bridge(state, bridge_id, instance_id=None, status='online')
    state.ensure_health_loop()
    agents = [e.to_dict() for e in state.endpoints.list_by_bridge(bridge_id)]
    await websocket.send_json({
        'type': 'registered',
        'bridge_id': bridge_id,
        'agents': agents,
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    'type': 'error',
                    'error': 'invalid_json'
                })
                continue
            mtype = msg.get('type')
            if mtype in ('register_bridge', 'heartbeat', 'register_endpoint'):
                _heartbeat_bridge(
                    state,
                    bridge_id,
                    instance_id=msg.get('instance_id'),
                    status=msg.get('status', 'online'),
                    agents=msg.get('agents'),
                    candidates=msg.get('candidates'),
                )
                await hub.register(bridge_id, websocket)
                if mtype in ('register_bridge', 'register_endpoint'):
                    await websocket.send_json({
                        'type': 'registered',
                        'bridge_id': bridge_id,
                        'agents': [
                            e.to_dict()
                            for e in state.endpoints.list_by_bridge(bridge_id)
                        ],
                    })
            elif mtype == 'stream_event':
                endpoint_id = (
                    msg.get('endpoint_id')
                    or (msg.get('event') or {}).get('endpoint_id'))
                ev = TeamEvent(
                    type='team.stream',
                    dispatch_id=msg.get('dispatch_id'),
                    endpoint_id=endpoint_id,
                    payload=msg.get('event') or {},
                )
                await state._fanout_event(ev)  # noqa: SLF001
            elif mtype == 'dispatch_done':
                hub.complete_dispatch(
                    msg.get('dispatch_id'),
                    {
                        'ok': msg.get('ok', True),
                        'summary': msg.get('summary', ''),
                        'artifacts': msg.get('artifacts') or [],
                        'dispatch_id': msg.get('dispatch_id'),
                        'code': msg.get('code'),
                    },
                )
                event_type = (
                    'team.dispatch_done'
                    if msg.get('ok', True) else 'team.dispatch_error')
                await state._fanout_event(  # noqa: SLF001
                    TeamEvent(
                        type=event_type,
                        dispatch_id=msg.get('dispatch_id'),
                        endpoint_id=msg.get('endpoint_id'),
                        payload={
                            'summary': msg.get('summary', ''),
                            'ok': msg.get('ok', True),
                            'code': msg.get('code'),
                        },
                    ))
            else:
                await websocket.send_json({'type': 'ack', 'ref': mtype})
    except WebSocketDisconnect:
        logger.info('Bridge disconnected: %s', bridge_id)
    finally:
        # If a newer daemon already replaced this socket, do not unregister it
        # or stamp the bridge offline — that would race the live connection.
        still_mine = hub.get(bridge_id) is websocket
        await hub.unregister(bridge_id, ws=websocket)
        if still_mine:
            # Mark offline immediately (no attach). Keep @at_name registered so a
            # reconnect can reclaim it; release only when another bind needs it.
            _heartbeat_bridge(
                state, bridge_id, instance_id=None, status='offline')
            state.ensure_health_loop()
