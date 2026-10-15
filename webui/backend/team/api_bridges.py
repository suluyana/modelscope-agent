# Copyright (c) ModelScope Contributors. All rights reserved.
"""Host Bridge REST API: one sidecar per machine, many Agents."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ms_agent.bridge.devmode import bridge_dev_mode
from ms_agent.bridge.reach import (
    cli_api_base,
    local_debug_command,
    remote_connect_command,
)
from ms_agent.team.errors import TeamError
from ms_agent.team.models import (
    AgentEndpoint,
    BridgeToken,
    InvokePolicy,
    MachineBridge,
    PairToken,
    RemoteProfile,
    new_id,
    new_secret_token,
)
from team.state import get_team_state

router = APIRouter(prefix='/bridges', tags=['team-bridges'])


class PairTokenRequest(BaseModel):
    owner_user_id: str
    ttl_minutes: int = 60


class BridgePairRequest(BaseModel):
    pair_code: str
    machine_label: str = ''
    bridge_id: Optional[str] = None
    agents: list[dict[str, Any]] = Field(default_factory=list)


class AgentRegisterRequest(BaseModel):
    endpoint_id: Optional[str] = None
    at_name: str
    runtime: str = 'claude_code'
    adapter_kind: str = 'acp'
    endpoint_type: str = 'persistent'
    machine_label: str = ''
    capabilities: list[str] = Field(default_factory=list)
    default_project_id: Optional[str] = None
    status: str = 'offline'
    candidate_id: Optional[str] = None


class IssueBridgeTokenRequest(BaseModel):
    owner_user_id: str
    ttl_seconds: int = 86400


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bridge_ts(bridge: MachineBridge) -> float:
    for value in (bridge.last_heartbeat, bridge.updated_at, bridge.created_at):
        if not value:
            continue
        try:
            return datetime.fromisoformat(
                str(value).replace('Z', '+00:00')).timestamp()
        except ValueError:
            continue
    return 0.0


def _find_canonical_bridge(
    state,
    *,
    owner_user_id: str,
    machine_label: str,
) -> MachineBridge | None:
    """Pick the durable Host Bridge for this owner+machine (prefer online)."""
    label = (machine_label or '').strip()
    if not label:
        return None
    peers = [
        b for b in state.bridges.list(owner_user_id)
        if (b.machine_label or '').strip() == label
    ]
    if not peers:
        return None
    online = [b for b in peers if getattr(b, 'status', None) == 'online']
    pool = online or peers
    pool.sort(key=_bridge_ts, reverse=True)
    return pool[0]


def _kick_bridge_sockets(bridge_ids: list[str], *, reason: str) -> None:
    """Best-effort: drop live WS for retired / replaced bridges."""
    if not bridge_ids:
        return
    try:
        from team.ws_bridge_hub import get_bridge_hub
        hub = get_bridge_hub()
    except Exception:  # noqa: BLE001
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for bid in bridge_ids:
        try:
            loop.create_task(
                hub.force_disconnect(bid, reason=reason))
        except Exception:  # noqa: BLE001
            pass


def _retire_same_machine_bridges(
    state,
    *,
    keep: MachineBridge,
    include_online: bool = True,
) -> list[str]:
    """Delete other bridges for the same owner + machine_label.

    Agents are rebound onto ``keep`` (offline until the live daemon
    heartbeats). Candidates for retired bridges are cleared. Online siblings
    are retired too when ``include_online`` — one machine, one bridge.
    """
    label = (keep.machine_label or '').strip()
    if not label:
        # Unlabeled bridges are mostly e2e noise; refuse to mass-delete them.
        return []

    retired: list[str] = []
    for old in list(state.bridges.list(keep.owner_user_id)):
        if old.bridge_id == keep.bridge_id:
            continue
        if (old.machine_label or '').strip() != label:
            continue
        if not include_online and getattr(old, 'status', None) == 'online':
            continue

        for ep in list(state.endpoints.list_by_bridge(old.bridge_id)):
            ep.bridge_id = keep.bridge_id
            ep.machine_label = label
            # Wait for the live daemon heartbeat — do not forge online.
            ep.status = 'offline'  # type: ignore[assignment]
            ep.updated_at = _now().isoformat()
            state.registry.register(ep)

        try:
            state.candidates.replace_for_bridge(old.bridge_id, [])
        except Exception:  # noqa: BLE001
            pass

        if state.bridges.delete(old.bridge_id):
            retired.append(old.bridge_id)
    return retired


# Local WebUI used to persist endpoints as owner ``u1``; the Team page now
# pairs as ``webui``. Treat them as one identity so enable is not a 409.
_LOCAL_OWNER_ALIASES = frozenset({'u1', 'webui'})


def _same_owner_identity(left: str, right: str) -> bool:
    if left == right:
        return True
    return left in _LOCAL_OWNER_ALIASES and right in _LOCAL_OWNER_ALIASES


def _bridge_is_live(state, bridge_id: Optional[str]) -> bool:
    if not bridge_id:
        return False
    peer = state.bridges.get(bridge_id)
    if peer is None:
        return False
    return getattr(peer, 'status', None) == 'online'


def _can_claim_existing_endpoint(state, existing: AgentEndpoint,
                                 bridge: MachineBridge) -> bool:
    """True when this Host Bridge may take over an existing @name."""
    if _same_owner_identity(existing.owner_user_id, bridge.owner_user_id):
        return True
    if _bridge_is_live(state, existing.bridge_id):
        return False
    exist_label = (existing.machine_label or '').strip()
    want_label = (bridge.machine_label or '').strip()
    return bool(exist_label and want_label and exist_label == want_label)


def _bind_agent_to_bridge(
    state,
    *,
    bridge: MachineBridge,
    at_name: str,
    runtime: str = 'claude_code',
    adapter_kind: str = 'acp',
    endpoint_type: str = 'persistent',
    endpoint_id: Optional[str] = None,
    machine_label: str = '',
    capabilities: Optional[list[str]] = None,
    default_project_id: Optional[str] = None,
    status: str = 'offline',
    candidate_id: Optional[str] = None,
) -> AgentEndpoint:
    """Create or rebind Agent onto this Host Bridge.

    Reconnect / re-pair must not 409 when ``@me`` (or any name) already exists
    for the same owner — move it onto the new bridge_id instead.

    The local demo owner ``u1`` is an alias of ``webui``: leftover names from
    earlier click-throughs (``@codex``, ``@cursor``, …) are claimed rather
    than rejected as another owner's conflict.
    """
    at_name = at_name.lstrip('@')
    existing = state.endpoints.get_by_at_name(at_name)
    if existing is not None:
        if existing.owner_user_id != bridge.owner_user_id:
            if not _can_claim_existing_endpoint(state, existing, bridge):
                raise TeamError(
                    'AT_NAME_CONFLICT',
                    f'@{at_name} is already registered by another owner.',
                    http_status=409,
                    details={
                        'existing_endpoint_id': existing.endpoint_id,
                        'existing_owner_user_id': existing.owner_user_id,
                    },
                )
            existing.owner_user_id = bridge.owner_user_id
        existing.bridge_id = bridge.bridge_id
        existing.runtime = runtime
        existing.adapter_kind = adapter_kind  # type: ignore[assignment]
        existing.machine_label = machine_label or bridge.machine_label
        if capabilities is not None:
            existing.capabilities = list(capabilities)
        if default_project_id is not None:
            existing.default_project_id = default_project_id
        # Do not forge online/last_heartbeat here — Host Bridge heartbeat
        # is the source of truth after agents_updated sync.
        existing.status = status  # type: ignore[assignment]
        existing.updated_at = _now().isoformat()
        saved = state.registry.register(existing)
    else:
        ep = AgentEndpoint(
            endpoint_id=endpoint_id or new_id('ep_'),
            at_name=at_name,
            owner_user_id=bridge.owner_user_id,
            endpoint_type=endpoint_type,  # type: ignore[arg-type]
            runtime=runtime,
            adapter_kind=adapter_kind,  # type: ignore[arg-type]
            machine_label=machine_label or bridge.machine_label,
            bridge_id=bridge.bridge_id,
            capabilities=list(capabilities or []),
            default_project_id=default_project_id,
            invoke_policy=InvokePolicy.OWNER_ONLY,
            remote_profile=RemoteProfile.OWNER_ONLY,
            status=status,  # type: ignore[arg-type]
        )
        saved = state.registry.register(ep)

    if candidate_id:
        cands = state.candidates.list_for_bridge(bridge.bridge_id)
        updated = []
        for c in cands:
            if c.candidate_id == candidate_id:
                c.bound_endpoint_id = saved.endpoint_id
            updated.append(c)
        state.candidates.replace_for_bridge(bridge.bridge_id, updated)
    return saved


@router.post('/pair-token')
def create_bridge_pair_token(body: PairTokenRequest, request: Request):
    state = get_team_state()
    code = new_secret_token('pair_')
    expires = (_now() + timedelta(minutes=body.ttl_minutes)).isoformat()
    tok = PairToken(
        pair_code=code,
        owner_user_id=body.owner_user_id,
        expires_at=expires,
    )
    state.pair_tokens.put(tok)
    payload = tok.to_dict()
    server = request.scope.get('server') or ('', 8000)
    port = server[1] if len(server) > 1 and server[1] else 8000
    # Not stored on the token. --dev on the WebUI adds a loopback command
    # whose daemon flag is also --dev. Production only gets the remote command.
    api_base = cli_api_base(port)
    dev = bridge_dev_mode()
    payload['api_base'] = api_base
    payload['dev'] = dev
    payload['remote_command'] = remote_connect_command(code, api_base)
    if dev:
        payload['local_command'] = local_debug_command(code, port)
    return payload


@router.post('/pair')
def pair_bridge(body: BridgePairRequest):
    """Pair a MachineBridge (+ optional initial Agents). Returns BridgeToken.

    Same owner + machine_label reuses the canonical bridge (prefer the
    currently online one) so a reconnect never creates a second online
    Host Bridge on one machine.
    """
    state = get_team_state()
    tok = state.pair_tokens.consume(body.pair_code)
    if tok is None:
        raise HTTPException(400, detail={
            'error': 'INVALID_PAIR_CODE',
            'message': 'Pair code missing or already consumed',
        })

    label = (body.machine_label or '').strip()
    reused = False
    canonical = _find_canonical_bridge(
        state,
        owner_user_id=tok.owner_user_id,
        machine_label=label,
    )
    if canonical is not None:
        bridge = canonical
        bridge.machine_label = label or bridge.machine_label
        # Offline until the new daemon WS authenticates — avoids dual-online
        # while the previous socket is being kicked.
        bridge.status = 'offline'  # type: ignore[assignment]
        bridge.updated_at = _now().isoformat()
        state.bridges.upsert(bridge)
        reused = True
    else:
        bridge_id = body.bridge_id or new_id('br_')
        bridge = MachineBridge(
            bridge_id=bridge_id,
            owner_user_id=tok.owner_user_id,
            machine_label=body.machine_label,
            status='offline',
        )
        state.bridges.upsert(bridge)

    # Drop every other same-machine bridge (online or offline) onto ``bridge``.
    retired_ids = _retire_same_machine_bridges(
        state, keep=bridge, include_online=True)
    # Kick old sockets: retired peers + this bridge's previous daemon (if any).
    _kick_bridge_sockets(
        [*retired_ids, bridge.bridge_id],
        reason='replaced by re-pair',
    )

    agents_out: list[dict] = []
    for spec in body.agents:
        at_name = str(spec.get('at_name') or '').lstrip('@')
        if not at_name:
            continue
        try:
            saved = _bind_agent_to_bridge(
                state,
                bridge=bridge,
                at_name=at_name,
                runtime=spec.get('runtime', 'claude_code'),
                adapter_kind=spec.get('adapter_kind', 'acp'),
                endpoint_type=spec.get('endpoint_type', 'persistent'),
                endpoint_id=spec.get('endpoint_id'),
                machine_label=body.machine_label
                or spec.get('machine_label', ''),
                capabilities=list(spec.get('capabilities') or []),
                status='offline',
            )
        except TeamError as exc:
            raise HTTPException(exc.http_status, detail=exc.to_dict()) from exc
        agents_out.append(saved.to_dict())

    btok = BridgeToken(
        token=new_secret_token('btok_'),
        bridge_id=bridge.bridge_id,
        owner_user_id=tok.owner_user_id,
        expires_at=(_now() + timedelta(seconds=86400)).isoformat(),
    )
    state.bridge_tokens.put(btok)
    return {
        'bridge': bridge.to_dict(),
        'agents': agents_out,
        'bridge_token': btok.token,
        'ws_url': '/api/v1/team/bridge',
        'retired_bridge_ids': retired_ids,
        'reused_bridge': reused,
    }


@router.get('')
def list_bridges(owner_user_id: Optional[str] = None):
    state = get_team_state()
    # Team page polls this — start the health loop and reconcile stale
    # MachineBridge "online" rows (e.g. after control-plane restart).
    state.ensure_health_loop()
    try:
        state.health.reconcile_bridges()
    except Exception:  # noqa: BLE001
        pass
    bridges = state.bridges.list(owner_user_id)
    return {
        'bridges': [
            {
                **b.to_dict(),
                'agents': [
                    e.to_dict()
                    for e in state.endpoints.list_by_bridge(b.bridge_id)
                ],
            }
            for b in bridges
        ]
    }


@router.get('/{bridge_id}')
def get_bridge(bridge_id: str):
    state = get_team_state()
    bridge = state.bridges.get(bridge_id)
    if bridge is None:
        raise HTTPException(404, detail={'error': 'BRIDGE_NOT_FOUND'})
    return {
        **bridge.to_dict(),
        'agents': [
            e.to_dict() for e in state.endpoints.list_by_bridge(bridge_id)
        ],
    }


@router.post('/{bridge_id}/agents')
async def register_agent_on_bridge(bridge_id: str, body: AgentRegisterRequest):
    state = get_team_state()
    bridge = state.bridges.get(bridge_id)
    if bridge is None:
        raise HTTPException(404, detail={'error': 'BRIDGE_NOT_FOUND'})
    try:
        saved = _bind_agent_to_bridge(
            state,
            bridge=bridge,
            at_name=body.at_name,
            runtime=body.runtime,
            adapter_kind=body.adapter_kind,
            endpoint_type=body.endpoint_type,
            endpoint_id=body.endpoint_id,
            machine_label=body.machine_label or bridge.machine_label,
            capabilities=body.capabilities,
            default_project_id=body.default_project_id,
            status=body.status,
            candidate_id=body.candidate_id,
        )
    except TeamError as exc:
        raise HTTPException(exc.http_status, detail=exc.to_dict()) from exc
    # Push to live Host Bridge so local slots + heartbeat catch up immediately.
    try:
        from team.ws_bridge_hub import get_bridge_hub
        await get_bridge_hub().notify_agents_updated(bridge_id)
    except Exception:  # noqa: BLE001
        pass
    return saved.to_dict()


@router.get('/{bridge_id}/candidates')
def list_candidates(bridge_id: str):
    state = get_team_state()
    if state.bridges.get(bridge_id) is None:
        raise HTTPException(404, detail={'error': 'BRIDGE_NOT_FOUND'})
    return {
        'candidates': [
            c.to_dict() for c in state.candidates.list_for_bridge(bridge_id)
        ]
    }


@router.post('/{bridge_id}/tokens')
def issue_bridge_token(bridge_id: str, body: IssueBridgeTokenRequest):
    state = get_team_state()
    bridge = state.bridges.get(bridge_id)
    if bridge is None:
        raise HTTPException(404, detail={'error': 'BRIDGE_NOT_FOUND'})
    if bridge.owner_user_id != body.owner_user_id:
        raise HTTPException(403, detail={'error': 'FORBIDDEN'})
    btok = BridgeToken(
        token=new_secret_token('btok_'),
        bridge_id=bridge_id,
        owner_user_id=body.owner_user_id,
        expires_at=(_now() + timedelta(seconds=body.ttl_seconds)).isoformat(),
    )
    state.bridge_tokens.put(btok)
    return btok.to_dict()
