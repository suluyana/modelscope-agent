# Copyright (c) ModelScope Contributors. All rights reserved.
"""C-14: Worker permission asks surface to Team, keyed by call_id."""
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

from ms_agent.bridge.adapters.acp_claude import AcpClaudeAdapter
from ms_agent.bridge.adapters.acp_client import AcpSession
from ms_agent.bridge.adapters.acp_runtime import permission_msg_to_event
from ms_agent.team.events import TeamEvent
from team import router as team_router
from team.state import get_team_state, reset_team_state_for_tests


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
        yield client
    finally:
        reset_team_state_for_tests()


def test_bridge_does_not_auto_allow_permissions_by_default():
    assert AcpClaudeAdapter().auto_allow_permissions is False
    sess = AcpSession(['echo'])
    assert sess.auto_allow is False


def test_permission_msg_to_event_keeps_call_id():
    ev = permission_msg_to_event(
        {
            'jsonrpc': '2.0',
            'id': 7,
            'method': 'session/request_permission',
            'params': {
                'toolCall': {
                    'toolCallId': 'call_w2',
                    'title': 'Bash',
                    'rawInput': {'command': 'curl https://example.com'},
                },
                'options': [{'optionId': 'allow-once'}],
            },
        },
        'sess-1',
    )
    assert ev is not None
    assert ev.type == 'permission_request'
    assert ev.payload['call_id'] == 'call_w2'
    assert ev.payload['acp_id'] == 7


def test_inbox_keys_parallel_workers_by_call_id(api: TestClient):
    import asyncio
    state = get_team_state()

    async def _seed():
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_p',
                dispatch_id='d1',
                at_name='coder',
                payload={
                    'request_id': 'req_a',
                    'call_id': 'call_a',
                    'tool_name': 'shell_executor',
                    'tool_args': {'cmd': 'curl a'},
                },
            ))
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_p',
                dispatch_id='d2',
                at_name='reviewer',
                payload={
                    'request_id': 'req_b',
                    'call_id': 'call_b',
                    'tool_name': 'shell_executor',
                    'tool_args': {'cmd': 'curl b'},
                },
            ))

    asyncio.run(_seed())
    asks = state.permission_inbox.list_pending(project_id='proj_p')
    assert {a['call_id'] for a in asks} == {'call_a', 'call_b'}
    assert state.permission_inbox.get_by_call('d1', 'call_a')['request_id'] == (
        'req_a')
    listed = api.get('/api/v1/team/projects/proj_p/permission_asks')
    assert listed.status_code == 200
    assert {row['call_id'] for row in listed.json()['asks']} == {
        'call_a',
        'call_b',
    }


def test_dispatch_status_waiting_approval(api: TestClient):
    import asyncio
    state = get_team_state()

    async def _seed():
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.dispatch_start',
                project_id='proj_w',
                dispatch_id='d_wait',
                at_name='coder',
                payload={'prompt': 'x'},
            ))
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.dispatch_waiting_approval',
                project_id='proj_w',
                dispatch_id='d_wait',
                payload={'call_id': 'c1', 'request_id': 'r1'},
            ))
        await state._fanout_event(  # noqa: SLF001
            TeamEvent(
                type='team.permission_requested',
                project_id='proj_w',
                dispatch_id='d_wait',
                payload={
                    'request_id': 'r1',
                    'call_id': 'c1',
                    'tool_name': 'shell',
                },
            ))

    asyncio.run(_seed())
    detail = api.get('/api/v1/team/dispatches/d_wait')
    assert detail.status_code == 200
    body = detail.json()
    assert body['status'] == 'waiting_approval'
    assert body['waiting_approval'] is True
    assert body['pending_permission_asks']
    assert body['pending_permission_asks'][0]['call_id'] == 'c1'
