# Copyright (c) ModelScope Contributors. All rights reserved.
"""Pairing helpers for Host Bridge (one sidecar per machine)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def pair_bridge_with_platform(
    api_base: str,
    *,
    pair_code: str,
    machine_label: str = '',
    bridge_id: str | None = None,
    agents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """HTTP pair call — returns bridge + agents + bridge_token + ws_url."""
    url = api_base.rstrip('/') + '/api/v1/team/bridges/pair'
    body = {
        'pair_code': pair_code,
        'machine_label': machine_label,
        'bridge_id': bridge_id,
        'agents': agents or [],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Pair failed ({exc.code}): {detail}') from exc


def register_agent_on_bridge(
    api_base: str,
    *,
    bridge_id: str,
    at_name: str,
    runtime: str = 'claude_code',
    adapter_kind: str = 'acp',
    candidate_id: str | None = None,
    endpoint_id: str | None = None,
) -> dict[str, Any]:
    url = (
        api_base.rstrip('/')
        + f'/api/v1/team/bridges/{bridge_id}/agents')
    body = {
        'at_name': at_name.lstrip('@'),
        'runtime': runtime,
        'adapter_kind': adapter_kind,
        'candidate_id': candidate_id,
        'endpoint_id': endpoint_id,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(
            f'Register agent failed ({exc.code}): {detail}') from exc


# Back-compat alias name used by older docs — now raises.
def pair_with_platform(*_args, **_kwargs):
    raise RuntimeError(
        'pair_with_platform(/endpoints/pair) is removed. '
        'Use pair_bridge_with_platform(/bridges/pair).')
