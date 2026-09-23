# Copyright (c) ModelScope Contributors. All rights reserved.
"""E2E: complex job on one board — decompose → parallel → gated merge.

This is the product path, not '@everyone in one chat':
1. Lead writes subtasks (simulated task_board_write).
2. Coordinator runs the board (按板执行).
3. Independent @bibo / @lily fire together; merge stays blocked.
4. After both complete, merge @ms_agent is released.
5. Lead follow-up sees a board pointer, not worker transcripts.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
_HTTP = _ROOT / 'webui' / 'backend'
for _p in (str(_ROOT), str(_HTTP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'

from ms_agent.team.context import ContextBundleAssembler  # noqa: E402
from team import router as team_router  # noqa: E402
from team.state import get_team_state, reset_team_state_for_tests  # noqa: E402

PID = 'proj_beijing_day'


def _client() -> TestClient:
    reset_team_state_for_tests()
    os.environ['MS_AGENT_TEAM_PERSIST'] = '0'
    os.environ['MS_AGENT_TEAM_CLOUD_DRY_RUN'] = '1'
    app = FastAPI()
    app.include_router(team_router)
    return TestClient(app)


def _register(client: TestClient, at_name: str, eid: str) -> None:
    r = client.post(
        '/api/v1/team/endpoints',
        json={
            'endpoint_id': eid,
            'at_name': at_name,
            'owner_user_id': 'u1',
            'endpoint_type': 'persistent',
            'runtime': 'ms_agent',
            'adapter_kind': 'cloud',
            'status': 'online',
        },
    )
    assert r.status_code == 200, r.text


def _write_task(client: TestClient, **body):
    r = client.post(f'/api/v1/team/projects/{PID}/tasks', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _board(client: TestClient) -> dict[str, dict]:
    rows = client.get(f'/api/v1/team/projects/{PID}/tasks').json()['tasks']
    return {t['task_id']: t for t in rows}


def _run_board(client: TestClient) -> dict:
    r = client.post(f'/api/v1/team/projects/{PID}/coordinator/run')
    assert r.status_code == 200, r.text
    return r.json()


def _send(client: TestClient, content: str) -> dict:
    r = client.post(
        f'/api/v1/team/projects/{PID}/messages',
        params={'wait': 'true', 'wait_timeout': 15},
        json={
            'content': content,
            'sender_user_id': 'u1',
            'channel': 'web',
            'thread_id': 'web-main',
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_status(client: TestClient, task_id: str, want: set[str],
                 timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _board(client).get(task_id) or {}
        if last.get('status') in want:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f'{task_id} status={last.get("status")!r} not in {want}')


@pytest.fixture
def api():
    client = _client()
    try:
        proj = client.post(
            '/api/v1/team/projects',
            json={
                'project_id': PID,
                'name': 'beijing-day-trip',
                'default_lead_at': 'ms_agent',
            },
        )
        assert proj.status_code == 200, proj.text
        _register(client, 'ms_agent', 'ep-lead')
        _register(client, 'bibo', 'ep-bibo')
        _register(client, 'lily', 'ep-lily')
        yield client
    finally:
        reset_team_state_for_tests()


def test_beijing_day_trip_decompose_dispatch_merge(api: TestClient):
    """Lead splits a day-trip; Coordinator parallelizes then gates the summary."""
    # 1) Decompose lands on the board (not only in Lead chat).
    _write_task(
        api,
        task_id='t_root',
        status='pending',
        prompt='规划北京一日游：美食 + 美景，最后汇总成一条路线',
        trigger_user_id='u1',
    )
    _write_task(
        api,
        task_id='t_food',
        status='pending',
        prompt='列出 3 家北京美食（店名 + 一句理由）',
        trigger_user_id='u1',
        target_at_name='bibo',
        parent_task_id='t_root',
    )
    _write_task(
        api,
        task_id='t_view',
        status='pending',
        prompt='列出 3 处北京美景（景点 + 一句理由）',
        trigger_user_id='u1',
        target_at_name='lily',
        parent_task_id='t_root',
    )
    _write_task(
        api,
        task_id='t_plan',
        status='pending',
        prompt='根据美食和美景结果，汇总一条一日路线',
        trigger_user_id='u1',
        target_at_name='ms_agent',
        parent_task_id='t_root',
        blocked_by=['t_food', 't_view'],
    )

    # 2) First 按板执行: unblocked workers only.
    first = _run_board(api)
    dispatched = {d['task_id'] for d in first['dispatched']}
    skipped = {s['task_id']: s['reason'] for s in first['skipped']}
    assert dispatched == {'t_food', 't_view'}
    assert skipped.get('t_plan') == 'blocked'
    assert skipped.get('t_root') == 'unclaimed'

    food = _wait_status(api, 't_food', {'completed', 'in_progress'})
    view = _wait_status(api, 't_view', {'completed', 'in_progress'})
    assert food['target_at_name'] == 'bibo'
    assert view['target_at_name'] == 'lily'
    # First wave did not dispatch t_plan (asserted via skipped). Auto-kick
    # after workers complete may already have released it.

    _wait_status(api, 't_food', {'completed'})
    _wait_status(api, 't_view', {'completed'})

    # 3) Second tick: merge is unblocked. Auto-kick may have done it if
    # coordinator.enabled; 按板执行 is the guaranteed product path.
    second = _run_board(api)
    released = {d['task_id'] for d in second['dispatched']}
    board = _board(api)
    if 't_plan' not in released:
        assert board['t_plan']['status'] in ('in_progress', 'completed')
    _wait_status(api, 't_plan', {'completed', 'in_progress'})

    # 4) Lead asks progress: pointer + tools, no worker transcript dump.
    follow = _send(api, '做完了吗？路线出来没有？')
    names = [d['target_at_name'] for d in follow['dispatches']]
    assert names == ['ms_agent']
    env = get_team_state().ingress.get_envelope(
        follow['dispatches'][0]['dispatch_id'])
    merged = ContextBundleAssembler.merge_prompt(
        env.prompt, env.context_bundle)
    assert '[task_board]' in merged
    assert 'task_board_read' in merged
    assert 'dispatch_result_read' in merged
    assert '列出 3 家北京美食' not in merged
    assert '列出 3 处北京美景' not in merged
    assert 'dry-run complete' not in merged
    assert env.context_bundle.audience == 'lead'


def test_failed_blocker_does_not_release_merge(api: TestClient):
    _write_task(
        api, task_id='t_food', status='failed', prompt='美食',
        trigger_user_id='u1', target_at_name='bibo')
    _write_task(
        api, task_id='t_view', status='completed', prompt='美景',
        trigger_user_id='u1', target_at_name='lily')
    _write_task(
        api, task_id='t_plan', status='pending', prompt='汇总',
        trigger_user_id='u1', target_at_name='ms_agent',
        blocked_by=['t_food', 't_view'])
    out = _run_board(api)
    assert {d['task_id'] for d in out['dispatched']} == set()
    reasons = {s['task_id']: s['reason'] for s in out['skipped']}
    assert reasons['t_plan'] == 'blocked'
    assert _board(api)['t_plan']['status'] == 'pending'
