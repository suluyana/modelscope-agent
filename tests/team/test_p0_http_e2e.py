# Copyright (c) ModelScope Contributors. All rights reserved.
"""P0 control-plane HTTP A/B/C: mention routing, receipts, lead snapshots."""
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

from ms_agent.team.context import ContextBundleAssembler
from team import router as team_router
from team.state import get_team_state, reset_team_state_for_tests


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


def _send(client: TestClient, project_id: str, content: str, **extra):
    r = client.post(
        f'/api/v1/team/projects/{project_id}/messages',
        params={'wait': 'true', 'wait_timeout': 15},
        json={
            'content': content,
            'sender_user_id': 'u1',
            'channel': 'web',
            'thread_id': 'web-main',
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _merged(dispatch_id: str) -> str:
    env = get_team_state().ingress.get_envelope(dispatch_id)
    assert env is not None
    return ContextBundleAssembler.merge_prompt(env.prompt, env.context_bundle)


@pytest.fixture
def api():
    client = _client()
    try:
        proj = client.post(
            '/api/v1/team/projects',
            json={
                'project_id': 'proj_p0_http',
                'name': 'p0-http-e2e',
                'default_lead_at': 'planner',
            },
        )
        assert proj.status_code == 200, proj.text
        _register(client, 'planner', 'ep-lead')
        _register(client, 'codex', 'ep-codex')
        _register(client, 'lily', 'ep-lily')
        yield client
    finally:
        reset_team_state_for_tests()


def test_p0_a_mention_only_dispatches_target(api: TestClient):
    body = _send(api, 'proj_p0_http', '@codex 你找北京美景')
    names = [d['target_at_name'] for d in body['dispatches']]
    assert names == ['codex']
    assert body['receipts']
    assert body['receipts'][0]['text'].startswith('已派 @codex')
    replies = body.get('replies') or []
    assert len(replies) == 1
    assert replies[0]['at_name'] == 'codex'
    assert replies[0]['ok'] is True
    merged = _merged(body['dispatches'][0]['dispatch_id'])
    assert 'project_timeline' not in merged
    assert '无法调用其他 agent' not in merged

    timeline = api.get('/api/v1/team/projects/proj_p0_http/timeline').json()
    texts = [m['content'] for m in timeline['messages']]
    assert any(t.startswith('已派 @codex') for t in texts)
    assert any('@codex 已结束执行' in t for t in texts)
    assert not any('处理完毕' in t for t in texts)
    assert not any('dry-run complete' in t for t in texts)

    tasks = api.get('/api/v1/team/projects/proj_p0_http/tasks').json()['tasks']
    assert len(tasks) == 1
    assert tasks[0]['target_at_name'] == 'codex'
    assert tasks[0]['status'] == 'completed'
    assert not tasks[0].get('result_summary')


def test_p0_b_two_mentions_two_dispatches(api: TestClient):
    body = _send(api, 'proj_p0_http', '@codex 写A @lily 写B')
    names = [d['target_at_name'] for d in body['dispatches']]
    assert names == ['codex', 'lily']
    sessions = {d['runtime_session_id'] for d in body['dispatches']}
    assert len(sessions) == 2
    assert {r['at_name'] for r in body['receipts']} == {'codex', 'lily'}
    replies = {r['at_name']: r for r in body.get('replies') or []}
    assert replies['codex']['ok'] and replies['lily']['ok']
    # Streams stay keyed by dispatch_id — not a single merged assistant buffer.
    ids = [d['dispatch_id'] for d in body['dispatches']]
    assert ids[0] != ids[1]
    prompts = {d['target_at_name']: d.get('prompt') or '' for d in body['dispatches']}
    assert '写A' in prompts['codex']
    assert '写B' not in prompts['codex']
    assert '写B' in prompts['lily']
    assert '写A' not in prompts['lily']


def test_p0_c_followup_goes_to_lead_with_snapshots_only(api: TestClient):
    a = _send(api, 'proj_p0_http', '@codex 你找北京美景')
    assert [d['target_at_name'] for d in a['dispatches']] == ['codex']
    follow = _send(api, 'proj_p0_http', '做完了吗')
    names = [d['target_at_name'] for d in follow['dispatches']]
    assert names == ['planner']
    merged = _merged(follow['dispatches'][0]['dispatch_id'])
    assert '[task_board]' in merged
    assert 'task_board_read' in merged
    assert 'Do not re-run completed tasks' in merged
    assert '找北京美景' not in merged
    assert 'completed @codex' not in merged
    assert 'dispatch_result_read' in merged
    assert 'dry-run complete' not in merged
    assert '无法调用其他 agent' not in merged
    assert '[project_timeline]' not in merged
    env = get_team_state().ingress.get_envelope(
        follow['dispatches'][0]['dispatch_id'])
    assert env.context_bundle.audience == 'lead'
    assert env.context_bundle.project_timeline == []


def test_p0_attribution_mismatch_is_emitted(api: TestClient):
    import asyncio

    from ms_agent.team.events import TeamEvent, reconcile_event_attribution

    body = _send(api, 'proj_p0_http', '@codex 你找北京美景')
    did = body['dispatches'][0]['dispatch_id']
    ev = TeamEvent(
        type='team.stream',
        dispatch_id=did,
        at_name='intruder',
        payload={'type': 'text', 'content': 'leaked-from-other-card'},
    )
    mismatch = reconcile_event_attribution(ev, 'codex')
    assert mismatch is not None
    assert mismatch.type == 'team.attribution_mismatch'
    assert mismatch.payload['event_at_name'] == 'intruder'
    assert mismatch.payload['card_at_name'] == 'codex'
    assert ev.at_name == 'codex'

    leaked = TeamEvent(
        type='team.stream',
        dispatch_id=did,
        at_name='intruder',
        payload={'type': 'text', 'content': 'leaked-from-other-card'},
    )
    asyncio.run(get_team_state()._fanout_event(leaked))
    events = get_team_state().dispatch_log.list(did)
    types = {e.type for e in events}
    assert 'team.attribution_mismatch' in types
    mismatch_ev = next(e for e in events if e.type == 'team.attribution_mismatch')
    assert mismatch_ev.payload.get('event_at_name') == 'intruder'
    assert mismatch_ev.at_name == 'codex'
