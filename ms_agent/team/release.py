# Copyright (c) ModelScope Contributors. All rights reserved.
"""Release Host Bridge agents and free unique @at_name slots."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ms_agent.team.events import TeamEvent
from ms_agent.team.models import AgentEndpoint

logger = logging.getLogger(__name__)

EventEmitter = Callable[[TeamEvent], Awaitable[None] | None]


async def release_endpoint(
    *,
    endpoints: Any,
    session_bindings: Any | None = None,
    candidates: Any | None = None,
    endpoint: AgentEndpoint,
    reason: str = 'bridge_offline',
    event_sink: EventEmitter | None = None,
) -> bool:
    """Delete a local endpoint so its ``@at_name`` can be claimed again."""
    eid = endpoint.endpoint_id
    at_name = endpoint.at_name
    if session_bindings is not None and hasattr(
            session_bindings, 'list_for_endpoint'):
        for binding in list(session_bindings.list_for_endpoint(eid)):
            try:
                session_bindings.delete(binding.binding_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    'failed to drop session binding %s',
                    getattr(binding, 'binding_id', '?'),
                    exc_info=True,
                )
    if candidates is not None and endpoint.bridge_id and hasattr(
            candidates, 'list_for_bridge'):
        try:
            rows = list(candidates.list_for_bridge(endpoint.bridge_id))
            changed = False
            for row in rows:
                if getattr(row, 'bound_endpoint_id', None) == eid:
                    row.bound_endpoint_id = None
                    changed = True
            if changed:
                candidates.replace_for_bridge(endpoint.bridge_id, rows)
        except Exception:  # noqa: BLE001
            logger.debug(
                'failed to clear candidates for %s', eid, exc_info=True)

    ok = bool(endpoints.delete(eid))
    if ok:
        logger.info('Released @%s (%s) reason=%s', at_name, eid, reason)
        await _emit(
            event_sink,
            TeamEvent(
                type='endpoint.unregistered',
                endpoint_id=eid,
                at_name=at_name,
                payload={
                    'reason': reason,
                    'bridge_id': endpoint.bridge_id,
                    'runtime': endpoint.runtime,
                },
            ),
        )
    return ok


async def release_bridge_local_agents(
    *,
    endpoints: Any,
    session_bindings: Any | None = None,
    candidates: Any | None = None,
    bridge_id: str,
    reason: str = 'bridge_disconnect',
    event_sink: EventEmitter | None = None,
    keep_cloud: bool = True,
) -> list[str]:
    """Unregister local agents on a Host Bridge; free their ``@`` names.

    Cloud endpoints are only marked offline when ``keep_cloud`` is true.
    """
    released: list[str] = []
    for ep in list(endpoints.list_by_bridge(bridge_id)):
        if keep_cloud and getattr(ep, 'adapter_kind', '') == 'cloud':
            ep.status = 'offline'  # type: ignore[assignment]
            endpoints.upsert(ep)
            continue
        if await release_endpoint(
                endpoints=endpoints,
                session_bindings=session_bindings,
                candidates=candidates,
                endpoint=ep,
                reason=reason,
                event_sink=event_sink,
        ):
            released.append(ep.at_name)
    return released


async def _emit(sink: EventEmitter | None, event: TeamEvent) -> None:
    if sink is None:
        return
    try:
        result = sink(event)
        if hasattr(result, '__await__'):
            await result  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        logger.debug('release event sink failed', exc_info=True)
