# Copyright (c) ModelScope Contributors. All rights reserved.
"""Endpoint registry REST API."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ms_agent.team.errors import TeamError
from ms_agent.team.models import (
    AgentEndpoint,
    InvokePolicy,
    RemoteProfile,
    new_id,
    new_secret_token,
)
from ms_agent.team.router import AtRouter
from team.state import get_team_state

router = APIRouter(prefix='/endpoints', tags=['team-endpoints'])


class PairTokenRequest(BaseModel):
    owner_user_id: str
    ttl_minutes: int = 60


class PairRequest(BaseModel):
    pair_code: str
    at_name: str
    machine_label: str = ''
    endpoint_type: str = 'persistent'
    runtime: str = 'claude_code'
    endpoint_id: Optional[str] = None
    adapter_kind: str = 'acp'


class EndpointUpsert(BaseModel):
    endpoint_id: Optional[str] = None
    at_name: str
    owner_user_id: str
    endpoint_type: str = 'persistent'
    runtime: str = 'claude_code'
    adapter_kind: str = 'cloud'
    machine_label: str = ''
    bridge_id: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    default_project_id: Optional[str] = None
    # Reserved policy fields — always accepted & stored.
    invoke_policy: str = 'owner_only'
    remote_profile: str = 'owner_only'
    invoke_allowlist: list[str] = Field(default_factory=list)
    remote_invoke_enabled: bool = False
    status: str = 'offline'


class IssueTokenRequest(BaseModel):
    owner_user_id: str
    ttl_seconds: int = 86400


def _viewer(x_user_id: Optional[str]) -> str:
    return x_user_id or 'anonymous'


@router.post('/pair-token')
def create_pair_token(body: PairTokenRequest):
    """Deprecated: use POST /bridges/pair-token (Host Bridge model)."""
    raise HTTPException(410, detail={
        'error': 'GONE',
        'message':
        'Use POST /api/v1/team/bridges/pair-token. '
        'Bridge↔Agent 1:1 pairing is removed.',
    })


@router.post('/pair')
def pair_endpoint(body: PairRequest):
    """Deprecated: use POST /bridges/pair then /bridges/{id}/agents."""
    raise HTTPException(410, detail={
        'error': 'GONE',
        'message':
        'Use POST /api/v1/team/bridges/pair. '
        'One Agent per WebSocket is no longer supported.',
    })


@router.post('')
def upsert_endpoint(body: EndpointUpsert):
    state = get_team_state()
    ep = AgentEndpoint(
        endpoint_id=body.endpoint_id or new_id('ep_'),
        at_name=body.at_name.lstrip('@'),
        owner_user_id=body.owner_user_id,
        endpoint_type=body.endpoint_type,  # type: ignore[arg-type]
        runtime=body.runtime,
        adapter_kind=body.adapter_kind,  # type: ignore[arg-type]
        machine_label=body.machine_label,
        bridge_id=body.bridge_id,
        capabilities=body.capabilities,
        default_project_id=body.default_project_id,
        invoke_policy=InvokePolicy(body.invoke_policy),
        remote_profile=RemoteProfile(body.remote_profile),
        invoke_allowlist=body.invoke_allowlist,
        remote_invoke_enabled=body.remote_invoke_enabled
        if state.flags.remote_invoke_enabled else False,
        status=body.status,  # type: ignore[arg-type]
    )
    try:
        saved = state.registry.register(ep)
    except TeamError as exc:
        raise HTTPException(exc.http_status, detail=exc.to_dict()) from exc
    return saved.to_dict()


@router.get('')
def list_endpoints(
    owner_user_id: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None, alias='X-User-Id'),
):
    state = get_team_state()
    items = state.endpoints.list(owner_user_id)
    viewer = _viewer(x_user_id)
    return {
        'endpoints': AtRouter.visibility_for_viewer(items, viewer),
        'feature_flags': state.flags.to_dict(),
    }


@router.get('/{endpoint_id}/status')
def endpoint_status(endpoint_id: str):
    state = get_team_state()
    state.ensure_health_loop()
    ep = state.endpoints.get(endpoint_id)
    if ep is None:
        raise HTTPException(404, detail={'error': 'ENDPOINT_NOT_FOUND'})
    health = state.health.view(endpoint_id)
    return {
        'endpoint_id': ep.endpoint_id,
        'at_name': ep.at_name,
        'status': ep.status,
        'health': health.to_dict(),
        'endpoint_type': ep.endpoint_type,
        'instance_id': ep.current_instance_id,
        'last_heartbeat': ep.last_heartbeat,
    }


@router.get('/{endpoint_id}/health')
def endpoint_health(endpoint_id: str):
    state = get_team_state()
    state.ensure_health_loop()
    if state.endpoints.get(endpoint_id) is None:
        raise HTTPException(404, detail={'error': 'ENDPOINT_NOT_FOUND'})
    return state.health.view(endpoint_id).to_dict()


@router.get('/{endpoint_id}/sessions')
def list_sessions(endpoint_id: str):
    state = get_team_state()
    if state.endpoints.get(endpoint_id) is None:
        raise HTTPException(404, detail={'error': 'ENDPOINT_NOT_FOUND'})
    return {
        'sessions': [
            s.to_dict() for s in state.sessions.list_for_endpoint(endpoint_id)
        ]
    }


@router.delete('/{endpoint_id}/sessions/{binding_id}')
def delete_session(endpoint_id: str, binding_id: str):
    state = get_team_state()
    binding = state.session_bindings.get(binding_id)
    if binding is None or binding.endpoint_id != endpoint_id:
        raise HTTPException(404, detail={'error': 'SESSION_NOT_FOUND'})
    state.sessions.invalidate(binding_id)
    return {'ok': True, 'binding_id': binding_id}


@router.post('/{endpoint_id}/tokens')
def issue_token(endpoint_id: str, body: IssueTokenRequest):
    state = get_team_state()
    try:
        tok = state.registry.issue_endpoint_token(
            endpoint_id,
            body.owner_user_id,
            state.endpoint_tokens,
            ttl_seconds=body.ttl_seconds,
        )
    except TeamError as exc:
        raise HTTPException(exc.http_status, detail=exc.to_dict()) from exc
    return tok.to_dict()
