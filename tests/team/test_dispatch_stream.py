# Copyright (c) ModelScope Contributors. All rights reserved.
"""C-05: per-dispatch private stream persist + HTTP replay."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HTTP = _ROOT / 'webui' / 'backend'
for _p in (str(_ROOT), str(_HTTP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'

from fastapi import FastAPI
from fastapi.testclient import TestClient

from team import router as team_router
from team.state import reset_team_state_for_tests


def _client() -> TestClient:
    reset_team_state_for_tests()
    os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
    os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'
    app = FastAPI()
    app.include_router(team_router)
    return TestClient(app)


@pytest.fixture
def api():
    client = _client()
    try:
        proj = client.post(
            '/api/v1/team/projects',
            json={
                'project_id': 'proj_c05',
                'name': 'c05-stream',
                'default_lead_at': 'planner',
            },
        )
        assert proj.status_code == 200, proj.text
        for name, eid in (
                ('planner', 'ep-lead'),
                ('codex', 'ep-codex'),
        ):
            r = client.post(
                '/api/v1/team/endpoints',
                json={
                    'endpoint_id': eid,
                    'at_name': name,
                    'owner_user_id': 'u1',
                    'endpoint_type': 'persistent',
                    'runtime': 'ms_agent',
                    'adapter_kind': 'cloud',
                    'status': 'online',
                },
            )
            assert r.status_code == 200, r.text
        yield client
    finally:
        reset_team_state_for_tests()


def test_dispatch_get_and_stream_replay(api: TestClient):
    r = api.post(
        '/api/v1/team/projects/proj_c05/messages',
        params={'wait': 'true', 'wait_timeout': 15},
        json={
            'content': '@codex 你找北京美景',
            'sender_user_id': 'u1',
            'channel': 'web',
            'thread_id': 'web-main',
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    did = body['dispatches'][0]['dispatch_id']

    missing = api.get('/api/v1/team/dispatches/does_not_exist')
    assert missing.status_code == 404

    detail = api.get(f'/api/v1/team/dispatches/{did}').json()
    assert detail['dispatch_id'] == did
    assert detail['at_name'] == 'codex'
    assert detail['status'] in ('done', 'running')
    assert detail['event_count'] >= 1

    from team.state import get_team_state
    events = get_team_state().dispatch_log.list(did)
    types = {e.type for e in events}
    assert 'team.dispatch_start' in types or 'team.stream' in types
    assert any(getattr(e, 'dispatch_id', None) == did for e in events)
    assert any(getattr(e, 'at_name', None) == 'codex' for e in events)


def test_acp_update_maps_tool_calls():
    from ms_agent.bridge.adapters.acp_runtime import _acp_update_to_events

    start = _acp_update_to_events(
        {
            'params': {
                'update': {
                    'sessionUpdate': 'tool_call',
                    'toolCallId': 'tc1',
                    'title': 'Read file',
                    'kind': 'read',
                    'status': 'in_progress',
                    'rawInput': {'path': 'a.py'},
                }
            }
        },
        'sid-1',
    )
    assert len(start) == 1
    assert start[0].type == 'tool_call'
    assert start[0].payload['call_id'] == 'tc1'
    assert start[0].payload['status'] == 'running'

    done = _acp_update_to_events(
        {
            'params': {
                'update': {
                    'sessionUpdate': 'tool_call_update',
                    'toolCallId': 'tc1',
                    'status': 'completed',
                    'content': [{'type': 'text', 'text': 'ok'}],
                }
            }
        },
        'sid-1',
    )
    assert done[0].type == 'tool_result'
    assert done[0].payload['status'] == 'done'
    assert 'ok' in done[0].content
