# Copyright (c) ModelScope Contributors. All rights reserved.
"""Endpoint health state machine driven by heartbeats."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ms_agent.team.events import TeamEvent
from ms_agent.team.models import AgentEndpoint, EndpointHealth, HealthState
from ms_agent.team.stores.base import EndpointStore

logger = logging.getLogger(__name__)

EventEmitter = Callable[[TeamEvent], Awaitable[None] | None]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


class HealthMonitor:
    """Derive online/degraded/offline from last_heartbeat age.

    need_reauth is sticky until a successful heartbeat clears it (caller
    must set status=need_reauth explicitly via observe).

    Offline is sticky until a real bridge heartbeat/observe updates the
    endpoint — age alone will not revive a disconnected bridge. Offline
    agents keep their ``@at_name`` so reconnect can reclaim; timed auto-
    release is intentionally not used.

    MachineBridge rows are scanned the same way: a persisted ``online``
    status with a stale ``last_heartbeat`` (e.g. after control-plane
    restart with no WS disconnect finally) is forced offline so the UI
    does not keep showing a dead Host Bridge as live.
    """

    def __init__(
        self,
        endpoint_store: EndpointStore,
        *,
        event_sink: EventEmitter | None = None,
        degraded_after_s: float | None = None,
        offline_after_s: float | None = None,
        bridges: Any | None = None,
        session_bindings: Any | None = None,
        candidates: Any | None = None,
        **_legacy: Any,
    ) -> None:
        del session_bindings, candidates, _legacy  # kept for call-site compat
        self.store = endpoint_store
        self.bridges = bridges
        self.event_sink = event_sink
        self.degraded_after_s = degraded_after_s if degraded_after_s is not None \
            else _env_float('MS_AGENT_BRIDGE_HEARTBEAT_MISS_S', 45.0)
        self.offline_after_s = offline_after_s if offline_after_s is not None \
            else _env_float('MS_AGENT_BRIDGE_OFFLINE_S', 120.0)

    def view(self, endpoint_id: str) -> EndpointHealth:
        ep = self.store.get(endpoint_id)
        if ep is None:
            return EndpointHealth(
                endpoint_id=endpoint_id,
                state='unregistered',
                reason='not_found',
                detail='Endpoint is not registered.',
            )
        return self._health_for(ep, datetime.now(timezone.utc))

    def list_views(self, owner_user_id: str | None = None) -> list[EndpointHealth]:
        now = datetime.now(timezone.utc)
        return [self._health_for(ep, now) for ep in self.store.list(owner_user_id)]

    async def tick(self) -> list[EndpointHealth]:
        """Scan endpoints + bridges: degrade/offline by age."""
        self.reconcile_bridges()
        now = datetime.now(timezone.utc)
        changed: list[EndpointHealth] = []
        for ep in list(self.store.list()):
            if ep.status == 'need_reauth':
                continue
            if getattr(ep, 'adapter_kind', '') == 'cloud':
                continue
            # Sticky offline: wait for a real heartbeat (observe / WS).
            if ep.status == 'offline':
                continue

            desired, reason = self._desired_from_age(ep, now)
            if desired == ep.status:
                continue
            prev = ep.status
            ep.status = desired  # type: ignore[assignment]
            ep.updated_at = now.isoformat()
            self.store.upsert(ep)
            health = self._health_for(ep, now, reason=reason)
            changed.append(health)
            await self._emit_status(ep, health, prev)
        return changed

    def reconcile_bridges(self) -> list[str]:
        """Force stale MachineBridge rows offline (sync; safe from REST).

        Returns bridge_ids that transitioned to offline.
        """
        if self.bridges is None or not hasattr(self.bridges, 'list'):
            return []
        now = datetime.now(timezone.utc)
        retired: list[str] = []
        for bridge in list(self.bridges.list()):
            status = getattr(bridge, 'status', None)
            if status in ('offline', None):
                continue
            last = _parse_iso(getattr(bridge, 'last_heartbeat', None))
            if last is None:
                desired = 'offline'
            else:
                age = (now - last).total_seconds()
                if age >= self.offline_after_s:
                    desired = 'offline'
                elif age >= self.degraded_after_s:
                    desired = 'degraded'
                else:
                    continue
            if desired == status:
                continue
            bridge.status = desired  # type: ignore[assignment]
            bridge.updated_at = now.isoformat()
            self.bridges.upsert(bridge)
            if desired == 'offline':
                retired.append(bridge.bridge_id)
                self._cascade_bridge_agents_offline(bridge.bridge_id, now)
        return retired

    def _cascade_bridge_agents_offline(
        self,
        bridge_id: str,
        now: datetime,
    ) -> None:
        list_fn = getattr(self.store, 'list_by_bridge', None)
        if not callable(list_fn):
            return
        for ep in list(list_fn(bridge_id)):
            if getattr(ep, 'adapter_kind', '') == 'cloud':
                continue
            if ep.status == 'offline':
                continue
            ep.status = 'offline'  # type: ignore[assignment]
            ep.updated_at = now.isoformat()
            self.store.upsert(ep)

    async def observe(
        self,
        endpoint_id: str,
        *,
        status: str = 'online',
        instance_id: str | None = None,
        reason: str = '',
    ) -> EndpointHealth | None:
        """Apply an explicit heartbeat / status observation."""
        ep = self.store.get(endpoint_id)
        if ep is None:
            return None
        prev = ep.status
        now = datetime.now(timezone.utc).isoformat()
        ep.last_heartbeat = now
        ep.updated_at = now
        if instance_id:
            ep.current_instance_id = instance_id
        # Heartbeat clears need_reauth / degraded unless explicitly set.
        if status == 'need_reauth':
            ep.status = 'need_reauth'
        else:
            ep.status = status  # type: ignore[assignment]
        self.store.upsert(ep)
        health = self._health_for(
            ep, datetime.now(timezone.utc), reason=reason or 'heartbeat')
        if prev != ep.status:
            await self._emit_status(ep, health, prev)
        return health

    def _desired_from_age(
        self,
        ep: AgentEndpoint,
        now: datetime,
    ) -> tuple[str, str]:
        last = _parse_iso(ep.last_heartbeat)
        if last is None:
            return 'offline', 'no_heartbeat'
        age = (now - last).total_seconds()
        if age >= self.offline_after_s:
            return 'offline', 'heartbeat_miss'
        if age >= self.degraded_after_s:
            return 'degraded', 'heartbeat_miss'
        # Preserve busy if still fresh.
        if ep.status == 'busy':
            return 'busy', 'in_dispatch'
        return 'online', 'heartbeat_ok'

    def _health_for(
        self,
        ep: AgentEndpoint,
        now: datetime,
        *,
        reason: str = '',
    ) -> EndpointHealth:
        state: HealthState
        if ep.status == 'need_reauth':
            state = 'need_reauth'
            reason = reason or 'need_reauth'
        elif ep.status == 'busy':
            state = 'busy'
            reason = reason or 'in_dispatch'
        elif ep.status == 'degraded':
            state = 'degraded'
            reason = reason or 'heartbeat_miss'
        elif ep.status in ('offline', 'reconnecting'):
            state = 'offline' if ep.status == 'offline' else 'degraded'
            reason = reason or ('heartbeat_miss'
                                if ep.status == 'offline' else 'reconnecting')
        else:
            # Re-check age for online claims.
            desired, age_reason = self._desired_from_age(ep, now)
            if desired != 'online' and desired != 'busy':
                state = desired  # type: ignore[assignment]
                reason = reason or age_reason
            else:
                state = 'online'
                reason = reason or 'ok'

        detail_map = {
            'heartbeat_miss': 'No recent heartbeat from bridge.',
            'no_heartbeat': 'Endpoint has never sent a heartbeat.',
            'need_reauth': 'Session or credentials require re-auth.',
            'in_dispatch': 'Endpoint is executing a dispatch.',
            'reconnecting': 'Endpoint is reconnecting.',
            'heartbeat': 'Heartbeat received.',
            'heartbeat_ok': 'Heartbeat within SLA.',
            'ok': 'Endpoint is healthy.',
        }
        return EndpointHealth(
            endpoint_id=ep.endpoint_id,
            state=state,
            last_seen=ep.last_heartbeat,
            reason=reason,
            detail=detail_map.get(reason, reason),
        )

    async def _emit_status(
        self,
        ep: AgentEndpoint,
        health: EndpointHealth,
        prev: str,
    ) -> None:
        if self.event_sink is None:
            return
        event = TeamEvent(
            type='endpoint.status',
            endpoint_id=ep.endpoint_id,
            at_name=ep.at_name,
            payload={
                'state': health.state,
                'prev': prev,
                'reason': health.reason,
                'detail': health.detail,
                'last_seen': health.last_seen,
            },
        )
        result = self.event_sink(event)
        if hasattr(result, '__await__'):
            await result  # type: ignore[misc]
