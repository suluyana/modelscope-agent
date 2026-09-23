"""C-15 plan gate: first pass is read-only, human approve then write."""
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

from ms_agent.team.models import TeamProjectMeta, TeamTask
from ms_agent.team.plan_gate import (
    apply_plan_prefix,
    project_wants_plan,
    task_needs_plan_pass,
)
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


def test_plan_helpers():
    proj = TeamProjectMeta(project_id='p', name='p', plan_gate=True)
    task = TeamTask(
        task_id='t1', project_id='p', status='pending', prompt='x',
        trigger_user_id='u', plan_status='required')
    assert project_wants_plan(proj)
    assert task_needs_plan_pass(task, proj)
    task.plan_status = 'approved'
    assert not task_needs_plan_pass(task, proj)
    assert apply_plan_prefix('do it').startswith('[plan_gate]')


def test_approve_plan_api(api: TestClient):
    proj = api.post('/api/v1/team/projects', json={'name': 'pg', 'plan_gate': True}).json()
    pid = proj['project_id']
    assert proj['plan_gate'] is True
    task = api.post(
        f'/api/v1/team/projects/{pid}/tasks',
        json={
            'status': 'wait_human',
            'prompt': 'change login',
            'target_at_name': 'coder',
            'plan_status': 'ready',
        },
    ).json()
    tid = task['task_id']
    out = api.post(f'/api/v1/team/projects/{pid}/tasks/{tid}/approve-plan').json()
    assert out['ok'] is True
    assert out['task']['plan_status'] == 'approved'
    assert out['task']['status'] == 'pending'


def test_artifacts_list_and_task_slots(api: TestClient):
    proj = api.post('/api/v1/team/projects', json={'name': 'arts'}).json()
    pid = proj['project_id']
    art = api.post(
        '/api/v1/team/artifacts',
        json={
            'project_id': pid,
            'filename': 'out.diff',
            'content_base64': '',
        },
    ).json()
    listed = api.get(f'/api/v1/team/artifacts?project_id={pid}').json()
    assert any(a['artifact_id'] == art['artifact_id'] for a in listed['artifacts'])
    task = api.post(
        f'/api/v1/team/projects/{pid}/tasks',
        json={
            'status': 'completed',
            'prompt': 'ship it',
            'output_artifacts': [art['artifact_id'], 'https://example.com/pr/1'],
        },
    ).json()
    assert art['artifact_id'] in task['output_artifacts']
    assert 'https://example.com/pr/1' in task['output_artifacts']
